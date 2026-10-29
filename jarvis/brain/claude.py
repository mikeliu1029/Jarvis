"""Optional cloud brain: Claude via the Anthropic API (set brain.provider = "anthropic").

Notes on how this is wired:
- The conversation is append-only (system prompt and tool list frozen per conversation), which keeps
  the prompt cache warm and keeps Claude's thinking blocks valid across turns.
- Full response content (including thinking blocks) is passed back unchanged in tool loops.
- Server-side refusal fallbacks are switched on for models that support them.
"""
from __future__ import annotations

import base64
import logging

from .base import Brain, Hooks, clean_reply

log = logging.getLogger(__name__)

# Models that accept the server-side `fallbacks: "default"` parameter.
FALLBACK_MODELS = {"claude-opus-5-5", "claude-opus-5", "claude-sonnet-5-5", "claude-fable-5-1"}


class ClaudeBrain(Brain):
    label = "anthropic"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        import anthropic
        self.anthropic = anthropic
        s = self.settings
        self.model = str(s.get("brain.anthropic.model", "claude-opus-5-5"))
        self.effort = str(s.get("brain.anthropic.effort", "low"))
        self.max_tokens = int(s.get("brain.anthropic.max_tokens", 16000))
        self.client = anthropic.Anthropic(timeout=90, max_retries=2)
        self.messages: list[dict] = []
        self._tool_schemas: list[dict] = []

    @property
    def label_long(self) -> str:
        return f"{self.model} (Claude API)"

    def reset_messages(self) -> None:
        self.messages = []
        self._tool_schemas = [t.anthropic_schema() for t in self.tools]

    def check(self) -> tuple[bool, str]:
        from ..config import secret
        if not (secret("ANTHROPIC_API_KEY") or secret("ANTHROPIC_AUTH_TOKEN")):
            return False, "No Claude key - add it in Settings → Getting started → Other services."
        return True, ""

    def _create(self):
        betas = ["thinking-binding-controls-2026-08-01"]
        kwargs = {}
        if self.model in FALLBACK_MODELS:
            betas.append("server-side-fallback-2026-07-01")
            kwargs["fallbacks"] = "default"
        return self.client.beta.messages.create(
            model=self.model,
            max_tokens=self.max_tokens,
            system=[{"type": "text", "text": self.system}],
            tools=self._tool_schemas,
            messages=self.messages,
            output_config={"effort": self.effort},
            thinking={"type": "adaptive", "block_binding": {"prefix_mismatch_behavior": "drop_block"}},
            cache_control={"type": "ephemeral"},
            betas=betas,
            **kwargs,
        )

    @staticmethod
    def _echo_content(content: list) -> list:
        """After a mid-output fallback, drop pre-boundary thinking/tool_use blocks before echoing back."""
        last = max((i for i, b in enumerate(content) if getattr(b, "type", "") == "fallback"), default=-1)
        if last < 0:
            return list(content)
        return [b for i, b in enumerate(content)
                if i > last or getattr(b, "type", "") not in ("thinking", "redacted_thinking", "tool_use", "fallback")]

    def complete(self, prompt: str) -> str:
        resp = self.client.messages.create(
            model=self.model, max_tokens=4000, output_config={"effort": "low"},
            messages=[{"role": "user", "content": prompt}])
        if resp.stop_reason == "refusal":
            return ""
        return "".join(b.text for b in resp.content if b.type == "text").strip()

    def respond(self, text: str, context: str, hooks: Hooks) -> str:
        a = self.anthropic
        self.begin_turn()
        self.ensure_session()
        self.messages.append({"role": "user", "content": self.user_content(text, context)})
        reply = ""
        try:
            for _step in range(self.max_steps):
                if hooks.cancelled():
                    reply = "Stopped, sir."
                    break
                resp = self._create()
                if resp.stop_reason == "refusal":
                    # Drop the unanswered turn so the history stays clean.
                    if self.messages and self.messages[-1]["role"] == "user" and _step == 0:
                        self.messages.pop()
                    reply = "I'm afraid that's one I can't help with, sir."
                    break
                content = self._echo_content(resp.content)
                self.messages.append({"role": "assistant", "content": content})
                if resp.stop_reason == "pause_turn":
                    continue
                tool_uses = [b for b in content if getattr(b, "type", "") == "tool_use"]
                if not tool_uses:
                    reply = clean_reply("".join(b.text for b in content if getattr(b, "type", "") == "text"))
                    break
                results = []
                for tu in tool_uses:
                    result = self.run_tool(tu.name, dict(tu.input or {}), hooks)
                    blocks: list[dict] = [{"type": "text", "text": result.text or "(no output)"}]
                    if result.image is not None:
                        blocks.append({"type": "image", "source": {
                            "type": "base64", "media_type": result.image_mime,
                            "data": base64.b64encode(result.image).decode()}})
                    item = {"type": "tool_result", "tool_use_id": tu.id, "content": blocks}
                    if result.is_error:
                        item["is_error"] = True
                    results.append(item)
                self.messages.append({"role": "user", "content": results})
            else:
                reply = "That took more steps than I'd like, sir. I've stopped there."
        except a.AuthenticationError:
            reply = "My Claude credentials were rejected, sir. Do check the key in Settings."
            self.reset()
        except a.RateLimitError:
            reply = "Claude is rate-limiting us at the moment, sir. Give it a minute."
        except a.BadRequestError as e:
            log.error("Claude rejected the request: %s", e)
            reply = "Claude rejected that request, sir. I've started a fresh conversation."
            self.reset()
        except a.APIStatusError as e:
            log.error("Claude API error %s: %s", e.status_code, e)
            reply = "Claude's servers are having a moment, sir."
        except a.APIConnectionError:
            reply = "I can't reach Claude's servers, sir. Is the internet connection up?"
        reply = reply or "Done, sir."
        self.finish_turn(text, reply)
        return reply
