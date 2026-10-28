"""MCP hooks (server side): expose Jarvis's desktop tools to other AI apps over MCP (stdio).

Add to Claude Code:
    claude mcp add jarvis -- "%USERPROFILE%\\.jarvis\\venv\\Scripts\\python.exe" "<this folder>\\mcp_server.py"
Or Claude Desktop's claude_desktop_config.json:
    "jarvis": {"command": "C:\\Users\\YOU\\.jarvis\\venv\\Scripts\\python.exe", "args": ["<this folder>\\mcp_server.py"]}

Tools that normally ask for confirmation (commands, power, killing processes, overwriting files) are
left out unless [mcp_server] allow_confirmed_tools = true, because the MCP host does the approving.
"""
from __future__ import annotations

import base64
import logging
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import anyio  # noqa: E402
import mcp.types as types  # noqa: E402
from mcp.server.lowlevel import Server  # noqa: E402
from mcp.server.stdio import stdio_server  # noqa: E402

from jarvis import winutil  # noqa: E402
from jarvis.config import LOG_DIR, get_settings  # noqa: E402
from jarvis.tools import load_builtin_tools  # noqa: E402


def build_server() -> Server:
    winutil.set_dpi_aware()
    registry = load_builtin_tools()
    allow = bool(get_settings().get("mcp_server.allow_confirmed_tools", False))
    exposed = {t.name: t for t in registry.all() if allow or t.confirm is None}

    async def list_tools(ctx, params) -> types.ListToolsResult:
        return types.ListToolsResult(tools=[
            types.Tool(name=t.name, description=t.description, input_schema=t.parameters) for t in exposed.values()])

    async def call_tool(ctx, params: types.CallToolRequestParams) -> types.CallToolResult:
        if params.name not in exposed:
            return types.CallToolResult(content=[types.TextContent(type="text", text=f"Unknown tool {params.name}")],
                                        is_error=True)
        result = await anyio.to_thread.run_sync(
            lambda: registry.execute(params.name, params.arguments or {}, confirmer=lambda q: True))
        content: list = [types.TextContent(type="text", text=result.text)]
        if result.image is not None:
            content.append(types.ImageContent(type="image", data=base64.b64encode(result.image).decode(),
                                              mime_type=result.image_mime))
        return types.CallToolResult(content=content, is_error=result.is_error)

    return Server("jarvis", version="1.0.0", instructions="Desktop control, screen reading, news, weather, "
                  "web search, files and reminders on the user's Windows PC.",
                  on_list_tools=list_tools, on_call_tool=call_tool)


async def main() -> None:
    server = build_server()
    async with stdio_server() as (read, write):
        await server.run(read, write, server.create_initialization_options())


if __name__ == "__main__":
    # stdout carries the MCP protocol, so logs go to a file only.
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(filename=LOG_DIR / "mcp_server.log", level=logging.INFO)
    anyio.run(main)
