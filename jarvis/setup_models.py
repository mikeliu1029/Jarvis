"""Download the local voice models: Kokoro (British TTS), Whisper (speech-to-text), openWakeWord."""
from __future__ import annotations

import sys
from pathlib import Path

import requests

from .config import MODELS_DIR, get_settings

KOKORO_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"


def _download(url: str, dest: Path) -> None:
    if dest.exists() and dest.stat().st_size > 0:
        print(f"  [ok] {dest.name} already downloaded")
        return
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(dest.suffix + ".part")
    with requests.get(url, stream=True, timeout=60) as r:
        r.raise_for_status()
        total = int(r.headers.get("content-length", 0))
        done, shown = 0, -10
        with open(tmp, "wb") as f:
            for chunk in r.iter_content(1 << 20):
                f.write(chunk)
                done += len(chunk)
                pct = done * 100 // total if total else 0
                if total and pct >= shown + 10:
                    shown = pct
                    print(f"\r  downloading {dest.name}: {pct}% of {total >> 20} MB", end="", flush=True)
    tmp.replace(dest)
    print(f"\r  [ok] {dest.name} ({dest.stat().st_size >> 20} MB)               ")


def main() -> int:
    settings = get_settings()
    ok = True
    print("Voice (Kokoro, British TTS):")
    try:
        _download(KOKORO_BASE + "kokoro-v1.0.onnx", MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx")
        _download(KOKORO_BASE + "voices-v1.0.bin", MODELS_DIR / "kokoro" / "voices-v1.0.bin")
    except Exception as e:
        ok = False
        print(f"  [fail] {e}")

    print("Speech recognition (faster-whisper):")
    try:
        from .voice.stt import SpeechToText
        SpeechToText(settings)
        print(f"  [ok] whisper '{settings.get('voice.stt_model', 'small.en')}' ready")
    except Exception as e:
        ok = False
        print(f"  [fail] {e}")

    print("Wake word (openWakeWord 'hey jarvis'):")
    try:
        from .voice.wakeword import ensure_models
        ensure_models(str(settings.get("voice.wake_word", "hey_jarvis")))
        print("  [ok] wake word + voice activity models ready")
    except Exception as e:
        ok = False
        print(f"  [fail] {e}")

    print("Windows app index:")
    try:
        from .tools.apps import start_apps
        print(f"  [ok] {len(start_apps(refresh=True))} apps indexed")
    except Exception as e:
        print(f"  [warn] {e}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
