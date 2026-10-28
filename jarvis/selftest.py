"""python -m jarvis --selftest : checks every subsystem without touching your screen."""
from __future__ import annotations

import time
import traceback

from rich.console import Console
from rich.table import Table

from .config import get_settings


def _checks():
    s = get_settings()

    def settings_():
        return f"provider={s.get('brain.provider')}, tts={s.get('tts.engine')}"

    def tools():
        from .tools import load_builtin_tools
        return f"{len(load_builtin_tools().all())} built-in tools"

    def brain():
        from .brain import make_brain
        from .memory import get_memory
        from .tools import load_builtin_tools
        b = make_brain(s, load_builtin_tools(), get_memory())
        ok, problem = b.check()
        if not ok:
            raise RuntimeError(problem)
        return getattr(b, "label_long", b.label)

    def ocr():
        from PIL import Image, ImageDraw, ImageFont
        from .tools import ocr as o
        img = Image.new("RGB", (500, 120), "white")
        ImageDraw.Draw(img).text((20, 30), "Jarvis online", fill="black", font=ImageFont.truetype("segoeui.ttf", 32))
        text = " ".join(line.text for line in o.recognize(img))
        if "jarvis" not in text.lower():
            raise RuntimeError(f"read {text!r}")
        return f"read {text!r}"

    def apps():
        from .tools.apps import match_app, start_apps
        found = start_apps()
        app, _ = match_app("settings", found)
        return f"{len(found)} apps indexed (e.g. {app['name'] if app else '?'})"

    def windows():
        from . import winutil
        return f"{len(winutil.list_windows())} windows; Desktop = {winutil.known_folder('desktop')}"

    def tts():
        from .voice.tts import Speaker
        sp = Speaker(s)
        t = time.time()
        audio, sr = sp.synth("At your service, sir.")
        return f"{sp.engine_name}: {len(audio) / sr:.1f}s of audio in {time.time() - t:.2f}s"

    def stt():
        import numpy as np
        from scipy.signal import resample_poly
        from .voice.stt import SpeechToText
        from .voice.tts import Speaker
        audio, sr = Speaker(s).synth("Open Spotify and play some jazz.")
        audio16 = resample_poly(audio, 16000, sr).astype(np.float32)
        t = time.time()
        text = SpeechToText(s).transcribe(audio16)
        return f"heard {text!r} in {time.time() - t:.2f}s"

    def wake():
        import numpy as np
        from scipy.signal import resample_poly
        from .voice.tts import Speaker
        from .voice.wakeword import WakeWord
        audio, sr = Speaker(s).synth("Hey Jarvis.")
        pcm = (resample_poly(audio, 16000, sr) * 32767).astype(np.int16)
        pcm = np.concatenate([np.zeros(16000, np.int16), pcm, np.zeros(16000, np.int16)])
        ww = WakeWord()
        best = max(ww.score(pcm[i:i + 1280]) for i in range(0, len(pcm) - 1280, 1280))
        return f"'hey jarvis' model loaded; synthetic-voice score {best:.2f}"

    def mic():
        import sounddevice as sd
        return sd.query_devices(kind="input")["name"]

    def web():
        from .tools.web import web_search
        out = web_search("capital of Australia", 3)
        if "Search failed" in out:
            raise RuntimeError(out)
        return f"{len(out)} chars of results"

    def news():
        from .tools.news import headlines
        items = headlines("top", count=3)
        if not items:
            raise RuntimeError("no headlines")
        return items[0]["title"][:70]

    def weather():
        from .tools.weather import get_weather
        return get_weather().splitlines()[0][:90]

    def gemini_live():
        import asyncio
        from google import genai
        from .config import secret
        key = secret("GEMINI_API_KEY")
        if not key:
            return "no GEMINI_API_KEY in .env - using the local voice"
        model = (s.get("live.models") or ["models/gemini-3.1-flash-live-preview"])[0]

        async def connect():
            client = genai.Client(api_key=key, http_options={"api_version": "v1beta"})
            t = time.time()
            async with client.aio.live.connect(model=model, config={"response_modalities": ["AUDIO"]}):
                return time.time() - t
        took = asyncio.run(connect())
        return f"{model.split('/')[-1]} connected in {took:.2f}s (voice {s.get('live.voice', 'Charon')})"

    def google():
        from . import google_auth as ga
        if not ga.connected("main"):
            return "not connected - double-click connect_google.bat to add Calendar, Gmail and Classroom"
        from .tools import gservices
        results = []
        for label, fn in gservices.HEALTH_CHECKS:
            try:
                results.append(f"{label} {fn()}")
            except Exception as e:
                raise RuntimeError(f"{label}: {e}") from e
        return f"{ga.email_of('main')}: " + "; ".join(results)

    def mcp_config():
        from .tools.mcp_bridge import load_config
        cfg = load_config()
        on = [k for k, v in cfg.items() if v.get("enabled", True)]
        return f"{len(cfg)} servers configured, {len(on)} enabled"

    return [("settings", settings_), ("tools", tools), ("Gemini Live", gemini_live), ("local brain", brain),
            ("OCR (Windows)", ocr),
            ("app index", apps), ("windows/folders", windows), ("text-to-speech", tts),
            ("speech-to-text", stt), ("wake word", wake), ("microphone", mic), ("web search", web),
            ("news feeds", news), ("weather", weather), ("Google", google), ("MCP config", mcp_config)]


def run() -> int:
    console = Console()
    table = Table(title="J.A.R.V.I.S. self-test", show_lines=False)
    table.add_column("check")
    table.add_column("result")
    failures = 0
    for name, fn in _checks():
        with console.status(f"checking {name}…"):
            try:
                table.add_row(name, f"[green]✓[/] {fn()}")
            except Exception as e:
                failures += 1
                table.add_row(name, f"[red]✗ {type(e).__name__}: {e}[/]")
                import logging
                logging.getLogger(__name__).debug(traceback.format_exc())
    console.print(table)
    console.print("[green]All systems nominal.[/]" if not failures else f"[yellow]{failures} check(s) need attention.[/]")
    return 1 if failures else 0
