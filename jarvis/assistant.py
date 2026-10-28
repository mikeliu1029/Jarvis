"""The orchestrator: routes input (voice, keyboard, HUD) to the brain, speaks replies, handles
confirmations, reminders, the daily briefing and proactive alerts."""
from __future__ import annotations

import logging
import queue
import random
import re
import threading
import time
from datetime import date, datetime

import psutil

from . import winutil
from .brain import make_brain
from .config import Settings
from .events import bus
from .memory import get_memory
from .tools import load_builtin_tools
from .tools.mcp_bridge import MCPBridge
from .tools.registry import Tool, ToolResult

log = logging.getLogger(__name__)

YES = re.compile(r"^\s*(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|proceed|please do|please|affirmative|confirm|"
                 r"of course|certainly|absolutely|definitely|go for it|sounds good|all ?right|do so|y)\b", re.I)
NO = re.compile(r"^\s*(no|nope|nah|don'?t|cancel|stop|negative|abort|n)\b", re.I)
STOP = re.compile(r"^\s*(stop|cancel|shut up|be quiet|quiet|silence|never ?mind|that'?s all|that will be all)[.!]?\s*$", re.I)
NEWS_RUNDOWN = ("Give a quick spoken news rundown: start with 'In the news,' and cover the three most significant "
                "stories - lead with major world and national events (conflicts, unrest, elections, disasters, big "
                "economic news) before tech - one short sentence each. Mention the full list is on screen.")
ARTICLE_PICK = ("\n\nFinally, open ONE article for the user to read: call open_article with a topic you know they're "
                "into (games they play, their interests, what they were working on - see your memory). If you know "
                "nothing about them yet, use the topic of the most interesting headline above. Say in one short line "
                "what you've put on screen and why you thought they'd like it.")
# All the start-up turn may use: it reads things and opens one article, but changes nothing on the PC.
# tool -> allowed actions ("" = the tool's default action), or None for any action of a read-only tool.
STARTUP_TOOLS = {"daily_briefing": None, "get_news": None, "get_weather": None, "system_status": None,
                 "open_article": None, "classroom": None, "memory": {"recall", "save"},
                 "calendar": {"", "agenda", "find"}, "email": {"", "unread", "search"}, "tasks": {"list"}}
ACKS = ["One moment, {t}.", "Right away, {t}.", "On it, {t}.", "Just a moment, {t}.", "Checking now, {t}."]


class Jarvis:
    def __init__(self, settings: Settings, *, voice_in: bool = True, voice_out: bool = True, hud: bool = True):
        self.settings = settings
        self.title = str(settings.get("user.title", "sir"))
        self.memory = get_memory()
        self.registry = load_builtin_tools()
        self.stopping = threading.Event()
        self.inbox: queue.Queue = queue.Queue()
        self.state = "idle"
        self.busy = False
        self._pending: dict | None = None
        self._offer: str | None = None
        self._cancel = threading.Event()
        self._acked = False
        self._turn_source = "console"
        self._alerted: dict[str, float] = {}
        self._cpu_high_count = 0
        self._last_args: dict[str, dict] = {}
        self.session_log: list[str] = []
        self.mic_muted = False
        self.last_user_activity = time.time()
        self.started = time.time()

        from .plugin_loader import load_plugins
        for plugin, ok, message in load_plugins(self.registry):
            if not ok:
                bus.emit("notice", level="warning", text=f"Plugin '{plugin}' didn't load: {message}")

        self.brain = make_brain(settings, self.registry, self.memory)
        self.mcp = MCPBridge(self.registry)

        from .config import secret
        from .voice.tts import SilentSpeaker
        self.speaker = SilentSpeaker()
        self.voice_out = voice_out and bool(settings.get("voice.output", True))
        self.listener = None
        self.voice_in = voice_in and bool(settings.get("voice.input", True))
        # Gemini Live (natural real-time voice) when there's a key; the local pipeline is the fallback.
        self.live = None
        provider = str(settings.get("brain.provider", "gemini_live")).lower()
        self.use_live = (provider in ("gemini_live", "gemini") and bool(secret("GEMINI_API_KEY"))
                         and self.voice_in and self.voice_out)
        if not self.use_live:
            self._init_local_speaker()
        self.hud = None
        # `hud` here means the optional *browser* HUD; the desktop HUD is a window run by __main__.
        self.hud_enabled = hud
        self.ok, self.brain_problem = True, ""

    # ---- lifecycle ---------------------------------------------------------------
    def _init_local_speaker(self) -> None:
        if not self.voice_out:
            return
        try:
            from .voice.tts import Speaker
            self.speaker = Speaker(self.settings)
        except Exception as e:
            log.exception("text-to-speech failed")
            bus.emit("notice", level="warning", text=f"Speech output is off ({e}).")

    def _start_local_listener(self) -> None:
        if not self.voice_in:
            return
        from .voice.listener import VoiceListener
        self.listener = VoiceListener(self, self.settings)
        self.listener.start()
        self.listener.ready.wait(60)
        if not self.listener.error:
            mode = "always listening - say \"Jarvis, ...\"" if self.listener.always else "say \"Hey Jarvis\""
            bus.emit("notice", level="info",
                     text=f"Microphone: {self.listener.device_label} ({mode}). "
                          f"Voice output: {getattr(self.speaker, 'device_label', 'default')}.")

    @property
    def live_active(self) -> bool:
        return bool(self.live and self.live.session is not None and not self.live.failed.is_set())

    @property
    def core_label(self) -> str:
        if self.use_live:
            model = self.live.model.split("/")[-1] if self.live and self.live.model else "connecting"
            return f"GEMINI LIVE · {model}"
        return f"LOCAL CORE · {getattr(self.brain, 'model', self.brain.label)}"

    @property
    def voice_label(self) -> str:
        if self.use_live and self.live:
            return f"GEMINI {self.live.voice}".upper()
        return self.speaker.engine_name.upper()

    def start(self) -> None:
        self.mcp.start()
        threading.Thread(target=self._worker, name="jarvis-worker", daemon=True).start()
        threading.Thread(target=self._scheduler, name="jarvis-scheduler", daemon=True).start()
        if self.use_live:
            from .live import GeminiLive
            self.live = GeminiLive(self, self.settings)
            self.live.start()
            deadline = time.time() + 20
            while time.time() < deadline and not (self.live.connected.is_set() or self.live.failed.is_set()):
                time.sleep(0.1)
            if self.live.connected.is_set():
                self.ok, self.brain_problem = True, ""
                bus.emit("notice", level="info",
                         text=f"Gemini Live connected ({self.live.model.split('/')[-1]}, voice {self.live.voice}). "
                              f"Microphone: {self.live.in_label} - always on (F4 mutes). "
                              f"Voice output: {self.live.out_label}.")
                self._start_hotkey()
            elif not self.live.failed.is_set():
                self.on_live_failed("it didn't connect within 20 seconds")
        else:
            self.ok, self.brain_problem = self.brain.check()
            threading.Thread(target=self.brain.warm_up, name="warm-up", daemon=True).start()
            if self.voice_in:
                self._start_local_listener()
                self._start_hotkey()
        if self.hud_enabled:
            try:
                from .hud.server import HUDServer
                self.hud = HUDServer(self, int(self.settings.get("hud.port", 7777)))
                self.hud.start()
                if self.settings.get("hud.open_on_start", True):
                    self.hud.open(app_window=bool(self.settings.get("hud.app_window", True)))
            except OSError as e:
                bus.emit("notice", level="warning", text=f"HUD couldn't start ({e}).")
        self.mcp.wait_ready(8)

    def on_live_failed(self, why: str) -> None:
        """Gemini Live can't be used (no internet, key rejected, out of quota): carry on with the local voice."""
        if not self.use_live:
            return
        self.use_live = False
        if self.live:
            self.live.stop()
        self.live = None
        bus.emit("notice", level="warning", text=f"Gemini Live is unavailable ({why}) - switching to the local voice.")

        def fallback():
            self.ok, self.brain_problem = self.brain.check()
            threading.Thread(target=self.brain.warm_up, daemon=True).start()
            self._init_local_speaker()
            self._start_local_listener()

        threading.Thread(target=fallback, name="local-fallback", daemon=True).start()

    # ---- Gemini Live callbacks --------------------------------------------------------------
    def on_live_user(self, text: str) -> None:
        self.last_user_activity = time.time()
        bus.emit("user", text=text, source="voice")
        self.memory.log("user", text=text, source="voice")
        self.session_log.append(f"User: {text}")

    def on_live_reply(self, text: str) -> None:
        bus.emit("jarvis", text=text)
        self.memory.log("jarvis", text=text)
        self.session_log.append(f"Jarvis: {text}")

    def stop(self) -> None:
        if self.stopping.is_set():
            return
        self.stopping.set()
        if self.live:
            self.live.stop()
        self.speaker.stop()
        self._save_session_summary()
        self.mcp.stop()
        if self.hud:
            self.hud.stop()

    def _save_session_summary(self) -> None:
        """Note what this session was about (what we did, what's in progress); the next start-up mentions it.

        Runs during the session as well as at shutdown, so a crash or force-close still leaves a note."""
        if sum(1 for line in self.session_log if line.startswith("User:")) < 2:
            return
        if len(self.session_log) == getattr(self, "_summarised_len", 0):
            return  # nothing new since the last note
        self._summarised_len = len(self.session_log)
        prompt = ("These are notes from a conversation between a user ('sir') and their assistant Jarvis, including "
                  "the actions Jarvis took. Write one or two sentences, as a note to yourself, about what sir was "
                  "doing and anything left in progress - e.g. 'Sir was revising a history essay and still needed to "
                  "add the sources.' Use only what the notes say. Output only the note.\n\n" +
                  "\n".join(self.session_log[-60:]))
        result: list[str] = []

        def work():
            try:
                result.append(self._summarise(prompt))
            except Exception:
                log.exception("session summary failed")

        t = threading.Thread(target=work, daemon=True)
        t.start()
        t.join(25)
        if result and result[0]:
            self.memory.save_session(result[0])

    def _summarise(self, prompt: str) -> str:
        """Gemini's fast text model when there's a key (well under a second); otherwise the local brain."""
        from .config import secret
        if secret("GEMINI_API_KEY"):
            from google import genai
            client = genai.Client(api_key=secret("GEMINI_API_KEY"))
            for model in self.settings.get("live.text_models", ["gemini-flash-lite-latest", "gemini-2.5-flash-lite"]):
                try:
                    text = (client.models.generate_content(model=model, contents=prompt).text or "").strip()
                    if text:
                        return text
                except Exception as e:
                    log.warning("summary with %s failed: %s", model, e)
        return self.brain.complete(prompt)

    def interrupt(self) -> None:
        """Stop talking and abandon the current task (ESC / the HUD's interrupt button)."""
        if self.live:
            self.live.interrupt()
        self.speaker.stop()
        if self.busy:
            self._cancel.set()
        bus.emit("notice", level="info", text="Interrupted.")

    def set_mic_muted(self, muted: bool) -> None:
        self.mic_muted = bool(muted)
        bus.emit("mic", muted=self.mic_muted)
        bus.emit("notice", level="info", text="Microphone muted." if muted else "Microphone active.")

    def _start_hotkey(self) -> None:
        combo = str(self.settings.get("voice.hotkey", "") or "")
        if not combo:
            return
        try:
            from pynput import keyboard
            listener = keyboard.GlobalHotKeys({combo: self.push_to_talk})
            listener.daemon = True
            listener.start()
        except Exception as e:
            log.warning("hotkey %s not available: %s", combo, e)

    def push_to_talk(self) -> None:
        if self.live:  # the mic is always open in live mode; the key just cuts him off
            self.live.interrupt()
            return
        if self.speaker.speaking:
            self.speaker.stop()
        if self.listener:
            self.listener.request_listen()

    # ---- state & speech ------------------------------------------------------------
    def set_state(self, state: str) -> None:
        if state != self.state:
            self.state = state
            bus.emit("state", value=state)

    def say(self, text: str, *, log_it: bool = True) -> None:
        if self.live_active:  # Gemini says it in its own voice; the transcript arrives via on_live_reply
            self.live.announce(text)
            return
        bus.emit("jarvis", text=text)
        if log_it:
            self.memory.log("jarvis", text=text)
        if self.voice_out:
            self.set_state("speaking")
            self.speaker.speak(text)
        self.set_state("thinking" if self.busy else "idle")

    def _say_async(self, text: str) -> None:
        threading.Thread(target=self.speaker.speak, args=(text,), daemon=True).start()

    # ---- input ---------------------------------------------------------------------
    def submit(self, text: str, source: str = "console") -> None:
        text = text.replace(chr(0xFEFF), "").strip()
        if not text:
            return
        if self.live_active:
            if self.live.pending and (YES.match(text) or NO.match(text)):
                bus.emit("user", text=text, source=source)
                self.live.resolve(bool(YES.match(text)) and not NO.match(text))
                return
            if STOP.match(text):
                bus.emit("user", text=text, source=source)
                self.live.interrupt()
                return
            self.last_user_activity = time.time()
            bus.emit("user", text=text, source=source)
            self.memory.log("user", text=text, source=source)
            self.session_log.append(f"User: {text}")
            self.live.send_text(text)
            return
        if self._pending is not None:  # answering "Shall I proceed?"
            bus.emit("user", text=text, source=source)
            self._pending["answer"] = bool(YES.match(text)) and not NO.match(text)
            self._pending["event"].set()
            return
        if STOP.match(text):
            bus.emit("user", text=text, source=source)
            self.speaker.stop()
            self._cancel.set()
            self._offer = None
            if not self.busy:
                self.say(f"Very good, {self.title}.", log_it=False)
            return
        if self._offer:
            offer, self._offer = self._offer, None
            if YES.match(text) and len(text.split()) <= 4:
                text = "Yes - give me my daily briefing." if offer == "briefing" else text
            elif NO.match(text) and len(text.split()) <= 4:
                bus.emit("user", text=text, source=source)
                self.inbox.put(("say", f"Very good, {self.title}.", source))
                return
        self.inbox.put(("chat", text, source))

    def ask(self, text: str) -> str:
        """One-shot question (used by --ask): returns the reply without the queue."""
        return self.brain.respond(text, self._context_line(), self)

    # ---- hooks used by the brain -----------------------------------------------------
    def on_tool_start(self, tool: Tool | None, name: str, args: dict) -> None:
        self._last_args[name] = args
        if name not in ("memory", "read_screen", "look_at_screen"):
            self.session_log.append(f"Jarvis did: {name}({', '.join(f'{k}={v}' for k, v in args.items())[:160]})")
        preview = ", ".join(f"{k}={str(v)[:60]!r}" for k, v in list(args.items())[:4])
        bus.emit("tool", name=name, args=preview)
        self.memory.log("tool", name=name, args=args)
        if (tool and tool.slow and not self._acked and self.voice_out and self._turn_source == "voice"
                and self.settings.get("voice.acknowledge", True)):
            self._acked = True
            self._say_async(random.choice(ACKS).format(t=self.title))

    # Results worth reading on screen go to the HUD's content panel; Jarvis only speaks a summary.
    CONTENT_TOOLS = {"web_search": "SEARCH", "get_news": "NEWS", "daily_briefing": "BRIEFING",
                     "read_webpage": "PAGE", "calendar": "CALENDAR", "email": "INBOX", "read_file": "FILE",
                     "system_status": "SYSTEM", "find_files": "FILES"}

    def on_tool_end(self, name: str, result: ToolResult) -> None:
        bus.emit("tool_result", name=name, ok=not result.is_error, text=result.text[:300])
        if name in self.CONTENT_TOOLS and not result.is_error:
            from .tools.outcome import unwrap
            args = self._last_args.get(name, {})
            detail = args.get("query") or args.get("topic") or args.get("category") or args.get("url") or ""
            title = self.CONTENT_TOOLS[name] + (f" — {detail}" if detail else "")
            body = unwrap(result.text)
            if name == "daily_briefing":
                body = body.split("\n\nDeliver this as")[0]
            bus.emit("content", title=title[:60], text=body[:6000])

    def confirm(self, question: str) -> bool:
        event = threading.Event()
        self._pending = {"event": event, "answer": False}
        bus.emit("confirm", text=question)
        spoken = question if len(question) <= 140 else question.split(":")[0] + " - the details are on screen."
        self.say(f"{spoken} Shall I proceed, {self.title}?", log_it=False)
        if self.listener:
            self.listener.open_follow_up(12)
        event.wait(timeout=45)
        answer = self._pending["answer"] if event.is_set() else False
        self._pending = None
        bus.emit("confirm_done", approved=answer)
        self.memory.log("confirm", question=question, approved=answer)
        return answer

    def cancelled(self) -> bool:
        return self._cancel.is_set()

    # ---- voice listener callbacks ------------------------------------------------------
    def on_listen_start(self, mode: str) -> None:
        if self.speaker.speaking:
            self.speaker.stop()
        self.set_state("listening")
        if mode in ("wake", "manual"):
            from .voice import audio
            audio.play_async(*audio.chime("wake"))
            threading.Thread(target=self.brain.warm_up, daemon=True).start()

    def on_listen_end(self, heard: bool, mode: str) -> None:
        if not heard:
            self.set_state("thinking" if self.busy else "idle")
            if mode in ("wake", "manual"):  # only when you deliberately woke him and said nothing
                from .voice import audio
                audio.play_async(*audio.chime("cancel"))

    def on_name_only(self) -> None:
        """You said just "Jarvis?" - answer, and take the next sentence as the request."""
        self.inbox.put(("say", f"Yes, {self.title}?", "voice"))

    def can_follow_up(self) -> bool:
        return self._pending is not None or (not self.busy and not self.speaker.speaking)

    # ---- the worker ------------------------------------------------------------------
    def _context_line(self) -> str:
        win = winutil.foreground_window()
        active = f"{win.title} ({win.process})" if win and win.title else "desktop"
        return f"[{datetime.now():%A %d %B %Y, %H:%M} | active window: {active}]"

    def _worker(self) -> None:
        winutil.ensure_com()
        while not self.stopping.is_set():
            try:
                kind, text, source = self.inbox.get(timeout=0.5)
            except queue.Empty:
                continue
            if kind == "say":
                self.say(text)
                if source == "voice" and self.listener:
                    self.listener.open_follow_up()
                continue
            self.busy, self._acked, self._turn_source = True, False, source
            self._cancel.clear()
            self.last_user_activity = time.time()
            self.memory.log("user", text=text[:500], source=source)
            if source != "auto":  # automatic prompts (start-up news, briefing) aren't shown as if you'd said them
                bus.emit("user", text=text, source=source)
                self.session_log.append(f"User: {text}")
            self.set_state("thinking")
            try:
                reply = self.brain.respond(text, self._context_line(), self)
            except Exception as e:
                log.exception("brain failed")
                ok, problem = self.brain.check()
                reply = (f"I'm afraid my thinking is offline, {self.title}. {problem}" if not ok else
                         f"Something went wrong there, {self.title}: {type(e).__name__}.")
            self.busy = False
            self.session_log.append(f"Jarvis: {reply}")
            if not self._cancel.is_set():
                self.say(reply)
            self.set_state("idle")
            if source == "voice" and self.listener:
                self.listener.open_follow_up()

    # ---- greeting, briefing, reminders, proactive alerts ------------------------------
    def greet(self) -> None:
        hour = datetime.now().hour
        part = "morning" if hour < 12 else "afternoon" if hour < 18 else "evening"
        if not self.ok:
            self.say(f"Good {part}, {self.title}. I'm online, but there's a problem: {self.brain_problem}")
            return
        line = f"Good {part}, {self.title}. All systems are online."
        mode = str(self.settings.get("briefing.on_first_launch", "auto")).lower()
        first_today = self.memory.get("last_briefing") != date.today().isoformat() and \
            self.memory.get("last_offer") != date.today().isoformat()
        if self.live_active:
            self._greet_live(part, first_today and mode in ("offer", "auto"))
            return
        if first_today and mode in ("offer", "auto"):
            self.memory.set("last_offer", date.today().isoformat())
            if mode == "auto":
                self.say(line)
                self.inbox.put(("chat", "Give me my daily briefing.", "auto"))
                return
            self._offer = "briefing"
            line += " Shall I run through today's briefing?"
            if self.listener:
                threading.Timer(0.1, lambda: self.listener.open_follow_up(10)).start()
            self.say(line)
            return
        if self.settings.get("briefing.news_on_launch", True):
            # Fetch the headlines while the greeting is being spoken, then hand them to the brain
            # as data so the rundown needs no tool round-trip (MARK LVI's two-phase start-up).
            result: list[str] = []
            fetch = threading.Thread(target=lambda: result.append(self._startup_headlines()), daemon=True)
            fetch.start()
            self.say(line)
            fetch.join(40)
            if result and result[0]:
                self.inbox.put(("chat", "[Start-up news - automatic, not typed by the user] Latest headlines:\n" +
                                result[0] + "\n\n" + NEWS_RUNDOWN + self._article_pick, "auto"))
            else:
                self.say(f"I'm afraid the news feeds aren't reachable at the moment, {self.title}. "
                         "Ask me again in a minute.")
            return
        self.say(line)

    @property
    def _article_pick(self) -> str:
        return ARTICLE_PICK if self.settings.get("briefing.open_article_on_launch", True) else ""

    def _greet_live(self, part: str, full_briefing: bool) -> None:
        """One natural opening turn: the greeting and the news together, in Gemini's voice."""
        greeting = f"[Start-up] Greet the user with a short, natural 'Good {part}, {self.title}' line."
        last = self.memory.unmentioned_session()
        if last:
            greeting += (f" Then, in one short line, pick up where you left off - last time ({last['date']}): "
                         f"{last['summary']} (Just mention it - don't open or start anything for it.)")

        def send(text: str) -> None:
            self.live.send_text(text, tools=STARTUP_TOOLS)  # nobody has asked for anything yet

        if full_briefing:
            self.memory.set("last_offer", date.today().isoformat())
            send(greeting + " Then give the daily briefing - call daily_briefing." + self._article_pick)
            return
        if not self.settings.get("briefing.news_on_launch", True):
            send(greeting)
            return
        news = self._startup_headlines()
        if news:
            send(f"{greeting} Then a quick news rundown. Latest headlines:\n{news}\n\n{NEWS_RUNDOWN}" + self._article_pick)
        else:
            send(greeting + " Then mention the news feeds aren't reachable right now, so you'll catch them up on the "
                            "news later.")

    def _startup_headlines(self) -> str:
        """Headlines for the start-up rundown (also shown in the HUD). Retries, because the first
        launch after boot or a network blip often can't resolve the news sites for a few seconds."""
        from .tools.news import format_items, headlines
        from .tools.outcome import untrusted
        # World and national news first - recency alone lets gadget news crowd out what matters.
        plan = [("world", 4), ("top", 4), ("tech", 2)]
        for attempt in range(4):
            seen, sections = set(), []
            for cat, n in plan:
                items = [it for it in headlines(cat, count=n + 3) if it["title"] not in seen][:n]
                seen.update(it["title"] for it in items)
                if items:
                    sections.append((cat, items))
            if sections:
                text = "\n\n".join(f"{cat.upper()}:\n{format_items(items)}" for cat, items in sections)
                bus.emit("content", title="NEWS — latest headlines", text=text)
                return untrusted("news feeds", text)
            if attempt < 3:
                time.sleep(6)
        return ""

    def _announce(self, key: str, text: str, cooldown: float = 3600, urgent: bool = False) -> None:
        if time.time() - self._alerted.get(key, 0) < cooldown:
            return
        if not urgent and winutil.is_fullscreen_foreground():
            return  # don't interrupt games or films
        self._alerted[key] = time.time()
        bus.emit("notice", level="info", text=text)
        self.inbox.put(("say", text, "system"))

    def _scheduler(self) -> None:
        last_check = 0.0
        while not self.stopping.wait(5):
            try:
                for r in self.memory.pop_due_reminders():
                    self._announce(f"reminder{r['id']}", f"{self.title.capitalize()}, a reminder: {r['message']}.",
                                   cooldown=0, urgent=True)
                at = str(self.settings.get("briefing.time", "") or "")
                today = date.today().isoformat()
                if at and datetime.now().strftime("%H:%M") >= at and self.memory.get("last_briefing") != today \
                        and self.memory.get("last_auto_briefing") != today:
                    self.memory.set("last_auto_briefing", today)
                    self.inbox.put(("chat", "Give me my daily briefing.", "auto"))
                if time.time() - last_check >= 60 and self.settings.get("proactive.enabled", True):
                    last_check = time.time()
                    self._health_check()
                    self._check_monitors()
                    # Keep the "what we were doing" note fresh: after 3 quiet minutes, write it down.
                    if time.time() - self.last_user_activity > 180:
                        threading.Thread(target=self._save_session_summary, daemon=True).start()
            except Exception:
                log.exception("scheduler tick failed")

    def _check_monitors(self) -> None:
        """Watched news topics: announce genuinely new headlines, a few times a day at most."""
        every = float(self.settings.get("monitor.check_every_hours", 4)) * 3600
        if self.busy or time.time() - self.last_user_activity < 30:
            return
        from .tools.news import headlines
        for topic, info in self.memory.monitors().items():
            if time.time() - info.get("last_check", 0) < every:
                continue
            items = headlines(topic=topic, count=8)
            seen = list(info.get("seen", []))
            fresh = [it for it in items if it["title"] not in seen
                     and it["published"] > max(info.get("last_check", 0), time.time() - 2 * 86400)]
            first_check = not info.get("last_check")
            self.memory.update_monitor(topic, seen + [it["title"] for it in items if it["title"] not in seen])
            if fresh and not first_check:  # the first check only records a baseline
                top = fresh[0]
                src = f" ({top['source']})" if top.get("source") else ""
                self._announce(f"monitor:{topic}", f"News on {topic}, {self.title}: {top['title']}{src}.",
                               cooldown=every / 2)
                bus.emit("content", title=f"MONITOR — {topic}",
                         text="\n".join(f"- {it['title']} ({it.get('source', '')})" for it in fresh[:6]))
            break  # at most one topic per tick keeps this light

    def _health_check(self) -> None:
        s, t = self.settings, self.title
        cpu = psutil.cpu_percent(interval=1)
        self._cpu_high_count = self._cpu_high_count + 1 if cpu >= float(s.get("proactive.cpu_high", 92)) else 0
        if self._cpu_high_count >= 3:
            from .tools.system import _top_processes
            top = _top_processes(1)[0]
            culprit = f" {top[0][0]} seems to be the culprit." if top else ""
            self._announce("cpu", f"{t.capitalize()}, the processor has been flat out for a few minutes.{culprit}")
        if psutil.virtual_memory().percent >= float(s.get("proactive.memory_high", 92)):
            self._announce("ram", f"Memory is nearly full, {t}. Closing a few programs may help.")
        try:
            free = psutil.disk_usage("C:\\").free / 2**30
            if free < float(s.get("proactive.disk_low_gb", 15)):
                self._announce("disk", f"Drive C is down to {free:.0f} gigabytes free, {t}.", cooldown=6 * 3600)
        except OSError:
            pass
        bat = psutil.sensors_battery()
        if bat and not bat.power_plugged and bat.percent <= float(s.get("proactive.battery_low", 20)):
            self._announce("battery", f"Battery is at {bat.percent:.0f} percent, {t}. Might I suggest the charger?",
                           cooldown=1800, urgent=True)

    # ---- status for the HUD ---------------------------------------------------------
    def status(self) -> dict:
        vm = psutil.virtual_memory()
        return {
            "state": self.state,
            "brain": getattr(self.brain, "label_long", self.brain.label),
            "brain_ok": self.ok,
            "voice": self.speaker.engine_name,
            "listening": bool(self.listener and not self.listener.error),
            "cpu": psutil.cpu_percent(interval=None),
            "ram": vm.percent,
            "time": datetime.now().strftime("%H:%M"),
            "mcp": self.mcp.status,
            "tools": len(self.registry.all()),
        }
