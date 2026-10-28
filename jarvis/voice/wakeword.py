"""'Hey Jarvis' wake word (openWakeWord, runs locally on CPU) and Silero voice-activity detection."""
from __future__ import annotations

import logging
import os

import numpy as np

log = logging.getLogger(__name__)


def ensure_models(name: str = "hey_jarvis") -> None:
    import openwakeword
    from openwakeword.utils import download_models
    path = openwakeword.MODELS[name]["model_path"].replace(".tflite", ".onnx")
    vad = openwakeword.VAD_MODELS["silero_vad"]["model_path"]
    if not os.path.exists(path) or not os.path.exists(vad):
        download_models(model_names=[name])


class WakeWord:
    def __init__(self, name: str = "hey_jarvis", threshold: float = 0.5):
        import openwakeword
        from openwakeword.model import Model
        ensure_models(name)
        path = openwakeword.MODELS[name]["model_path"].replace(".tflite", ".onnx")
        self.model = Model(wakeword_models=[path], inference_framework="onnx")
        self.threshold = threshold

    def score(self, frame: np.ndarray) -> float:
        preds = self.model.predict(frame)
        return max(preds.values()) if preds else 0.0

    def detected(self, frame: np.ndarray) -> bool:
        return self.score(frame) >= self.threshold

    def reset(self) -> None:
        self.model.reset()


class VoiceActivity:
    """Speech probability per 80 ms frame (Silero VAD); falls back to a loudness check."""

    def __init__(self):
        self.vad = None
        try:
            from openwakeword.vad import VAD
            ensure_models()
            self.vad = VAD()
        except Exception as e:
            log.warning("Silero VAD unavailable, using a loudness check instead: %s", e)
        self.noise = 150.0

    def is_speech(self, frame: np.ndarray) -> bool:
        rms = float(np.sqrt(np.mean(frame.astype(np.float32) ** 2)))
        if self.vad is not None:
            try:
                return float(self.vad.predict(frame, frame_size=640)) > 0.5
            except Exception:
                self.vad = None
        if rms < self.noise * 2:
            self.noise = 0.97 * self.noise + 0.03 * rms
        return rms > max(self.noise * 3.0, 300)

    def reset(self) -> None:
        if self.vad is not None:
            try:
                self.vad.reset_states()
            except Exception:
                pass
