"""The terminal interface: shows the conversation and lets you type commands."""
from __future__ import annotations

import threading
from typing import TYPE_CHECKING

from rich.console import Console
from rich.panel import Panel

from .events import bus

if TYPE_CHECKING:
    from .assistant import Jarvis

HELP = """[cyan]Talk:[/] say "Hey Jarvis" (or press Ctrl+Alt+J), or just type below.
[cyan]Try:[/] open Spotify · play lofi on YouTube · what's on my screen · daily briefing · news about AI
      search my PC for the budget spreadsheet · remind me in 20 minutes to stretch · system status
[cyan]Commands:[/] /help  /reset (new conversation)  /mute  /unmute  /hud  /quit"""


class ConsoleUI:
    def __init__(self, jarvis: "Jarvis"):
        self.jarvis = jarvis
        self.c = Console(highlight=False)
        self._lock = threading.Lock()
        bus.subscribe(self._on_event)

    def _print(self, *args, **kw) -> None:
        with self._lock:
            self.c.print(*args, **kw)

    def _on_event(self, e: dict) -> None:
        kind = e["type"]
        if kind == "user" and e.get("source") != "console":
            self._print(f"[bold white]You[/] [dim]({e.get('source')})[/] › {e['text']}")
        elif kind == "jarvis":
            self._print(f"[bold cyan]JARVIS[/] › {e['text']}")
        elif kind == "tool":
            self._print(f"[dim]   ↳ {e['name']}({e.get('args', '')})[/]")
        elif kind == "notice":
            self._print(f"[yellow]• {e['text']}[/]")
        elif kind == "confirm":
            self._print(f"[bold yellow]? {e['text']}[/] [dim](type yes or no)[/]")
        elif kind == "state" and e.get("value") == "listening":
            self._print("[dim cyan]   (listening…)[/]")

    def banner(self) -> None:
        j = self.jarvis
        ears = "always on" if j.live_active or (j.listener and not j.listener.error) else "off"
        lines = [f"[bold cyan]J.A.R.V.I.S.[/]  core: {j.core_label}  ·  voice: {j.voice_label}"
                 f"  ·  mic: {ears}  ·  tools: {len(j.registry.all())}"]
        if j.hud:
            lines.append(f"Browser HUD: {j.hud.url}")
        elif getattr(j, "desktop_hud", False):
            lines.append("HUD: desktop window (close it to shut Jarvis down)")
        connected = {k: v for k, v in j.mcp.status.items()}
        if connected:
            lines.append("MCP: " + ", ".join(f"{k} ({v})" for k, v in connected.items()))
        lines.append(HELP)
        self._print(Panel("\n".join(lines), border_style="cyan"))

    def loop(self) -> str:
        """Returns "quit" when the user asked to quit, "eof" when there is no console (e.g. pythonw)."""
        j = self.jarvis
        while not j.stopping.is_set():
            try:
                text = input()
            except EOFError:
                return "eof"
            except KeyboardInterrupt:
                return "quit"
            # Piped or redirected input can start with a byte-order mark, which would hide a /command
            # and send it to the brain as a sentence instead.
            text = text.replace(chr(0xFEFF), "").replace(chr(0x200B), "").strip()
            if not text:
                continue
            cmd = text.lower()
            if cmd in ("/quit", "/exit", "quit", "exit", "goodbye jarvis"):
                j.say(f"Goodbye, {j.title}.", log_it=False)
                return "quit"
            if cmd == "/help":
                self._print(HELP)
            elif cmd == "/reset":
                j.brain.reset()
                self._print("[dim]Started a fresh conversation.[/]")
            elif cmd == "/mute":
                j.voice_out = False
                j.speaker.stop()
                self._print("[dim]Muted.[/]")
            elif cmd == "/unmute":
                j.voice_out = True
                self._print("[dim]Speaking again.[/]")
            elif cmd == "/hud" and j.hud:
                j.hud.open(app_window=True)
            else:
                j.submit(text, "console")
        return "quit"
