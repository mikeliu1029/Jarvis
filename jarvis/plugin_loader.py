"""Drop-in plugins: every plugins/*.py (not starting with "_") is loaded at startup.

A plugin registers tools exactly like the built-in ones (see plugins/_template.py). A plugin
that fails to import is reported and skipped - it never takes Jarvis down. The drop-in model
follows MARK LVI by FatihMakes (CC BY-NC 4.0).
"""
from __future__ import annotations

import importlib.util
import logging
import sys

from .config import PROJECT_DIR
from .tools.registry import ToolRegistry

log = logging.getLogger(__name__)
PLUGIN_DIR = PROJECT_DIR / "plugins"


def load_plugins(registry: ToolRegistry) -> list[tuple[str, bool, str]]:
    """Returns (plugin, loaded, message) for each file found."""
    results = []
    if not PLUGIN_DIR.is_dir():
        return results
    for path in sorted(PLUGIN_DIR.glob("*.py")):
        if path.name.startswith("_"):
            continue  # shared helpers and the template
        before = set(registry.names())
        name = f"jarvis_plugin_{path.stem}"
        try:
            spec = importlib.util.spec_from_file_location(name, path)
            module = importlib.util.module_from_spec(spec)
            sys.modules[name] = module
            spec.loader.exec_module(module)
            added = sorted(set(registry.names()) - before)
            for tool_name in added:
                tool = registry.get(tool_name)
                if tool:
                    tool.source = "builtin"  # validated like built-ins (typed args, confirmations)
            results.append((path.stem, True, f"{len(added)} tool(s): {', '.join(added) or 'none'}"))
            log.info("plugin %s loaded: %s", path.stem, added)
        except Exception as e:
            sys.modules.pop(name, None)
            results.append((path.stem, False, f"{type(e).__name__}: {e}"))
            log.exception("plugin %s failed to load", path.stem)
    return results
