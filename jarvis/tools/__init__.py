"""Jarvis's tools. Importing a module registers its tools with the shared registry."""
from .registry import P, Tool, ToolRegistry, ToolResult, registry

_loaded = False


def load_builtin_tools() -> ToolRegistry:
    global _loaded
    if not _loaded:
        from . import (apps, briefing, files, gservices, keyboard_mouse, news,  # noqa: F401
                       productivity, screen, system, weather, web)
        _loaded = True
    return registry


__all__ = ["P", "Tool", "ToolRegistry", "ToolResult", "registry", "load_builtin_tools"]
