"""Persistent memory, all plain JSON in the project's memory/ folder:

  long_term.json  what Jarvis knows about you, by category and key (nothing is silently dropped)
  tasks.json      to-do list
  reminders.json  spoken reminders
  monitors.json   news topics Jarvis watches for you
  state.json      small bits of state (last briefing, last session summary, ...)
  logs/           one conversation log per day

The long-term layout - categories, recall on demand, a prompt that carries a core plus an
index of everything else - follows MARK LVI by FatihMakes (CC BY-NC 4.0).
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any

from .config import MEMORY_DIR

log = logging.getLogger(__name__)

CATEGORIES = ("identity", "preferences", "projects", "relationships", "wishes", "notes")


def _read_json(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default
    except Exception:
        log.exception("could not read %s; starting fresh (old copy kept as .bad)", path)
        try:
            path.replace(path.with_suffix(path.suffix + ".bad"))
        except OSError:
            pass
        return default


def _write_json(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding="utf-8")
    for _ in range(10):  # OneDrive can hold a file for a moment while syncing
        try:
            os.replace(tmp, path)
            return
        except PermissionError:
            time.sleep(0.2)
    log.warning("could not save %s (file locked)", path)


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _key(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:48] or "note"


def _match(items: list[dict], ref: Any, *, open_only: bool = False) -> dict | None:
    pool = [i for i in items if not (open_only and i.get("done"))]
    ref_s = str(ref).strip().lower().lstrip("#")
    if ref_s.isdigit():
        for item in pool:
            if item.get("id") == int(ref_s):
                return item
    for item in pool:
        text = str(item.get("text") or item.get("message") or "").lower()
        if ref_s and ref_s in text:
            return item
    return None


class Memory:
    def __init__(self, root: Path = MEMORY_DIR):
        self.root = root
        (root / "logs").mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._lt = _read_json(root / "long_term.json", {})
        for c in CATEGORIES:
            self._lt.setdefault(c, {})
        self._migrate_profile()
        self._tasks = _read_json(root / "tasks.json", {"next_id": 1, "items": []})
        self._reminders = _read_json(root / "reminders.json", {"next_id": 1, "items": []})
        self._monitors = _read_json(root / "monitors.json", {"topics": {}})
        self._state = _read_json(root / "state.json", {})

    def _migrate_profile(self) -> None:
        """Earlier builds kept a flat list of facts in profile.json."""
        old = self.root / "profile.json"
        data = _read_json(old, None)
        if isinstance(data, dict) and isinstance(data.get("facts"), list):
            for f in data["facts"]:
                text = f.get("text") if isinstance(f, dict) else str(f)
                if text:
                    self._lt["notes"][_key(text)] = {"value": text, "updated": _now()}
            _write_json(self.root / "long_term.json", self._lt)
            try:
                old.replace(old.with_name("profile.migrated.json"))
            except OSError:
                pass

    # ---- long-term memory --------------------------------------------------------------
    def save(self, category: str, key: str, value: str) -> str:
        category = category if category in CATEGORIES else "notes"
        key, value = _key(key or value), value.strip().rstrip(".")
        if not value:
            return "Nothing to remember."
        with self._lock:
            old = self._lt[category].get(key, {}).get("value")
            self._lt[category][key] = {"value": value, "updated": _now()}
            _write_json(self.root / "long_term.json", self._lt)
        return f"Updated {category}/{key}: {value} (was: {old})." if old and old != value \
            else f"Saved {category}/{key}: {value}."

    def entries(self) -> list[dict]:
        with self._lock:
            rows = [{"category": c, "key": k, "value": v.get("value", ""), "updated": v.get("updated", "")}
                    for c in CATEGORIES for k, v in self._lt.get(c, {}).items()]
        return sorted(rows, key=lambda r: r["updated"], reverse=True)

    def recall(self, query: str = "", limit: int = 10) -> list[dict]:
        rows = self.entries()
        words = [w for w in re.split(r"\W+", query.lower()) if len(w) > 1]
        if not words:
            return rows[:limit * 3]
        scored = []
        for r in rows:
            hay = f"{r['category']} {r['key'].replace('_', ' ')} {r['value']}".lower()
            score = sum(1 for w in words if w in hay)
            if score:
                scored.append((score, r))
        scored.sort(key=lambda s: (s[0], s[1]["updated"]), reverse=True)
        return [r for _, r in scored[:limit]]

    def forget(self, query: str, category: str = "") -> list[str]:
        q = query.lower().strip()
        removed = []
        with self._lock:
            for c in ([category] if category in CATEGORIES else CATEGORIES):
                for k in list(self._lt[c]):
                    v = self._lt[c][k].get("value", "")
                    if q and (q == k or _key(q) == k or q in v.lower() or q in k.replace("_", " ")):
                        removed.append(f"{k.replace('_', ' ')}: {v}")
                        del self._lt[c][k]
            if removed:
                _write_json(self.root / "long_term.json", self._lt)
        return removed

    def prompt_block(self, budget: int = 1400) -> str:
        """Identity in full, then the newest facts within budget, then an index of the rest.

        The index matters: a model can't look up something it doesn't know exists."""
        rows = self.entries()
        if not rows:
            return "- Nothing yet."
        lines, used, shown = [], 0, set()
        for r in sorted((r for r in rows if r["category"] == "identity"), key=lambda r: r["key"]):
            line = f"- {r['key'].replace('_', ' ')}: {r['value']}"
            lines.append(line)
            used += len(line)
            shown.add((r["category"], r["key"]))
        for r in rows:
            if (r["category"], r["key"]) in shown:
                continue
            line = f"- ({r['category']}) {r['key'].replace('_', ' ')}: {r['value']}"
            if used + len(line) > budget:
                continue
            lines.append(line)
            used += len(line)
            shown.add((r["category"], r["key"]))
        rest = [r for r in rows if (r["category"], r["key"]) not in shown]
        if rest:
            # interleave categories so one big category can't crowd the others out of the index
            by_cat = {c: [r for r in rest if r["category"] == c] for c in CATEGORIES}
            order, i = [], 0
            while len(order) < len(rest):
                for c in CATEGORIES:
                    if i < len(by_cat[c]):
                        order.append(by_cat[c][i])
                i += 1
            names = ", ".join(r["key"].replace("_", " ") for r in order[:60])
            lines.append(f"[ALSO REMEMBERED - look up with memory(action='recall')]: {names}")
        return "\n".join(lines)

    # ---- tasks -----------------------------------------------------------------
    def add_task(self, text: str) -> dict:
        with self._lock:
            item = {"id": self._tasks["next_id"], "text": text.strip(), "done": False, "added": _now()}
            self._tasks["next_id"] += 1
            self._tasks["items"].append(item)
            _write_json(self.root / "tasks.json", self._tasks)
            return item

    def tasks(self, include_done: bool = False) -> list[dict]:
        with self._lock:
            return [dict(t) for t in self._tasks["items"] if include_done or not t.get("done")]

    def complete_task(self, ref: Any) -> dict | None:
        with self._lock:
            item = _match(self._tasks["items"], ref, open_only=True)
            if item:
                item["done"] = True
                item["completed"] = _now()
                _write_json(self.root / "tasks.json", self._tasks)
            return item

    def remove_task(self, ref: Any) -> dict | None:
        with self._lock:
            item = _match(self._tasks["items"], ref)
            if item:
                self._tasks["items"].remove(item)
                _write_json(self.root / "tasks.json", self._tasks)
            return item

    # ---- reminders -------------------------------------------------------------
    def add_reminder(self, message: str, due: float) -> dict:
        with self._lock:
            item = {"id": self._reminders["next_id"], "message": message.strip(), "due": due, "fired": False}
            self._reminders["next_id"] += 1
            self._reminders["items"].append(item)
            _write_json(self.root / "reminders.json", self._reminders)
            return item

    def pending_reminders(self) -> list[dict]:
        with self._lock:
            return sorted((dict(r) for r in self._reminders["items"] if not r["fired"]), key=lambda r: r["due"])

    def pop_due_reminders(self, now: float | None = None) -> list[dict]:
        now = now or time.time()
        with self._lock:
            due = [r for r in self._reminders["items"] if not r["fired"] and r["due"] <= now]
            if due:
                for r in due:
                    r["fired"] = True
                self._reminders["items"] = [r for r in self._reminders["items"]
                                            if not r["fired"] or r["due"] > now - 86400]
                _write_json(self.root / "reminders.json", self._reminders)
            return [dict(r) for r in due]

    def cancel_reminder(self, ref: Any) -> dict | None:
        with self._lock:
            item = _match([r for r in self._reminders["items"] if not r["fired"]], ref)
            if item:
                self._reminders["items"].remove(item)
                _write_json(self.root / "reminders.json", self._reminders)
            return item

    # ---- monitored news topics ---------------------------------------------------------
    def monitors(self) -> dict[str, dict]:
        with self._lock:
            return {k: dict(v) for k, v in self._monitors["topics"].items()}

    def add_monitor(self, topic: str) -> bool:
        topic = topic.strip()
        with self._lock:
            if topic.lower() in (t.lower() for t in self._monitors["topics"]):
                return False
            self._monitors["topics"][topic] = {"added": _now(), "last_check": 0, "seen": []}
            _write_json(self.root / "monitors.json", self._monitors)
            return True

    def remove_monitor(self, topic: str) -> str | None:
        with self._lock:
            for t in list(self._monitors["topics"]):
                if topic.lower() in t.lower():
                    del self._monitors["topics"][t]
                    _write_json(self.root / "monitors.json", self._monitors)
                    return t
        return None

    def update_monitor(self, topic: str, seen: list[str]) -> None:
        with self._lock:
            if topic in self._monitors["topics"]:
                self._monitors["topics"][topic]["last_check"] = time.time()
                self._monitors["topics"][topic]["seen"] = seen[-200:]
                _write_json(self.root / "monitors.json", self._monitors)

    # ---- session summaries -----------------------------------------------------------
    def save_session(self, summary: str) -> None:
        self.set("last_session", {"date": datetime.now().strftime("%Y-%m-%d"), "summary": summary.strip(),
                                  "mentioned": False})

    def last_session(self) -> dict | None:
        """The most recent session note - kept, so 'what were we doing last time?' can always be answered."""
        return self.get("last_session")

    def unmentioned_session(self) -> dict | None:
        """The last session note if it hasn't been brought up yet; marks it as mentioned (greeted once)."""
        with self._lock:
            last = self._state.get("last_session")
            if not last or last.get("mentioned"):
                return None
            last["mentioned"] = True
            _write_json(self.root / "state.json", self._state)
            return dict(last)

    def pop_last_session(self) -> dict | None:
        return self.unmentioned_session()

    # ---- state -----------------------------------------------------------------
    def get(self, key: str, default: Any = None) -> Any:
        with self._lock:
            return self._state.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._state[key] = value
            _write_json(self.root / "state.json", self._state)

    # ---- conversation / event log ---------------------------------------------
    def log(self, kind: str, **data: Any) -> None:
        entry = {"ts": _now(), "type": kind, **data}
        path = self.root / "logs" / f"{datetime.now():%Y-%m-%d}.jsonl"
        try:
            with self._lock, open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps(entry, ensure_ascii=False, default=str) + "\n")
        except OSError:
            log.warning("could not write conversation log")


_memory: Memory | None = None


def get_memory() -> Memory:
    global _memory
    if _memory is None:
        _memory = Memory()
    return _memory
