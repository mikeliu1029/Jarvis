"""Tool registry: every capability Jarvis has is a Tool with a JSON schema.

The same registry feeds the local brain (Ollama), the Claude brain, and the MCP server.
"""
from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from typing import Any, Callable

log = logging.getLogger(__name__)

MAX_RESULT_CHARS = 8000


@dataclass
class ToolResult:
    text: str
    image: bytes | None = None       # screenshot for vision-capable brains
    image_mime: str = "image/jpeg"
    is_error: bool = False


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    func: Callable[..., Any]
    confirm: Callable[[dict], str | None] | None = None  # returns a question when the user must approve
    slow: bool = False        # Jarvis says "one moment" before running it
    source: str = "builtin"   # "builtin" or "mcp:<server>"

    def ollama_schema(self) -> dict:
        return {"type": "function", "function": {"name": self.name, "description": self.description,
                                                 "parameters": self.parameters}}

    def anthropic_schema(self) -> dict:
        return {"name": self.name, "description": self.description, "input_schema": self.parameters}


def P(type_: str, description: str, *, required: bool = True, enum: list | None = None) -> dict:
    """Compact parameter spec used by @registry.tool."""
    spec: dict[str, Any] = {"type": type_, "description": description, "_required": required}
    if enum:
        spec["enum"] = enum
    if type_ == "array":
        spec["items"] = {"type": "string"}
    return spec


def _schema(params: dict[str, dict] | None) -> dict:
    props, required = {}, []
    for name, spec in (params or {}).items():
        spec = dict(spec)
        if spec.pop("_required", True):
            required.append(name)
        props[name] = spec
    schema: dict[str, Any] = {"type": "object", "properties": props}
    if required:
        schema["required"] = required
    return schema


def _coerce(value: Any, spec: dict) -> Any:
    """Local models sometimes send '5' for 5 or 'true' for True - fix that up."""
    kind = spec.get("type")
    try:
        if kind == "boolean" and isinstance(value, str):
            return value.strip().lower() in {"true", "yes", "1", "on", "y"}
        if kind == "integer" and not isinstance(value, bool) and value is not None:
            return int(float(value))
        if kind == "number" and not isinstance(value, bool) and value is not None:
            return float(value)
        if kind == "string" and value is not None and not isinstance(value, str):
            return json.dumps(value) if isinstance(value, (dict, list)) else str(value)
        if kind == "array" and isinstance(value, str):
            try:
                parsed = json.loads(value)
                return parsed if isinstance(parsed, list) else [value]
            except ValueError:
                return [value]
    except (TypeError, ValueError):
        pass
    return value


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, Tool] = {}
        self._lock = threading.RLock()

    def add(self, tool: Tool) -> None:
        with self._lock:
            self._tools[tool.name] = tool

    def tool(self, name: str, description: str, params: dict | None = None, *,
             confirm: Callable[[dict], str | None] | None = None, slow: bool = False):
        def decorator(fn):
            self.add(Tool(name, description, _schema(params), fn, confirm, slow))
            return fn
        return decorator

    def get(self, name: str) -> Tool | None:
        with self._lock:
            tool = self._tools.get(name)
            if tool is None:  # tolerate mangled names from small models
                lowered = name.lower().replace("-", "_").split(".")[-1]
                tool = next((t for n, t in self._tools.items() if n.lower() == lowered), None)
            return tool

    def all(self) -> list[Tool]:
        with self._lock:
            return list(self._tools.values())

    def names(self) -> list[str]:
        with self._lock:
            return sorted(self._tools)

    def execute(self, name: str, args: Any, confirmer: Callable[[str], bool] | None = None) -> ToolResult:
        from ..winutil import ensure_com
        ensure_com()

        tool = self.get(name)
        if tool is None:
            return ToolResult(f"Error: there is no tool called '{name}'. Available: {', '.join(self.names())}",
                              is_error=True)
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except ValueError:
                args = {}
        if not isinstance(args, dict):
            args = {}

        if tool.source == "builtin":
            props = tool.parameters.get("properties", {})
            clean = {k: _coerce(v, props[k]) for k, v in args.items() if k in props and v is not None}
            missing = [r for r in tool.parameters.get("required", []) if clean.get(r) in (None, "")]
            if missing:
                return ToolResult(f"Error: {name} needs {', '.join(missing)}.", is_error=True)
        else:
            clean = args

        if tool.confirm:
            question = tool.confirm(clean)
            if question and (confirmer is None or not confirmer(question)):
                return ToolResult("The user declined (or didn't confirm). Don't retry; acknowledge briefly.",
                                  is_error=True)
        try:
            out = tool.func(**clean)
        except Exception as e:
            if type(e).__name__ == "FailSafeException":
                return ToolResult("Aborted: the mouse fail-safe was triggered (pointer in a screen corner).",
                                  is_error=True)
            log.exception("tool %s failed", name)
            return ToolResult(f"Error: {type(e).__name__}: {e}", is_error=True)

        result = out if isinstance(out, ToolResult) else ToolResult("Done." if out is None else str(out))
        if len(result.text) > MAX_RESULT_CHARS:
            result.text = result.text[:MAX_RESULT_CHARS] + "\n...(truncated)"
        return result


registry = ToolRegistry()
