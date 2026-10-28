"""Paths, settings (config/settings.toml) and secrets (.env)."""
from __future__ import annotations

import os
import threading
import tomllib
from pathlib import Path
from typing import Any

PROJECT_DIR = Path(__file__).resolve().parent.parent
CONFIG_DIR = PROJECT_DIR / "config"
MEMORY_DIR = PROJECT_DIR / "memory"
LOG_DIR = MEMORY_DIR / "logs"

# Heavy, machine-specific files (virtualenv, voice/speech models, caches) live
# outside the project folder so OneDrive doesn't try to sync gigabytes of binaries.
HOME_DIR = Path(os.environ.get("JARVIS_HOME") or (Path.home() / ".jarvis"))
MODELS_DIR = HOME_DIR / "models"
CACHE_DIR = HOME_DIR / "cache"


class Settings:
    """settings.toml with dotted-key lookups, e.g. settings.get("tts.engine")."""

    def __init__(self, data: dict[str, Any]):
        self.data = data

    def get(self, key: str, default: Any = None) -> Any:
        node: Any = self.data
        for part in key.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    def section(self, key: str) -> dict[str, Any]:
        value = self.get(key, {})
        return value if isinstance(value, dict) else {}

    def set(self, key: str, value: Any) -> None:
        """Override a value for this run only (used by command-line flags)."""
        node = self.data
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value


_settings: Settings | None = None
_lock = threading.Lock()


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(PROJECT_DIR / ".env", override=False)


def get_settings() -> Settings:
    global _settings
    with _lock:
        if _settings is None:
            load_env()
            path = CONFIG_DIR / "settings.toml"
            data = tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {}
            _settings = Settings(data)
            for folder in (MEMORY_DIR, LOG_DIR, MODELS_DIR, CACHE_DIR):
                folder.mkdir(parents=True, exist_ok=True)
        return _settings


def secret(name: str) -> str:
    """Read an API key or credential from the environment / .env file."""
    return (os.environ.get(name) or "").strip()
