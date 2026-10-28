"""Local brain: any tool-calling model served by Ollama (default qwen3-vl:8b, which can also see)."""
from __future__ import annotations

import json
import logging
import re

import requests

from .. import vision
from .base import Brain, Hooks, clean_reply

log = logging.getLogger(__name__)


def _text_tool_calls(content: str, known: set[str]) -> list[dict]:
    """Some models write tool calls as text instead of structured calls - recover them."""
    calls = []
    for blob in re.findall(r"<tool_call>\s*(\{.*?\})\s*</tool_call>", content, re.S) or (
            [content.strip()] if content.strip().startswith("{") else []):
        try:
            data = json.loads(blob)
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("name") in known:
            calls.append({"function": {"name": data["name"], "arguments": data.get("arguments") or {}}})
    return calls


class OllamaBrain(Brain):
    label = "ollama"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        s = self.settings
        # Windows resolves "localhost" to ::1 first while Ollama listens on IPv4 only.
        self.host = str(s.get("brain.ollama.host", "http://127.0.0.1:11434")).rstrip("/").replace(
            "://localhost", "://127.0.0.1")
        self.model = str(s.get("brain.ollama.model", "qwen3-vl:8b"))
        self.num_ctx = int(s.get("brain.ollama.num_ctx", 16384))
        self.keep_alive = s.get("brain.ollama.keep_alive", "30m")
        self.temperature = float(s.get("brain.ollama.temperature", 0.6))
        self.think = s.get("brain.ollama.think", None)
        self.http = requests.Session()
        self.http.trust_env = False  # a system-wide proxy must not intercept calls to this same machine
        self.messages: list[dict] = []

    @property
    def label_long(self) -> str:
        return f"{self.model} (local, Ollama)"

    def reset_messages(self) -> None:
        self.messages = []

    def check(self) -> tuple[bool, str]:
        try:
            tags = self.http.get(self.host + "/api/tags", timeout=4).json().get("models", [])
        except requests.RequestException:
            return False, "Ollama isn't running. Start it from the Start menu (or run 'ollama serve')."
        names = {m.get("name") for m in tags} | {m.get("model") for m in tags}
        if self.model not in names and f"{self.model}:latest" not in names:
            return False, f"The model '{self.model}' isn't downloaded. Run: ollama pull {self.model}"
        return True, ""

    def warm_up(self) -> None:
        """Load the model into GPU memory so the first reply is quick."""
        try:
            self.http.post(self.host + "/api/chat", timeout=180, json={
                "model": self.model, "messages": [], "keep_alive": self.keep_alive,
                "options": {"num_ctx": self.num_ctx}})
        except requests.RequestException:
            pass

    def _chat(self, tools: list[dict] | None) -> dict:
        body = {"model": self.model, "messages": [{"role": "system", "content": self.system}] + self.messages,
                "stream": False, "keep_alive": self.keep_alive,
                "options": {"num_ctx": self.num_ctx, "temperature": self.temperature}}
        if tools:
            body["tools"] = tools
        if self.think is not None:
            body["think"] = self.think
        r = self.http.post(self.host + "/api/chat", json=body, timeout=300)
        if r.status_code == 400 and "think" in body:
            body.pop("think")
            r = self.http.post(self.host + "/api/chat", json=body, timeout=300)
        if r.status_code >= 400:
            raise RuntimeError(f"Ollama error {r.status_code}: {r.text[:300]}")
        return r.json().get("message", {})

    def complete(self, prompt: str) -> str:
        r = self.http.post(self.host + "/api/chat", timeout=120, json={
            "model": self.model, "messages": [{"role": "user", "content": prompt}], "stream": False,
            "keep_alive": self.keep_alive, "options": {"num_ctx": self.num_ctx, "temperature": 0.3}})
        r.raise_for_status()
        return clean_reply(r.json().get("message", {}).get("content", ""))

    def respond(self, text: str, context: str, hooks: Hooks) -> str:
        self.begin_turn()
        self.ensure_session()
        self.messages.append({"role": "user", "content": self.user_content(text, context)})
        schemas = [t.ollama_schema() for t in self.tools]
        known = {t.name for t in self.tools}
        used_tool, nudged = False, False
        reply = ""
        for _step in range(self.max_steps + 1):
            if hooks.cancelled():
                reply = "Stopped, sir."
                break
            last_chance = _step == self.max_steps
            msg = self._chat(None if last_chance else schemas)
            content = clean_reply(msg.get("content", ""))
            calls = msg.get("tool_calls") or ([] if last_chance else _text_tool_calls(content, known))
            if not calls:
                if not content and not nudged and not last_chance:  # thinking models occasionally return nothing
                    nudged = True
                    self.messages.append({"role": "user", "content": "(Please reply to me now, briefly.)"})
                    continue
                reply = content or ("Done, sir." if used_tool else "I'm afraid I lost my train of thought, sir.")
                self.messages.append({"role": "assistant", "content": reply})
                break
            self.messages.append({"role": "assistant", "content": content, "tool_calls": calls})
            for call in calls:
                fn = call.get("function", {})
                name = fn.get("name", "")
                args = fn.get("arguments") or {}
                if isinstance(args, str):
                    try:
                        args = json.loads(args)
                    except ValueError:
                        args = {}
                result = self.run_tool(name, args, hooks)
                used_tool = True
                out = result.text
                if result.image is not None:  # describe screenshots with the vision model
                    question = args.get("question") or "Describe what is on the screen."
                    try:
                        out = "What the screen shows: " + vision.describe(result.image, question)
                    except Exception as e:
                        out = f"Couldn't analyse the screenshot ({e})."
                tool_msg = {"role": "tool", "content": out, "tool_name": name}
                if call.get("id"):
                    tool_msg["tool_call_id"] = call["id"]
                self.messages.append(tool_msg)
        self.finish_turn(text, reply)
        return reply
