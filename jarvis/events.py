"""A tiny publish/subscribe bus so the console, HUD and logs all see the same events.

Event types: state, user, jarvis, tool, notice, confirm.
"""
from __future__ import annotations

import logging
import threading
import time
from collections import deque
from typing import Any, Callable

log = logging.getLogger(__name__)

Event = dict[str, Any]
TRANSIENT = {"jarvis_partial"}  # many per second while he talks; not worth keeping in the history


class EventBus:
    def __init__(self, history: int = 80):
        self._subscribers: list[Callable[[Event], None]] = []
        self._lock = threading.Lock()
        self.history: deque[Event] = deque(maxlen=history)

    def subscribe(self, fn: Callable[[Event], None]) -> None:
        with self._lock:
            self._subscribers.append(fn)

    def unsubscribe(self, fn: Callable[[Event], None]) -> None:
        with self._lock:
            if fn in self._subscribers:
                self._subscribers.remove(fn)

    def emit(self, kind: str, **data: Any) -> Event:
        event: Event = {"type": kind, "ts": time.time(), **data}
        with self._lock:
            subscribers = list(self._subscribers)
            if kind not in TRANSIENT:
                self.history.append(event)
        for fn in subscribers:
            try:
                fn(event)
            except Exception:  # a broken subscriber must never take Jarvis down
                log.exception("event subscriber failed")
        return event


bus = EventBus()
