"""Microphone capture, playback and the little activation chimes."""
from __future__ import annotations

import queue
import threading
import time
from collections import deque

import numpy as np
import sounddevice as sd

SAMPLE_RATE = 16000
FRAME = 1280  # 80 ms at 16 kHz - what openWakeWord expects


def find_input_device(hint: str) -> int | None:
    if not hint:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_input_channels"] > 0 and hint.lower() in d["name"].lower():
            return i
    return None


def find_output_device(hint: str) -> int | None:
    if not hint:
        return None
    for i, d in enumerate(sd.query_devices()):
        if d["max_output_channels"] > 0 and hint.lower() in d["name"].lower():
            return i
    return None


def device_name(device: int | None, kind: str) -> str:
    try:
        return sd.query_devices(device, kind=kind)["name"] if device is not None else \
            sd.query_devices(kind=kind)["name"]
    except Exception:
        return "unknown"


# Where Jarvis's voice and chimes go (None = the Windows default output).
output_device: int | None = None


class Microphone:
    def __init__(self, device: int | None = None):
        self.device = device
        self.q: queue.Queue[np.ndarray] = queue.Queue(maxsize=500)
        self.stream: sd.InputStream | None = None

    def _callback(self, indata, frames, time_info, status) -> None:
        frame = indata[:, 0].copy()
        try:
            self.q.put_nowait(frame)
        except queue.Full:
            try:
                self.q.get_nowait()
            except queue.Empty:
                pass
            self.q.put_nowait(frame)

    def start(self) -> None:
        self.stream = sd.InputStream(samplerate=SAMPLE_RATE, channels=1, dtype="int16", blocksize=FRAME,
                                     device=self.device, callback=self._callback)
        self.stream.start()

    def read(self, timeout: float = 0.5) -> np.ndarray | None:
        try:
            return self.q.get(timeout=timeout)
        except queue.Empty:
            return None

    def flush(self) -> None:
        while not self.q.empty():
            try:
                self.q.get_nowait()
            except queue.Empty:
                break

    def stop(self) -> None:
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None


def _tone(freq: float, ms: int, sr: int, volume: float = 0.16) -> np.ndarray:
    t = np.linspace(0, ms / 1000, int(sr * ms / 1000), endpoint=False)
    wave = np.sin(2 * np.pi * freq * t) * volume
    fade = min(len(t) // 4, int(sr * 0.012))
    env = np.ones_like(wave)
    env[:fade] = np.linspace(0, 1, fade)
    env[-fade:] = np.linspace(1, 0, fade)
    return (wave * env).astype(np.float32)


def chime(kind: str = "wake", sr: int = 24000) -> tuple[np.ndarray, int]:
    gap = np.zeros(int(sr * 0.02), dtype=np.float32)
    if kind == "wake":
        audio = np.concatenate([_tone(880, 70, sr), gap, _tone(1320, 90, sr)])
    elif kind == "done":
        audio = np.concatenate([_tone(1320, 60, sr), gap, _tone(880, 80, sr)])
    else:
        audio = _tone(660, 90, sr, 0.12)
    return audio, sr


# ---- live levels for the HUD waveform --------------------------------------------
# Jarvis's own voice is published as an envelope (one value per 20 ms) with its start time,
# so the HUD can read the level for "right now" without the audio thread pushing at 60 Hz.
_out: tuple[np.ndarray, float, float] | None = None
_mic: tuple[float, float] = (0.0, 0.0)


def _envelope(audio: np.ndarray, sr: int, hop_s: float = 0.02) -> np.ndarray:
    hop = max(1, int(sr * hop_s))
    n = len(audio) // hop
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    rms = np.sqrt(np.mean(audio[: n * hop].reshape(n, hop) ** 2, axis=1))
    return np.clip((rms - 0.004) / 0.18, 0.0, 1.0).astype(np.float32)


_stream_levels: deque = deque(maxlen=64)  # (start_time, envelope) for streamed speech (Gemini Live)


def queue_output_level(pcm: np.ndarray, sr: int, start: float) -> None:
    """Streamed audio: register the envelope of a chunk that starts sounding at `start`."""
    _stream_levels.append((start, _envelope(pcm.astype(np.float32) / 32768.0, sr)))


def output_level() -> float:
    now = time.time()
    for start, env in reversed(_stream_levels):
        i = int((now - start) / 0.02)
        if 0 <= i < len(env):
            return float(env[i])
    cur = _out
    if cur is None:
        return 0.0
    env, t0, hop = cur
    i = int((now - t0) / hop)
    return float(env[i]) if 0 <= i < len(env) else 0.0


def set_mic_level(frame: np.ndarray) -> None:
    global _mic
    rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
    _mic = (min(1.0, max(0.0, (rms - 60.0) / 2540.0)), time.time())


def mic_level() -> float:
    level, at = _mic
    return level if time.time() - at < 0.3 else 0.0


_play_lock = threading.Lock()


def play(audio: np.ndarray, sr: int, stop: threading.Event | None = None) -> bool:
    """Play a clip; returns False if interrupted by `stop`."""
    global _out
    with _play_lock:
        _out = (_envelope(audio, sr), time.time() + 0.03, 0.02)
        sd.play(audio, sr, device=output_device)
        duration = len(audio) / sr
        start = time.time()
        while time.time() - start < duration + 0.3:
            if stop is not None and stop.is_set():
                sd.stop()
                return False
            stream = sd.get_stream()
            if stream is None or not stream.active:
                break
            time.sleep(0.03)
        sd.stop()
        return True


def play_async(audio: np.ndarray, sr: int) -> None:
    threading.Thread(target=play, args=(audio, sr), daemon=True).start()
