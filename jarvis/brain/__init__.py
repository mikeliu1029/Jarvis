"""Pick the brain from settings: local Ollama (default) or Claude."""
from __future__ import annotations

from ..config import Settings
from ..memory import Memory
from ..tools.registry import ToolRegistry
from .base import Brain, Hooks, NullHooks


def make_brain(settings: Settings, registry: ToolRegistry, memory: Memory) -> Brain:
    provider = str(settings.get("brain.provider", "ollama")).lower()
    if provider in ("anthropic", "claude"):
        from .claude import ClaudeBrain
        return ClaudeBrain(settings, registry, memory)
    from .ollama import OllamaBrain
    return OllamaBrain(settings, registry, memory)


__all__ = ["Brain", "Hooks", "NullHooks", "make_brain"]
