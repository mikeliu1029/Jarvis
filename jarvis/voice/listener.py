"""The ears.

Two ways in, both always running while the mic is on (F4 / the HUD button mutes it):

  * Always listening (default): any speech is recorded and transcribed. If it contains
    "Jarvis" ("Jarvis, boot up Cyberpunk", "what's the weather, Jarvis?") it's a command.
    For a short while after Jarvis replies, you can carry on without saying his name.
  * "Hey Jarvis" wake word: an instant chime, and it also interrupts him mid-sentence.

Set voice.listen_mode = "wake_word" to go back to wake-word-only listening.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import TYPE_CHECKING

import numpy as np

from ..config import Settings
from ..events import bus
from . import audio
from .stt import SpeechToText, mentions_name, strip_wake_words
from .wakeword import VoiceActivity, WakeWord

if TYPE_CHECKING:
    from ..assistant import Jarvis

log = logging.getLogger(__name__)


class VoiceListener(threading.Thread):
    def __init__(self, jarvis: "Jarvis", settings: Settings):
        super().__init__(name="voice-listener", daemon=True)
        self.jarvis = jarvis
        self.settings = settings
        self.always = str(settings.get("voice.listen_mode", "always")).lower() != "wake_word"
        self.require_name = bool(settings.get("voice.require_name", True))
        self.conversation_seconds = float(settings.get("voice.conversation_seconds", 20))
        self.end_silence_ms = int(settings.get("voice.end_silence_ms", 800))
        self._listen_now = threading.Event()
        self._conversation_until = 0.0
        self._quiet_until = 0.0
        self.ready = threading.Event()
        self.error: str | None = None
        self.mic: audio.Microphone | None = None
        self.device_label = "none"

    # -- called from other threads -----------------------------------------------
    def request_listen(self) -> None:
        """Push-to-talk / HUD talk button: take the next thing said as a command."""
        self._listen_now.set()

    def open_follow_up(self, seconds: float | None = None) -> None:
        """After a reply, the next thing said counts as addressed to Jarvis even without his name."""
        if self.mic:
            self.mic.flush()  # don't hear the tail of our own voice
        self._quiet_until = time.time() + 0.6
        self._conversation_until = time.time() + (seconds or self.conversation_seconds)

    def in_conversation(self) -> bool:
        return time.time() < self._conversation_until

    # -- main loop -------------------------------------------------------------------
    def run(self) -> None:
        try:
            self.stt = SpeechToText(self.settings)
            self.wake = WakeWord(str(self.settings.get("voice.wake_word", "hey_jarvis")),
                                 float(self.settings.get("voice.wake_threshold", 0.5)))
            self.vad = VoiceActivity()
            device = audio.find_input_device(str(self.settings.get("voice.input_device", "")))
            self.device_label = audio.device_name(device, "input")
            self.mic = audio.Microphone(device)
            self.mic.start()
        except Exception as e:
            self.error = f"{type(e).__name__}: {e}"
            log.exception("voice input failed to start")
            bus.emit("notice", level="warning", text=f"Voice input is off ({self.error}). Typing still works.")
            self.ready.set()
            return
        self.ready.set()
        preroll: deque[np.ndarray] = deque(maxlen=6)  # ~0.5 s so the first word isn't clipped
        speech_frames = 0
        while not self.jarvis.stopping.is_set():
            frame = self.mic.read(0.5)
            if frame is None:
                continue
            audio.set_mic_level(frame)
            if self.jarvis.mic_muted:
                self._listen_now.clear()
                preroll.clear()
                continue

            mode = None
            speaking = self.jarvis.speaker.speaking
            if speaking:
                self._quiet_until = time.time() + 0.6  # ignore our own voice's tail as well
            if self._listen_now.is_set():
                self._listen_now.clear()
                mode = "manual"
            elif self.wake.score(frame) >= self.wake.threshold:
                mode = "wake"  # works even while Jarvis is talking - that's how you interrupt him
            elif not speaking and time.time() > self._quiet_until and (self.always or self.in_conversation()):
                speech_frames = speech_frames + 1 if self.vad.is_speech(frame) else 0
                if speech_frames >= 2:
                    mode = "speech"
            preroll.append(frame)
            if mode is None:
                continue
            speech_frames = 0
            self._handle(mode, list(preroll))
            self.wake.reset()
            preroll.clear()
        self.mic.stop()

    def _handle(self, mode: str, preroll: list[np.ndarray]) -> None:
        addressed = mode in ("wake", "manual") or self.in_conversation() or not self.require_name
        self.jarvis.on_listen_start(mode)
        captured = self._capture(preroll if mode == "speech" else [], wait=6.0 if mode != "speech" else 1.0)
        if captured is None:
            self.jarvis.on_listen_end(heard=False, mode=mode)
            return
        raw = self.stt.transcribe(captured)
        named = mentions_name(raw)
        text = strip_wake_words(raw)
        if mode == "speech" and not (addressed or named):
            log.debug("heard (not for me): %s", raw)
            self.jarvis.on_listen_end(heard=False, mode="ignored")
            return
        if named and not text:  # just "Jarvis?" - answer and wait for the request
            self.jarvis.on_listen_end(heard=True, mode=mode)
            self.jarvis.on_name_only()
            return
        self.jarvis.on_listen_end(heard=bool(text), mode=mode)
        if text:
            self.jarvis.submit(text, "voice")

    def _capture(self, initial: list[np.ndarray], wait: float) -> np.ndarray | None:
        frames = list(initial)
        started = bool(initial)
        silence = 0
        t0 = time.time()
        self.vad.reset()
        while not self.jarvis.stopping.is_set():
            frame = self.mic.read(0.5)
            if frame is None:
                continue
            frames.append(frame)
            audio.set_mic_level(frame)
            if self.vad.is_speech(frame):
                started, silence = True, 0
            elif started:
                silence += 80
            if started and silence >= self.end_silence_ms:
                break
            if not started and time.time() - t0 > wait:
                return None
            if time.time() - t0 > 25:
                break
        if not frames:
            return None
        return np.concatenate(frames).astype(np.float32) / 32768.0
