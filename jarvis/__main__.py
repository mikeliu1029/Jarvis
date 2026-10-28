"""Run Jarvis:  python -m jarvis [--text] [--mute] [--no-hud] [--selftest] [--setup] [--say TEXT] [--ask TEXT]"""
from __future__ import annotations

import argparse
import logging
import logging.handlers
import sys
import warnings

from . import winutil
from .config import LOG_DIR, get_settings


def _setup_logging(verbose: bool) -> None:
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(LOG_DIR / "jarvis.log", maxBytes=2_000_000, backupCount=3,
                                                   encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s"))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.addHandler(handler)
    for noisy in ("httpx", "httpx2", "httpcore", "urllib3", "comtypes", "faster_whisper", "mcp", "primp",
                  "trafilatura", "asyncio", "phonemizer", "kokoro_onnx", "google_genai", "google.genai",
                  "websockets"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    warnings.filterwarnings("ignore", category=UserWarning)
    warnings.filterwarnings("ignore", category=FutureWarning, module=r"google\..*")  # Python-version nags


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="jarvis", description="J.A.R.V.I.S. - local desktop assistant")
    p.add_argument("--text", action="store_true", help="keyboard only (no microphone)")
    p.add_argument("--mute", action="store_true", help="don't speak replies")
    p.add_argument("--no-hud", action="store_true", help="don't start the browser HUD")
    p.add_argument("--setup", action="store_true", help="download voice/speech models and exit")
    p.add_argument("--selftest", action="store_true", help="check every subsystem and exit")
    p.add_argument("--google", action="store_true", help="connect Google (Calendar, Gmail, Drive, Classroom)")
    p.add_argument("--say", metavar="TEXT", help="speak a line in Jarvis's voice and exit")
    p.add_argument("--ask", metavar="TEXT", help="ask one question, print/speak the answer and exit")
    p.add_argument("--verbose", action="store_true", help="debug logging to memory/logs/jarvis.log")
    args = p.parse_args(argv)

    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _setup_logging(args.verbose)
    winutil.set_dpi_aware()
    settings = get_settings()

    if args.setup:
        from .setup_models import main as setup
        return setup()
    if args.selftest:
        from .selftest import run
        return run()
    if args.google:
        from .google_setup import main as google_setup
        return google_setup()
    if args.say:
        from .voice.tts import Speaker
        Speaker(settings).speak(args.say)
        return 0

    from .assistant import Jarvis
    if args.ask:
        jarvis = Jarvis(settings, voice_in=False, voice_out=not args.mute, hud=False)
        jarvis.mcp.start()
        jarvis.mcp.wait_ready(20)
        reply = jarvis.ask(args.ask)
        print(reply)
        if jarvis.voice_out:
            jarvis.speaker.speak(reply)
        jarvis.stop()
        return 0

    if not _single_instance():
        return 0

    hud_mode = "off" if args.no_hud else str(settings.get("hud.mode", "desktop")).lower()
    if hud_mode == "desktop":
        try:
            from .hud import desktop  # noqa: F401  (PyQt6 present?)
        except ImportError as e:
            print(f"Desktop HUD unavailable ({e}); using the browser HUD instead.")
            hud_mode = "browser"

    jarvis = Jarvis(settings, voice_in=not args.text, voice_out=not args.mute, hud=hud_mode == "browser")
    from .console import ConsoleUI
    ui = ConsoleUI(jarvis)

    if hud_mode == "desktop":
        return _run_desktop(jarvis, ui)

    def on_quit(event):  # "Jarvis, shut down" (the desktop window handles this itself)
        if event["type"] == "quit":
            import os
            import threading
            threading.Thread(target=lambda: (jarvis.stop(), os._exit(0)), daemon=True).start()

    from .events import bus
    bus.subscribe(on_quit)
    jarvis.start()
    ui.banner()
    try:
        jarvis.greet()
        ui.loop()
    finally:
        jarvis.stop()
    return 0


_instance_mutex = None


def _single_instance() -> bool:
    """Two Jarvises would both listen and both answer. A second launch (e.g. a double-click on the desktop
    button while he's running) brings the running one to the front instead."""
    global _instance_mutex
    import ctypes
    k32, u32 = ctypes.windll.kernel32, ctypes.windll.user32
    _instance_mutex = k32.CreateMutexW(None, False, "Local\\JARVIS-assistant-single-instance")
    if k32.GetLastError() != 183:  # ERROR_ALREADY_EXISTS
        return True
    found = []

    @ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    def each(hwnd, _):
        buf = ctypes.create_unicode_buffer(256)
        u32.GetWindowTextW(hwnd, buf, 256)
        if buf.value.endswith("— HUD") and u32.IsWindowVisible(hwnd):
            found.append(hwnd)
        return True

    u32.EnumWindows(each, None)
    if found:
        u32.ShowWindow(found[0], 9)  # SW_RESTORE
        u32.SetForegroundWindow(found[0])
    else:
        u32.MessageBoxW(None, "Jarvis is already running.", "J.A.R.V.I.S.", 0x40)
    print("Jarvis is already running.")
    return False


def _run_desktop(jarvis, ui) -> int:
    """Qt owns the main thread; Jarvis and the console run beside it."""
    import threading

    from .hud.desktop import MainWindow, create_app

    app = create_app()
    jarvis.desktop_hud = True
    window = MainWindow(jarvis)
    window.show()
    popup = None
    if jarvis.settings.get("hud.popup", True):
        from .hud.popup import SpeechPopup
        popup = SpeechPopup(jarvis)  # noqa: F841  (kept alive for the app's lifetime)

    def boot():
        jarvis.start()
        ui.banner()
        jarvis.greet()

    def console():
        # A QTimer started from this thread would never fire (no event loop here) - a signal is
        # queued onto the GUI thread instead.
        if ui.loop() == "quit":
            window.event_sig.emit({"type": "quit"})

    threading.Thread(target=boot, name="jarvis-boot", daemon=True).start()
    threading.Thread(target=console, name="jarvis-console", daemon=True).start()
    code = app.exec()
    jarvis.stop()
    return code


if __name__ == "__main__":
    sys.exit(main())
