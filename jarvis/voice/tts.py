"""Text-to-speech with a British voice. Pluggable engines:

  kokoro      local neural TTS (default) - British male voices bm_george, bm_fable, bm_lewis, bm_daniel
  edge        Microsoft neural voices, free but online (en-GB-RyanNeural, en-GB-ThomasNeural)
  elevenlabs  cloud, needs ELEVENLABS_API_KEY (+ ELEVENLABS_VOICE_ID) in .env
  fishaudio   cloud, needs FISH_AUDIO_API_KEY (+ FISH_AUDIO_VOICE_ID) in .env
  sapi        Windows' built-in voices (last-resort fallback)
"""
from __future__ import annotations

import asyncio
import io
import logging
import queue
import re
import threading
from pathlib import Path

import numpy as np
import requests
import soundfile as sf

from ..config import MODELS_DIR, Settings, secret
from . import audio

log = logging.getLogger(__name__)

KOKORO_DIR = MODELS_DIR / "kokoro"
KOKORO_MODEL = KOKORO_DIR / "kokoro-v1.0.onnx"
KOKORO_VOICES = KOKORO_DIR / "voices-v1.0.bin"


# Narrow emoji ranges only - a wide range like U+24C2..U+1F251 also spans all of CJK.
_EMOJI = re.compile("[\U0001F300-\U0001FAFF\U0001F1E6-\U0001F1FF☀-➿️‍]+")


def clean_for_speech(text: str) -> str:
    text = _EMOJI.sub("", text)  # otherwise "😊" is read out as "smiling face with smiling eyes"
    text = re.sub(r"```.*?```", " (code is on screen) ", text, flags=re.S)
    text = re.sub(r"https?://\S+", "the link", text)
    text = re.sub(r"[*_#`>|]+", "", text)
    text = re.sub(r"^\s*[-•]\s+", "", text, flags=re.M)
    for a, b in (("°F", " degrees Fahrenheit"), ("°C", " degrees Celsius"), ("°", " degrees"),
                 ("%", " percent"), (" & ", " and "), ("e.g.", "for example"), ("i.e.", "that is"),
                 (" vs. ", " versus "), ("->", " to ")):
        text = text.replace(a, b)
    return re.sub(r"\s+", " ", text).strip()


def split_sentences(text: str, max_len: int = 240) -> list[str]:
    parts = re.split(r"(?<=[.!?;:])\s+", text)
    chunks, cur = [], ""
    for p in parts:
        if len(cur) + len(p) < 60 or (cur and len(cur) + len(p) < max_len and len(cur) < 40):
            cur = f"{cur} {p}".strip()
        else:
            if cur:
                chunks.append(cur)
            cur = p
    if cur:
        chunks.append(cur)
    return [c for c in chunks if c.strip()]


# ---- engines -----------------------------------------------------------------
class KokoroEngine:
    name = "kokoro"

    def __init__(self, settings: Settings):
        from kokoro_onnx import Kokoro
        if not KOKORO_MODEL.exists() or not KOKORO_VOICES.exists():
            raise FileNotFoundError("Kokoro voice model not downloaded (run: python -m jarvis --setup)")
        self.kokoro = Kokoro(str(KOKORO_MODEL), str(KOKORO_VOICES))
        self.speed = float(settings.get("tts.kokoro_speed", 1.0))
        self.voice = self._voice(str(settings.get("tts.kokoro_voice", "bm_george")))

    def _voice(self, spec: str):
        """'bm_george' or a blend like 'bm_george:0.7,bm_fable:0.3'."""
        if ":" not in spec:
            return spec.strip()
        mix = None
        for part in spec.split(","):
            name, _, weight = part.partition(":")
            style = self.kokoro.get_voice_style(name.strip()) * float(weight or 1)
            mix = style if mix is None else mix + style
        return mix

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        samples, sr = self.kokoro.create(text, voice=self.voice, speed=self.speed, lang="en-gb")
        return samples.astype(np.float32), sr


class EdgeEngine:
    name = "edge"

    def __init__(self, settings: Settings):
        import edge_tts  # noqa: F401
        self.voice = str(settings.get("tts.edge_voice", "en-GB-RyanNeural"))
        self.rate = str(settings.get("tts.edge_rate", "+0%"))

    async def _fetch(self, text: str) -> bytes:
        import edge_tts
        out = bytearray()
        async for chunk in edge_tts.Communicate(text, self.voice, rate=self.rate).stream():
            if chunk.get("type") == "audio":
                out.extend(chunk["data"])
        return bytes(out)

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        data, sr = sf.read(io.BytesIO(asyncio.run(self._fetch(text))), dtype="float32")
        return (data if data.ndim == 1 else data.mean(axis=1)), sr


class ElevenLabsEngine:
    name = "elevenlabs"

    def __init__(self, settings: Settings):
        self.key = secret("ELEVENLABS_API_KEY")
        if not self.key:
            raise ValueError("ELEVENLABS_API_KEY missing from .env")
        self.voice = secret("ELEVENLABS_VOICE_ID") or "onwK4e9ZLuTAKqWW03F9"  # "Daniel" - British
        self.model = secret("ELEVENLABS_MODEL") or "eleven_flash_v2_5"

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        r = requests.post(f"https://api.elevenlabs.io/v1/text-to-speech/{self.voice}",
                          params={"output_format": "pcm_24000"},
                          headers={"xi-api-key": self.key, "Content-Type": "application/json"},
                          json={"text": text, "model_id": self.model}, timeout=30)
        r.raise_for_status()
        return np.frombuffer(r.content, dtype=np.int16).astype(np.float32) / 32768, 24000


class FishAudioEngine:
    name = "fishaudio"

    def __init__(self, settings: Settings):
        self.key = secret("FISH_AUDIO_API_KEY")
        if not self.key:
            raise ValueError("FISH_AUDIO_API_KEY missing from .env")
        self.voice = secret("FISH_AUDIO_VOICE_ID")
        self.model = secret("FISH_AUDIO_MODEL") or "s1"

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        body = {"text": text, "format": "wav", "sample_rate": 24000, "latency": "balanced", "normalize": True}
        if self.voice:
            body["reference_id"] = self.voice
        r = requests.post("https://api.fish.audio/v1/tts", json=body, timeout=45,
                          headers={"Authorization": f"Bearer {self.key}", "model": self.model})
        r.raise_for_status()
        data, sr = sf.read(io.BytesIO(r.content), dtype="float32")
        return (data if data.ndim == 1 else data.mean(axis=1)), sr


class SapiEngine:
    name = "sapi"

    def __init__(self, settings: Settings):
        self.hint = str(settings.get("tts.sapi_voice", "George")).lower()
        self._local = threading.local()

    def _voice(self):
        if not hasattr(self._local, "voice"):
            from ..winutil import ensure_com
            ensure_com()
            import comtypes.client
            v = comtypes.client.CreateObject("SAPI.SpVoice")
            for token in v.GetVoices():
                if self.hint and self.hint in token.GetDescription().lower():
                    v.Voice = token
                    break
            self._local.voice = v
        return self._local.voice

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        import comtypes.client
        import tempfile
        voice = self._voice()
        path = Path(tempfile.gettempdir()) / f"jarvis_sapi_{threading.get_ident()}.wav"
        stream = comtypes.client.CreateObject("SAPI.SpFileStream")
        stream.Open(str(path), 3)
        voice.AudioOutputStream = stream
        voice.Speak(text)
        stream.Close()
        data, sr = sf.read(str(path), dtype="float32")
        return (data if data.ndim == 1 else data.mean(axis=1)), sr


ENGINES = {"kokoro": KokoroEngine, "edge": EdgeEngine, "elevenlabs": ElevenLabsEngine,
           "fishaudio": FishAudioEngine, "sapi": SapiEngine}


def make_engine(settings: Settings):
    wanted = str(settings.get("tts.engine", "kokoro")).lower()
    for name in dict.fromkeys([wanted, "kokoro", "edge", "sapi"]):
        try:
            engine = ENGINES[name](settings)
            if name != wanted:
                log.warning("TTS engine '%s' unavailable - using '%s' instead", wanted, name)
            return engine
        except Exception as e:
            log.warning("TTS engine %s failed to start: %s", name, e)
    raise RuntimeError("no text-to-speech engine could start")


class Speaker:
    """Speaks sentence by sentence: the next sentence is synthesised while the current one plays."""

    def __init__(self, settings: Settings):
        self.engine = make_engine(settings)
        hint = str(settings.get("voice.output_device", "") or "")
        audio.output_device = audio.find_output_device(hint)
        if hint and audio.output_device is None:
            log.warning("output device '%s' not found - using the Windows default", hint)
        self.device_label = audio.device_name(audio.output_device, "output")
        self.stop_event = threading.Event()
        self.speaking = False
        self._lock = threading.Lock()

    @property
    def engine_name(self) -> str:
        return self.engine.name

    def synth(self, text: str) -> tuple[np.ndarray, int]:
        return self.engine.synth(text)

    def speak(self, text: str) -> bool:
        text = clean_for_speech(text)
        if not text:
            return True
        chunks = split_sentences(text)
        clips: queue.Queue = queue.Queue(maxsize=3)
        with self._lock:
            self.stop_event.clear()
            self.speaking = True

            def produce():
                for chunk in chunks:
                    if self.stop_event.is_set():
                        break
                    try:
                        clips.put(self.engine.synth(chunk))
                    except Exception:
                        log.exception("speech synthesis failed")
                        break
                clips.put(None)

            threading.Thread(target=produce, daemon=True).start()
            finished = True
            try:
                while True:
                    item = clips.get(timeout=60)
                    if item is None or self.stop_event.is_set():
                        finished = item is None and not self.stop_event.is_set()
                        break
                    if not audio.play(item[0], item[1], self.stop_event):
                        finished = False
                        break
            except queue.Empty:
                finished = False
            finally:
                self.speaking = False
            return finished

    def stop(self) -> None:
        self.stop_event.set()


class SilentSpeaker:
    engine_name = "muted"
    device_label = "none"
    speaking = False

    def speak(self, text: str) -> bool:
        return True

    def stop(self) -> None:
        pass
