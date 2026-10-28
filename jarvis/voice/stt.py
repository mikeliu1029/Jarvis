"""Local speech-to-text with faster-whisper."""
from __future__ import annotations

import logging
import os
import re

import numpy as np

from ..config import MODELS_DIR, Settings

log = logging.getLogger(__name__)
os.environ.setdefault("HF_HUB_DISABLE_SYMLINKS_WARNING", "1")
os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

# Whisper "hears" these in near-silence; ignore them when the audio was quiet/short.
HALLUCINATIONS = {"thank you", "thanks for watching", "thank you for watching", "you", "bye", "okay", "so",
                  "subtitles by the amara org community", "please subscribe", "uh", "um", "hmm"}
WAKE_PREFIX = re.compile(r"^\s*((hey|hi|okay|ok|a)[,\s]+)?(jarvis|jervis|travis|javis|jarvas)\b[\s,.!?]*", re.I)


class SpeechToText:
    def __init__(self, settings: Settings):
        from faster_whisper import WhisperModel
        self.model_name = str(settings.get("voice.stt_model", "small.en"))
        device = str(settings.get("voice.stt_device", "cpu"))
        compute = "int8" if device == "cpu" else "float16"
        self.model = WhisperModel(self.model_name, device=device, compute_type=compute,
                                  download_root=str(MODELS_DIR / "whisper"),
                                  cpu_threads=min(8, os.cpu_count() or 4))
        self.prompt = "Jarvis, open Spotify. Search YouTube. What's the weather? Daily briefing."

    def transcribe(self, audio: np.ndarray) -> str:
        """audio: float32 mono at 16 kHz."""
        if audio.size < 16000 * 0.3:
            return ""
        segments, _info = self.model.transcribe(audio, language="en", beam_size=3, vad_filter=False,
                                                condition_on_previous_text=False, initial_prompt=self.prompt,
                                                without_timestamps=True)
        text = " ".join(s.text.strip() for s in segments if s.no_speech_prob < 0.8).strip()
        bare = re.sub(r"[^a-z ]", "", text.lower()).strip()
        if bare in HALLUCINATIONS and (audio.size < 16000 * 1.5 or float(np.abs(audio).mean()) < 0.01):
            return ""
        return text


NAME = re.compile(r"\b(jarvis|jervis|javis|jarvas|jarves|jarbis|jarviss)\b", re.I)
_TRAILING_NAME = re.compile(r"[,\s]+(jarvis|jervis|javis|jarvas|jarves|jarbis)\s*[.!?]*\s*$", re.I)


def strip_wake_words(text: str) -> str:
    text = WAKE_PREFIX.sub("", text).strip()
    return _TRAILING_NAME.sub("", text).strip(" ,")


def mentions_name(text: str) -> bool:
    return bool(NAME.search(text or ""))
