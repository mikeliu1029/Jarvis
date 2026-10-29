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

# settings.toml holds the defaults that ship with Jarvis. Whatever the user changes in the Settings window
# goes here instead - a private file, never uploaded, that wins over the defaults.
USER_SETTINGS = CONFIG_DIR / "my_settings.json"
ENV_FILE = PROJECT_DIR / ".env"


def load_env() -> None:
    try:
        from dotenv import load_dotenv
    except ImportError:
        return
    load_dotenv(ENV_FILE, override=False)


def _merge(base: dict, over: dict) -> dict:
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(base.get(k), dict):
            _merge(base[k], v)
        else:
            base[k] = v
    return base


def user_overrides() -> dict:
    import json
    try:
        data = json.loads(USER_SETTINGS.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def default_settings() -> Settings:
    path = CONFIG_DIR / "settings.toml"
    return Settings(tomllib.loads(path.read_text(encoding="utf-8-sig")) if path.exists() else {})


def get_settings() -> Settings:
    global _settings
    with _lock:
        if _settings is None:
            load_env()
            _settings = Settings(_merge(default_settings().data, user_overrides()))
            for folder in (MEMORY_DIR, LOG_DIR, MODELS_DIR, CACHE_DIR):
                folder.mkdir(parents=True, exist_ok=True)
        return _settings


def save_user_setting(key: str, value: Any) -> None:
    """Remember a choice made in the Settings window (and use it right away where Jarvis reads it live)."""
    import json
    with _lock:
        data = user_overrides()
        node = data
        parts = key.split(".")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
        tmp = USER_SETTINGS.with_suffix(".tmp")
        tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(USER_SETTINGS)
    if _settings is not None:
        _settings.set(key, value)


def secret(name: str) -> str:
    """Read an API key or credential from the environment / .env file."""
    return (os.environ.get(name) or "").strip()


def set_secret(name: str, value: str) -> None:
    """Store an API key in .env (on this PC only) and use it from now on."""
    from dotenv import set_key, unset_key
    value = (value or "").strip()
    if not ENV_FILE.exists():
        ENV_FILE.write_text("# Keys and passwords for Jarvis - set them in Jarvis's Settings window.\n",
                            encoding="utf-8")
    if value:
        set_key(str(ENV_FILE), name, value, quote_mode="never")
        os.environ[name] = value
    else:
        unset_key(str(ENV_FILE), name)
        os.environ.pop(name, None)
