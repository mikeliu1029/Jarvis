"""Gemini Live: real-time spoken conversation, like ChatGPT voice mode.

One model hears your audio and answers in its own natural voice - no separate speech-to-text or
text-to-speech steps - so the first word arrives in about half a second and you can talk over it.
Jarvis's own tools (opening apps and games, the screen, news, memory...) still run on this PC:
Gemini asks for them, they run here, and the result goes back. The session layout (mic stream,
receive loop, playback, session resumption, the model ladder) follows MARK LVI by FatihMakes
(CC BY-NC 4.0).
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import re
import threading
import time
from datetime import datetime
from typing import TYPE_CHECKING, Iterable

import numpy as np
import sounddevice as sd

from .config import Settings, secret
from .events import bus
from .persona import build_system_prompt
from .voice import audio

if TYPE_CHECKING:
    from .assistant import Jarvis

log = logging.getLogger(__name__)

IN_RATE, OUT_RATE = 16000, 24000
YES = re.compile(r"^\W*(yes|yeah|yep|yup|sure|ok|okay|do it|go ahead|proceed|please do|please|confirm|affirmative|of course|"
                 r"certainly|absolutely|definitely|go for it|sounds good|all ?right|do so)\b", re.I)
NO = re.compile(r"^\W*(no|nope|nah|don'?t|cancel|stop|negative|abort)\b", re.I)
HEADPHONES = ("headphone", "headset", "arctis", "airpods", "buds", "earphone", "earbud")
_KEEP = object()  # "leave the automatic-turn tool limit as it is"
REPEATABLE = {"press_keys", "type_text", "mouse", "click_on_screen", "read_screen", "look_at_screen",
              "media_control", "windows"}

LIVE_RULES = """
## Live conversation
- You are speaking out loud in real time. Talk like a person - relaxed, warm, natural rhythm - with a refined British (Received Pronunciation) accent, in the manner of Paul Bettany's JARVIS. Never sound like you're reading.
- The microphone is always on, so you also hear things not meant for you: the user talking to other people, games, videos, music. Only respond when the user addresses you ("Jarvis") or is clearly continuing a conversation with you. Otherwise stay completely silent.
- Before anything slow (opening a game, searching the web, news, the briefing) say a quick acknowledgement such as "Right away, sir." and then call the tool.
- If a tool result says [ask], ask the user that exact question and wait - you'll be told what they decided. Never say it was done.
- Messages that start with [Start-up], [Announce] or [System] come from the assistant software, not from the user.
- It is {now} (local time) at the start of this session.
"""

_TYPES = {"object": "OBJECT", "string": "STRING", "integer": "INTEGER", "number": "NUMBER",
          "boolean": "BOOLEAN", "array": "ARRAY"}


def gemini_schema(s) -> dict:
    """JSON schema -> the subset Gemini function declarations accept."""
    if not isinstance(s, dict):
        return {"type": "STRING"}
    t = s.get("type", "object" if "properties" in s else "string")
    if isinstance(t, list):
        t = next((x for x in t if x != "null"), "string")
    out: dict = {"type": _TYPES.get(str(t).lower(), "STRING")}
    if s.get("description"):
        out["description"] = str(s["description"])[:600]
    if s.get("enum") and out["type"] == "STRING":
        out["enum"] = [str(e) for e in s["enum"]]
    if out["type"] == "OBJECT":
        props = s.get("properties") or {}
        out["properties"] = {k: gemini_schema(v) for k, v in props.items()}
        req = [r for r in s.get("required", []) if r in props]
        if req:
            out["required"] = req
    elif out["type"] == "ARRAY":
        out["items"] = gemini_schema(s.get("items") or {"type": "string"})
    return out


class _Reconnect(Exception):
    pass


def _root(e: BaseException) -> BaseException:
    while isinstance(e, BaseExceptionGroup) and e.exceptions:
        e = e.exceptions[0]
    return e


class GeminiLive:
    def __init__(self, jarvis: "Jarvis", settings: Settings):
        self.jarvis = jarvis
        self.settings = settings
        self.registry = jarvis.registry
        self.models = list(settings.get("live.models", ["models/gemini-3.1-flash-live-preview",
                                                       "models/gemini-2.5-flash-native-audio-latest"]))
        self.voice = str(settings.get("live.voice", "Charon"))
        self.proactive = bool(settings.get("live.proactive_audio", True))
        self.model = ""
        self.connected = threading.Event()
        self.failed = threading.Event()
        self.error = ""
        self.loop: asyncio.AbstractEventLoop | None = None
        self.session = None
        self._stop = threading.Event()
        self._model_i = 0
        self._resume: str | None = None
        self._play_q: asyncio.Queue | None = None
        self._mic_q: asyncio.Queue | None = None
        self._play_end = 0.0
        self._pending: tuple | None = None
        self._recent: dict[str, tuple[float, str]] = {}
        self._images: list[tuple[bytes, str, str]] = []
        self._user_buf: list[str] = []
        self._user_last = 0.0
        self._out_buf: list[str] = []
        # addressing gate (see _decide)
        self._turn: str | None = None
        self._held: list[tuple[str, object]] = []
        self._wait_since = 0.0
        self._heard = ""
        self._heard_at = 0.0
        self._system_turn = False   # the model's next reply answers something we sent, not overheard speech
        self._system_used = False   # ...and has that reply been given yet?
        self._system_at = 0.0
        self._auto_tools: dict | None = None  # tool -> allowed actions, while the turn is one we started
        self._last_decision = ""
        self._last_allow = 0.0
        self._conversation_until = 0.0
        self.conversation_seconds = float(settings.get("voice.conversation_seconds", 20))

        out_hint = str(settings.get("voice.output_device", "") or "")
        in_hint = str(settings.get("voice.input_device", "") or "")
        self.out_device = audio.find_output_device(out_hint)
        self.in_device = audio.find_input_device(in_hint)
        self.out_label = audio.device_name(self.out_device, "output")
        self.in_label = audio.device_name(self.in_device, "input")
        barge = str(settings.get("live.barge_in", "auto")).lower()
        # Talking over Jarvis works on headphones; on speakers the mic would hear him and he'd interrupt himself.
        self.barge_in = barge == "true" or (barge == "auto" and any(w in self.out_label.lower() for w in HEADPHONES))

    # ---- public (any thread) ------------------------------------------------------------
    @property
    def speaking(self) -> bool:
        return time.time() < self._play_end

    @property
    def pending(self) -> bool:
        return self._pending is not None

    def start(self) -> None:
        threading.Thread(target=lambda: asyncio.run(self._main()), name="gemini-live", daemon=True).start()

    def stop(self) -> None:
        self._stop.set()

    def send_text(self, text: str, tools: Iterable[str] | dict | None = None) -> None:
        """Start a turn. `tools` limits what the model may call in it - for turns nobody asked for (the
        start-up, announcements), so he can't wander off and change things on his own. A dict maps a tool
        to the actions it may use (None = any)."""
        if tools is not None and not isinstance(tools, dict):
            tools = {name: None for name in tools}
        if self.loop and self.session:
            asyncio.run_coroutine_threadsafe(self._send_text(text, tools), self.loop)

    def announce(self, text: str) -> None:
        self.send_text(f"[Announce] Say this to the user naturally, in your own words, in one short sentence: {text}",
                       tools=())

    def interrupt(self) -> None:
        if self.loop:
            self.loop.call_soon_threadsafe(self._clear_playback)

    def resolve(self, approved: bool) -> bool:
        pending, self._pending = self._pending, None
        if not pending or not self.loop:
            return False
        bus.emit("confirm_done", approved=approved)
        asyncio.run_coroutine_threadsafe(self._finish_pending(pending, approved), self.loop)
        return True

    # ---- session lifecycle -----------------------------------------------------------------
    def _config(self):
        from google.genai import types
        tools = self.registry.all()
        decls = [{"name": t.name, "description": t.description[:1000], "parameters": gemini_schema(t.parameters)}
                 for t in tools]
        system = build_system_prompt(self.settings, self.jarvis.memory, tools)
        system = system.split("Each message from")[0].rstrip()  # that rule is for the typed pipeline
        system += "\n" + LIVE_RULES.format(now=datetime.now().strftime("%A %d %B %Y, %H:%M"))
        cfg = dict(
            response_modalities=["AUDIO"],
            system_instruction=system,
            tools=[{"function_declarations": decls}],
            input_audio_transcription={},
            output_audio_transcription={},
            session_resumption=types.SessionResumptionConfig(handle=self._resume),
            context_window_compression=types.ContextWindowCompressionConfig(sliding_window=types.SlidingWindow()),
            speech_config=types.SpeechConfig(voice_config=types.VoiceConfig(
                prebuilt_voice_config=types.PrebuiltVoiceConfig(voice_name=self.voice))),
        )
        if self.proactive:
            # Lets the model stay silent when speech isn't addressed to it.
            cfg["proactivity"] = types.ProactivityConfig(proactive_audio=True)
        return types.LiveConnectConfig(**cfg)

    async def _main(self) -> None:
        from google import genai
        self.loop = asyncio.get_running_loop()
        self._mic_q = asyncio.Queue(maxsize=60)
        mic = self._open_mic()
        failures = 0
        try:
            while not self._stop.is_set():
                model = self.models[self._model_i]
                client = genai.Client(api_key=secret("GEMINI_API_KEY"),
                                      http_options={"api_version": "v1alpha" if self.proactive else "v1beta"})
                try:
                    await self._session(client, model)
                    failures = 0
                except Exception as exc:
                    if self._stop.is_set():
                        break
                    err = _root(exc)
                    if isinstance(err, _Reconnect):
                        continue
                    msg = f"{type(err).__name__}: {err}"
                    log.warning("Gemini Live (%s) error: %s", model, msg)
                    low = msg.lower()
                    if any(k in low for k in ("api key not valid", "permission_denied", "unauthenticated", "401", "403")):
                        self._fail(f"the Gemini key was rejected ({msg[:120]})")
                        return
                    if self.proactive and ("proactiv" in low or ("invalid_argument" in low and failures == 0)):
                        self.proactive = False  # not supported by this model/API version - carry on without it
                        continue
                    if self._resume and ("resum" in low or "handle" in low):
                        self._resume = None
                        continue
                    if any(k in low for k in ("429", "resource_exhausted", "quota", "not found", "404", "not supported")):
                        if self._model_i + 1 < len(self.models):
                            self._model_i += 1
                            bus.emit("notice", level="warning",
                                     text=f"{model.split('/')[-1]} is unavailable (quota or access) - switching to "
                                          f"{self.models[self._model_i].split('/')[-1]}.")
                            continue
                        self._fail("every Gemini Live model is out of quota for now")
                        return
                    failures += 1
                    if not self.connected.is_set() and failures >= 3:
                        self._fail(msg[:160])
                        return
                    await asyncio.sleep(min(2 ** failures, 20))
        finally:
            mic.stop()
            mic.close()

    def _fail(self, why: str) -> None:
        self.error = why
        self.failed.set()
        self.jarvis.on_live_failed(why)

    def _open_mic(self) -> sd.InputStream:
        loop, q = self.loop, self._mic_q

        def callback(indata, frames, time_info, status):
            audio.set_mic_level(indata[:, 0])
            try:
                loop.call_soon_threadsafe(q.put_nowait, indata.tobytes())
            except Exception:
                pass

        stream = sd.InputStream(samplerate=IN_RATE, channels=1, dtype="int16", blocksize=1600,
                                device=self.in_device, callback=callback)
        stream.start()
        return stream

    async def _session(self, client, model: str) -> None:
        async with client.aio.live.connect(model=model, config=self._config()) as session:
            self.session, self.model = session, model
            first = not self.connected.is_set()
            self.connected.set()
            while not self._mic_q.empty():  # don't send audio that piled up while connecting
                self._mic_q.get_nowait()
            self._play_q = asyncio.Queue()
            if first:
                log.info("Gemini Live connected: %s, voice %s", model, self.voice)
            self.jarvis.set_state("listening")
            try:
                async with asyncio.TaskGroup() as tg:
                    tg.create_task(self._send_mic(session))
                    tg.create_task(self._receive(session))
                    tg.create_task(self._play())
                    tg.create_task(self._watch())
            finally:
                self.session = None

    # ---- the four loops --------------------------------------------------------------------
    async def _send_mic(self, session) -> None:
        from google.genai import types
        while not self._stop.is_set():
            data = await self._mic_q.get()
            if self.jarvis.mic_muted or (self.speaking and not self.barge_in):
                continue
            await session.send_realtime_input(audio=types.Blob(data=data, mime_type=f"audio/pcm;rate={IN_RATE}"))
        raise _Reconnect()

    async def _receive(self, session) -> None:
        while True:
            async for r in session.receive():
                upd = getattr(r, "session_resumption_update", None)
                if upd is not None and getattr(upd, "resumable", False) and getattr(upd, "new_handle", None):
                    self._resume = upd.new_handle
                if getattr(r, "go_away", None) is not None:
                    raise _Reconnect()
                sc = r.server_content
                if sc and sc.input_transcription and sc.input_transcription.text:
                    self._user_buf.append(sc.input_transcription.text)
                    self._user_last = time.time()
                if r.data:
                    await self._output("audio", r.data)
                if sc:
                    if sc.interrupted:
                        self._clear_playback()
                    if sc.output_transcription and sc.output_transcription.text:
                        await self._output("text", sc.output_transcription.text)
                    if sc.turn_complete:
                        await self._turn_complete()
                if r.tool_call:
                    await self._output("tools", r.tool_call)

    # ---- "was that meant for me?" -----------------------------------------------------------
    # The mic is always open, so Gemini hears everything in the room and will happily answer (and
    # act on) a sentence meant for someone else. Its own "proactive audio" setting isn't reliable,
    # so the decision is made here: a reply is only played - and its tools only run - when the
    # utterance named Jarvis, came inside an ongoing conversation, or the turn was started by
    # typed text / the assistant itself. Anything else is dropped silently.
    def _collect_user(self) -> None:
        if not self._user_buf:
            return
        text = re.sub(r"\s+", " ", "".join(self._user_buf)).strip()
        self._user_buf.clear()
        if text:
            self._heard = f"{self._heard} {text}".strip()
            self._heard_at = time.time()
            self._system_turn = False  # a new utterance decides what happens next
            self._auto_tools = None  # ...and the user is back in charge of what gets done

    def _begin_system_turn(self) -> None:
        self._system_turn, self._system_used, self._system_at = True, False, time.time()

    def _decide(self) -> str:
        from .voice.stt import mentions_name
        if self._system_turn and time.time() - self._system_at < 60:
            return "allow"
        now = time.time()
        if self._heard:
            if mentions_name(self._heard) or now < self._conversation_until:
                return "allow"
            return "block"
        return "wait"  # output before its transcript: hold it a moment (see _settle_wait)

    def _apply_decision(self, decision: str) -> None:
        self._turn = decision
        self._last_decision = decision
        heard, self._heard = self._heard, ""
        if decision == "allow":
            self._last_allow = time.time()
            if heard:
                if self._pending and (YES.match(heard) or NO.match(heard)):
                    self.resolve(bool(YES.match(heard)) and not NO.match(heard))
                self.jarvis.on_live_user(heard)
        elif heard:
            log.debug("ignored (not addressed to Jarvis): %s", heard)

    async def _output(self, kind: str, payload) -> None:
        self._collect_user()
        if self._turn is None:
            decision = self._decide()
            if decision == "wait":
                self._turn, self._wait_since = "wait", time.time()
            else:
                self._apply_decision(decision)
        if self._turn == "wait":
            self._held.append((kind, payload))
        elif self._turn == "allow":
            await self._deliver(kind, payload)
        elif kind == "tools":
            await self._reject_calls(payload)

    async def _settle_wait(self, force: bool = False) -> None:
        """Held output waits briefly for the transcript of what was said, then is played or dropped."""
        if self._turn != "wait":
            return
        self._collect_user()
        if not self._heard and not force and time.time() - self._wait_since < 0.8:
            return
        decision = self._decide()
        self._apply_decision("block" if decision == "wait" else decision)
        held, self._held = self._held, []
        for kind, payload in held:
            if self._turn == "allow":
                await self._deliver(kind, payload)
            elif kind == "tools":
                await self._reject_calls(payload)

    async def _deliver(self, kind: str, payload) -> None:
        if kind in ("audio", "text") and self._system_turn:
            self._system_used = True  # the follow-up is being given; once it ends, the pass is spent
        if kind == "audio":
            await self._play_q.put(payload)
        elif kind == "text":
            self._out_buf.append(payload)
            partial = re.sub(r"\s+([,.!?;:'])", r"\1", re.sub(r"\s+", " ", " ".join(self._out_buf))).strip()
            # Gemini writes faster than he speaks, so the caption queues behind the audio generated before it
            # and goes out once that audio has actually been heard - the pop-up stays in step with his voice.
            await self._play_q.put(partial)
        elif kind == "tools":
            replies = [await self._run_tool(fc) for fc in payload.function_calls]
            self._begin_system_turn()  # what the model says next is the follow-up to these results
            await self.session.send_tool_response(function_responses=replies)
            await self._send_images(self.session)

    async def _reject_calls(self, tool_call) -> None:
        from google.genai import types
        await self.session.send_tool_response(function_responses=[
            types.FunctionResponse(id=fc.id, name=fc.name, response={
                "result": "[failed] Not run: that wasn't said to you (the user didn't address Jarvis). "
                          "Stay silent and do nothing."}) for fc in tool_call.function_calls])

    async def _turn_complete(self) -> None:
        await self._settle_wait(force=True)
        if self._turn == "allow":
            self._flush_out()
            self._conversation_until = time.time() + self.conversation_seconds
        else:
            self._out_buf.clear()
        self._turn = None
        if self._system_turn and self._system_used:
            # The follow-up has been said. Without this, the next thing the mic overheard (someone singing)
            # was treated as part of it - and acted on.
            self._system_turn = False
        if self.jarvis.state == "thinking" and not self.speaking and self._play_q.empty():
            self.jarvis.set_state("listening")  # a silent turn mustn't leave the HUD and pop-up "thinking"

    async def _play(self) -> None:
        stream = sd.RawOutputStream(samplerate=OUT_RATE, channels=1, dtype="int16", device=self.out_device)
        stream.start()
        lat = float(getattr(stream, "latency", 0.0) or 0.0)  # sound card delay: written != heard
        try:
            while True:
                item = await self._play_q.get()
                if isinstance(item, str):  # a caption with no audio queued ahead of it
                    self._caption(item, lat)
                    continue
                buf, caption = bytearray(item), None
                while len(buf) < 9600:  # batch up to ~200 ms per write
                    try:
                        nxt = self._play_q.get_nowait()
                    except asyncio.QueueEmpty:
                        break
                    if isinstance(nxt, str):
                        caption = nxt  # its words start right after this batch
                        break
                    buf.extend(nxt)
                dur = len(buf) / 2 / OUT_RATE
                start = max(self._play_end, time.time())
                self._play_end = start + dur
                audio.queue_output_level(np.frombuffer(bytes(buf), np.int16), OUT_RATE, start + lat)
                self.jarvis.set_state("speaking")
                if caption is not None:
                    self._caption(caption, lat)
                await asyncio.to_thread(stream.write, bytes(buf))
        finally:
            stream.stop()
            stream.close()

    def _caption(self, text: str, lat: float) -> None:
        """Tell the pop-up what he's saying, stamped with when the audio queued so far will have been heard."""
        nudge = float(self.settings.get("hud.caption_delay_ms", 0) or 0) / 1000  # fine-tuning for slow headsets
        bus.emit("jarvis_partial", text=text, at=max(self._play_end, time.time()) + lat + nudge)

    async def _watch(self) -> None:
        """Back to 'listening' when he stops talking; let go of overheard speech nobody answered."""
        while True:
            await asyncio.sleep(0.15)
            if self.jarvis.state == "speaking" and not self.speaking and self._play_q.empty():
                self.jarvis.set_state("listening")
            await self._settle_wait()
            if self._turn is None and time.time() - self._user_last > 3.0:
                self._collect_user()
                if self._heard and time.time() - self._heard_at > 3.0:
                    # Nobody answered it: overheard speech. Only a yes/no to a pending question counts.
                    heard, self._heard = self._heard, ""
                    if self._pending and (YES.match(heard) or NO.match(heard)):
                        self.jarvis.on_live_user(heard)
                        self.resolve(bool(YES.match(heard)) and not NO.match(heard))

    # ---- transcripts -------------------------------------------------------------------------

    def _flush_out(self) -> None:
        if not self._out_buf:
            return
        text = re.sub(r"\s+", " ", " ".join(self._out_buf)).strip()
        text = re.sub(r"\s+([,.!?;:'])", r"\1", text)
        self._out_buf.clear()
        if text:
            self.jarvis.on_live_reply(text)

    def _clear_playback(self) -> None:
        if self._play_q is not None:
            while not self._play_q.empty():
                self._play_q.get_nowait()
        self._play_end = time.time()

    # ---- tools ------------------------------------------------------------------------------
    async def _run_tool(self, fc):
        from google.genai import types
        name, args = fc.name, dict(fc.args or {})
        tool = self.registry.get(name)
        key = name + json.dumps(args, sort_keys=True, default=str)
        recent = self._recent.get(key)
        auto = self._auto_tools
        if auto is not None and (name not in auto or (auto[name] is not None and
                                                       str(args.get("action") or "") not in auto[name])):
            log.info("blocked %s(%s) in an automatic turn", name, args.get("action", ""))
            allowed = ", ".join(n if acts is None else f"{n} ({'/'.join(sorted(a for a in acts if a))})"
                                for n, acts in sorted(auto.items())) or "none"
            text = (f"[failed] Not now - the user didn't ask for that. In this automatic turn you may only use: "
                    f"{allowed}. Don't try anything else; just speak.")
        elif recent and name not in REPEATABLE and time.time() - recent[0] < 30:
            text = f"(Already done moments ago - result: {recent[1][:400]}) Don't call it again; reply to the user."
        else:
            question = None
            if tool and tool.confirm:
                try:
                    question = tool.confirm(args)
                except Exception:
                    question = None
            if question:
                # The model never confirms its own actions: it asks, and only the user's "yes" (spoken or
                # the on-screen button) runs the tool.
                self._pending = (name, args, question, time.time())
                bus.emit("confirm", text=question)
                text = (f"[ask] This needs the user's permission first. Ask them: \"{question}\" They can say yes or "
                        "no, or use the on-screen buttons. Don't call the tool again - you'll be told the outcome.")
            else:
                self.jarvis.set_state("thinking")
                self.jarvis.on_tool_start(tool, name, args)
                result = await asyncio.to_thread(self.registry.execute, name, args, lambda q: True)
                self.jarvis.on_tool_end(name, result)
                if result.image is not None:
                    self._images.append((result.image, result.image_mime, str(args.get("question", ""))))
                text = result.text
                self._recent[key] = (time.time(), text)
        return types.FunctionResponse(id=fc.id, name=name, response={"result": text})

    async def _send_images(self, session) -> None:
        while self._images:
            data, mime, question = self._images.pop(0)
            await session.send_client_content(turns={"role": "user", "parts": [
                {"inline_data": {"mime_type": mime, "data": base64.b64encode(data).decode()}},
                {"text": f"[System] Screenshot of the user's screen, as requested. {question}"},
            ]}, turn_complete=True)

    async def _finish_pending(self, pending: tuple, approved: bool) -> None:
        name, args, question, _ts = pending
        if not approved:
            await self._send_text(f"[System] The user declined: \"{question}\". Acknowledge briefly; don't retry.")
            return
        tool = self.registry.get(name)
        self.jarvis.on_tool_start(tool, name, args)
        result = await asyncio.to_thread(self.registry.execute, name, args, lambda q: True)
        self.jarvis.on_tool_end(name, result)
        await self._send_text(f"[System] The user confirmed \"{question}\". Result of {name}: {result.text[:1500]}\n"
                              "Tell them the outcome in a few words.")

    async def _send_text(self, text: str, tools: dict | None | object = _KEEP) -> None:
        if self.session is None:
            return
        self._collect_user()
        self._heard = ""
        self._begin_system_turn()  # we started this turn, so its reply is always played
        if tools is not _KEEP:
            self._auto_tools = tools
        await self.session.send_client_content(turns={"role": "user", "parts": [{"text": text}]}, turn_complete=True)
