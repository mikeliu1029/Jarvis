"""One shared undo stack: actions register how to reverse themselves.

Idea from MARK LVI by FatihMakes (CC BY-NC 4.0). Rules kept from there:
- never guess: only register an undo when the previous state was actually read;
- never hoard: file contents over 1 MB are not kept;
- undo only covers Jarvis's own actions.
"""
from __future__ import annotations

import logging
import threading
from typing import Callable

log = logging.getLogger(__name__)
MAX_ITEMS = 25
MAX_FILE_BYTES = 1_000_000

_stack: list[tuple[str, Callable[[], str]]] = []
_lock = threading.Lock()


def register(description: str, reverse: Callable[[], str]) -> None:
    with _lock:
        _stack.append((description, reverse))
        del _stack[:-MAX_ITEMS]


def history() -> list[str]:
    with _lock:
        return [d for d, _ in reversed(_stack)]


def undo_last() -> str:
    with _lock:
        if not _stack:
            return ""
        description, reverse = _stack.pop()
    try:
        return reverse() or f"Undid: {description}."
    except Exception as e:
        log.exception("undo failed")
        return f"Couldn't undo '{description}': {e}"
