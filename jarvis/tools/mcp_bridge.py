"""MCP hooks (client side): connect to the servers in config/mcp_servers.json and give their tools to Jarvis.

Each server runs in its own long-lived task on a private asyncio loop, so connections stay open
and tool calls from Jarvis's worker thread are simply scheduled onto that loop.
"""
from __future__ import annotations

import asyncio
import base64
import json
import logging
import os
import re
import shutil
import threading
from typing import Any

from ..config import CONFIG_DIR
from .registry import Tool, ToolRegistry, ToolResult

log = logging.getLogger(__name__)


def _expand(value: Any) -> Any:
    if isinstance(value, str):
        return os.path.expandvars(os.path.expanduser(value))
    if isinstance(value, list):
        return [_expand(v) for v in value]
    if isinstance(value, dict):
        return {k: _expand(v) for k, v in value.items()}
    return value


def _safe_name(server: str, tool: str) -> str:
    name = re.sub(r"[^a-zA-Z0-9_-]+", "_", f"{server}_{tool}").strip("_")
    return name[:64]


def load_config() -> dict[str, dict]:
    path = CONFIG_DIR / "mcp_servers.json"
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8-sig"))
    return {k: v for k, v in (data.get("mcpServers") or {}).items() if isinstance(v, dict)}


class MCPBridge:
    def __init__(self, registry: ToolRegistry):
        self.registry = registry
        self.status: dict[str, str] = {}
        self._clients: dict[str, Any] = {}
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._loop.run_forever, name="mcp-bridge", daemon=True)
        self._stop: asyncio.Event | None = None
        self._ready = threading.Event()

    def start(self) -> None:
        servers = {n: c for n, c in load_config().items() if c.get("enabled", True)}
        if not servers:
            self._ready.set()
            return
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._main(servers), self._loop)

    def wait_ready(self, timeout: float) -> None:
        self._ready.wait(timeout)

    async def _main(self, servers: dict[str, dict]) -> None:
        self._stop = asyncio.Event()
        pending = {name: asyncio.Event() for name in servers}
        tasks = [asyncio.create_task(self._serve(n, c, pending[n])) for n, c in servers.items()]

        async def all_ready():
            await asyncio.gather(*(e.wait() for e in pending.values()))
            self._ready.set()

        asyncio.create_task(all_ready())
        await asyncio.gather(*tasks, return_exceptions=True)

    async def _serve(self, name: str, cfg: dict, ready: asyncio.Event) -> None:
        from mcp import Client, StdioServerParameters
        cfg = _expand(cfg)
        try:
            if cfg.get("url"):
                target: Any = cfg["url"]
            else:
                command = shutil.which(cfg["command"]) or cfg["command"]
                target = StdioServerParameters(command=command, args=cfg.get("args", []),
                                               env={**os.environ, **cfg.get("env", {})}, cwd=cfg.get("cwd"))
            self.status[name] = "connecting"
            async with Client(target, read_timeout_seconds=float(cfg.get("timeout", 120))) as client:
                tools = (await client.list_tools()).tools
                self._clients[name] = client
                for t in tools:
                    self.registry.add(Tool(
                        name=_safe_name(name, t.name),
                        description=f"[{name}] {t.description or t.name}"[:1000],
                        parameters=t.input_schema or {"type": "object", "properties": {}},
                        func=lambda _s=name, _t=t.name, **kw: self.call(_s, _t, kw),
                        source=f"mcp:{name}",
                    ))
                self.status[name] = f"connected ({len(tools)} tools)"
                log.info("MCP server %s connected with %d tools", name, len(tools))
                ready.set()
                await self._stop.wait()
        except BaseException as e:  # keep one bad server from affecting the rest
            self.status[name] = f"failed: {type(e).__name__}: {e}"[:300]
            log.warning("MCP server %s failed: %s", name, e)
        finally:
            self._clients.pop(name, None)
            ready.set()

    def call(self, server: str, tool: str, arguments: dict) -> ToolResult:
        client = self._clients.get(server)
        if client is None:
            return ToolResult(f"MCP server '{server}' isn't connected.", is_error=True)
        fut = asyncio.run_coroutine_threadsafe(client.call_tool(tool, arguments or {}), self._loop)
        result = fut.result(timeout=180)
        texts, image, mime = [], None, "image/png"
        for block in result.content or []:
            kind = getattr(block, "type", "")
            if kind == "text":
                texts.append(block.text)
            elif kind == "image" and image is None:
                image, mime = base64.b64decode(block.data), block.mime_type
            elif kind:
                texts.append(f"[{kind} content]")
        if not texts and result.structured_content is not None:
            texts.append(json.dumps(result.structured_content, default=str)[:6000])
        from .outcome import untrusted
        text = untrusted(f"{server} MCP server", "\n".join(texts)) if texts else "(no output)"
        return ToolResult(text, image=image, image_mime=mime, is_error=bool(result.is_error))

    def stop(self) -> None:
        if self._stop is not None and self._loop.is_running():
            self._loop.call_soon_threadsafe(self._stop.set)
