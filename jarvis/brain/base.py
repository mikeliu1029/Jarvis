"""Shared brain logic: conversation sessions, the per-turn context line and hooks back into Jarvis."""
from __future__ import annotations

import re
import time
from typing import Protocol

from ..config import Settings
from ..memory import Memory
from ..persona import build_system_prompt
from ..tools.registry import Tool, ToolRegistry, ToolResult


class Hooks(Protocol):
    def on_tool_start(self, tool: Tool | None, name: str, args: dict) -> None: ...
    def on_tool_end(self, name: str, result: ToolResult) -> None: ...
    def confirm(self, question: str) -> bool: ...
    def cancelled(self) -> bool: ...


class NullHooks:
    def on_tool_start(self, tool, name, args): pass
    def on_tool_end(self, name, result): pass
    def confirm(self, question): return False
    def cancelled(self): return False


_FAKE_STATUS = re.compile(r"^\s*\[(done|unverified|failed|ask|started)\].*$", re.I | re.M)
_MARKERS = re.compile(r"<<<\s*(END\s+)?UNTRUSTED[^>]*>>>", re.I)


def clean_reply(text: str) -> str:
    text = re.sub(r"(?s)<think>.*?</think>", "", text or "")
    text = text.replace("<think>", "").replace("</think>", "")
    # Status lines and content markers belong to tool results. A small model sometimes imitates
    # them in its own reply ("[done] memory saved ...") - which would be a claimed action that
    # never happened, spoken aloud. Remove them.
    text = _FAKE_STATUS.sub("", text)
    text = _MARKERS.sub("", text)
    text = re.sub(r'^\s*text\s*"\s*', "", text)  # a stray 'text"' the live transcript sometimes starts with
    return re.sub(r"\n{3,}", "\n\n", text).strip()


class Brain:
    """A conversation keeps a frozen system prompt and tool list; it only ever appends messages.

    That keeps the local model's KV cache (and Claude's prompt cache / thinking blocks) valid.
    A fresh conversation starts after a quiet spell or once it grows long, carrying a short recap.
    """

    label = "brain"

    def __init__(self, settings: Settings, registry: ToolRegistry, memory: Memory):
        self.settings, self.registry, self.memory = settings, registry, memory
        self.max_steps = int(settings.get("brain.max_tool_steps", 10))
        self.idle_reset = float(settings.get("brain.new_conversation_after_minutes", 20)) * 60
        self.max_turns = int(settings.get("brain.max_turns_per_conversation", 12))
        self.system = ""
        self.tools: list[Tool] = []
        self.turns = 0
        self.last_used = 0.0
        self.recap: list[tuple[str, str]] = []
        self._carry: list[tuple[str, str]] = []

    # -- sessions --------------------------------------------------------------
    def ensure_session(self) -> None:
        stale = time.time() - self.last_used > self.idle_reset
        if not self.system or stale or self.turns >= self.max_turns:
            self._carry = [] if stale else self.recap[-3:]
            self.tools = self.registry.all()
            self.system = build_system_prompt(self.settings, self.memory, self.tools)
            self.turns = 0
            self.reset_messages()

    def reset(self) -> None:
        self.system = ""
        self.recap.clear()

    def reset_messages(self) -> None:
        raise NotImplementedError

    def user_content(self, text: str, context: str) -> str:
        parts = [context]
        if self._carry:
            recap = " | ".join(f"User: {u} / You: {j}" for u, j in self._carry)
            parts.append(f"(Recap of the conversation so far - {recap})")
            self._carry = []
        parts.append(text)
        return "\n".join(parts)

    # -- repeated-call guard ---------------------------------------------------------
    # A local model sometimes asks for the same tool again instead of answering - which, for a
    # launcher, means opening the app a second, third and fourth time (MARK LVI hit exactly this).
    # A repeat within one turn is answered from the earlier result and never reaches the tool.
    # Input tools are exempt: pressing Tab twice or clicking Next twice is often intended.
    REPEATABLE = {"press_keys", "type_text", "mouse", "click_on_screen", "read_screen", "look_at_screen",
                  "media_control", "windows"}

    def begin_turn(self) -> None:
        self._calls: dict[str, str] = {}

    def run_tool(self, name: str, args: dict, hooks: Hooks) -> ToolResult:
        import json
        key = name + json.dumps(args, sort_keys=True, default=str)
        if name not in self.REPEATABLE and key in getattr(self, "_calls", {}):
            return ToolResult(f"(Already done earlier in this turn - result: {self._calls[key][:600]}) "
                              "Do not call it again; reply to the user now.")
        hooks.on_tool_start(self.registry.get(name), name, args)
        result = self.registry.execute(name, args, confirmer=hooks.confirm)
        hooks.on_tool_end(name, result)
        self._calls[key] = result.text
        return result

    def complete(self, prompt: str) -> str:
        """A one-off answer with no tools and no history (used for session summaries)."""
        raise NotImplementedError

    def finish_turn(self, user_text: str, reply: str) -> None:
        self.turns += 1
        self.last_used = time.time()
        self.recap = (self.recap + [(user_text[:200], reply[:300])])[-6:]

    # -- interface ---------------------------------------------------------------
    def respond(self, text: str, context: str, hooks: Hooks) -> str:
        raise NotImplementedError

    def check(self) -> tuple[bool, str]:
        return True, ""

    def warm_up(self) -> None:
        pass
