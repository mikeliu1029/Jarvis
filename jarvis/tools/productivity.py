"""Long-term memory, to-do list, reminders - plus the iCal / IMAP fallbacks used when Google isn't connected."""
from __future__ import annotations

import email
import email.header
import imaplib
import re
import time
from datetime import date, datetime, timedelta

import requests

from .. import undo as undo_stack
from ..config import secret
from ..memory import CATEGORIES, get_memory
from . import outcome
from .registry import P, registry


@registry.tool(
    "memory",
    "Long-term memory about the user. save: quietly store a lasting fact whenever the user reveals their name, "
    "job, city, preferences, people in their life, projects or plans (category + short snake_case key + value, "
    "value in English). recall: look up stored facts by keyword (empty query lists everything) - use it before "
    "saying you don't know something personal. forget: delete facts matching the query.",
    {"action": P("string", "save, recall or forget", enum=["save", "recall", "forget"]),
     "category": P("string", "For save", required=False, enum=list(CATEGORIES)),
     "key": P("string", "For save: short key, e.g. favourite_drink, sister_name", required=False),
     "value": P("string", "For save: the fact, e.g. 'black coffee'", required=False),
     "query": P("string", "For recall/forget: what to look for", required=False)},
)
def memory(action: str, category: str = "notes", key: str = "", value: str = "", query: str = "") -> str:
    mem = get_memory()
    if action == "save":
        if not value and query:
            value = query
        return outcome.done(mem.save(category or "notes", key or value, value)) if value \
            else outcome.failed("Nothing to save - give a value.")
    if action == "forget":
        removed = mem.forget(query or key or value)
        return outcome.done(f"Forgot: {'; '.join(removed)}.") if removed \
            else outcome.failed(f"Nothing remembered matches '{query or key}'.")
    rows = mem.recall(query or key or value)
    if not rows:
        return f"Nothing stored matches '{query}'." if query else "Nothing remembered yet."
    return "Stored facts:\n" + "\n".join(
        f"- ({r['category']}) {r['key'].replace('_', ' ')}: {r['value']}  [{r['updated'][:10]}]" for r in rows)


@registry.tool(
    "undo",
    "Reverse the last change YOU made on this PC (a file you wrote, the volume or dark mode you changed). Use when "
    "the user says undo, revert, put it back or that you did the wrong thing. action=list shows what can be undone.",
    {"action": P("string", "undo (default) or list", required=False, enum=["undo", "list"])},
)
def undo(action: str = "undo") -> str:
    if action == "list":
        items = undo_stack.history()
        return ("Things I can undo, most recent first:\n" + "\n".join(f"{i + 1}. {t}" for i, t in enumerate(items))
                if items else "I haven't changed anything I can undo yet.")
    result = undo_stack.undo_last()
    return outcome.done(result) if result else outcome.failed("There is nothing of mine to undo.")


@registry.tool(
    "monitor",
    "Watch a news topic for the user: Jarvis checks it a few times a day and mentions genuinely new headlines. "
    "add: start watching ('keep an eye on Nvidia'); remove: stop; list: what's being watched.",
    {"action": P("string", "add, remove or list", enum=["add", "remove", "list"]),
     "topic": P("string", "The topic, e.g. 'SpaceX Starship' or 'Arsenal'", required=False)},
)
def monitor(action: str, topic: str = "") -> str:
    mem = get_memory()
    if action == "add":
        if not topic:
            return outcome.ask("Which topic should I watch?")
        return outcome.done(f"Now watching '{topic}'. I'll mention new headlines when they appear.") \
            if mem.add_monitor(topic) else outcome.done(f"Already watching '{topic}'.")
    if action == "remove":
        gone = mem.remove_monitor(topic)
        return outcome.done(f"Stopped watching '{gone}'.") if gone else outcome.failed(f"I'm not watching '{topic}'.")
    topics = list(mem.monitors())
    return "Watching: " + ", ".join(topics) if topics else "No topics are being watched."


@registry.tool(
    "tasks",
    "The user's to-do list: add a task, list open tasks, complete or remove one (by number or words).",
    {"action": P("string", "add, list, complete or remove", enum=["add", "list", "complete", "remove"]),
     "text": P("string", "Task text (add) or number/words identifying it (complete/remove)", required=False)},
)
def tasks(action: str, text: str = "") -> str:
    mem = get_memory()
    if action == "add":
        if not text:
            return "Error: what's the task?"
        item = mem.add_task(text)
        return f"Added task #{item['id']}: {item['text']}."
    if action in ("complete", "remove"):
        item = mem.complete_task(text) if action == "complete" else mem.remove_task(text)
        return f"{'Completed' if action == 'complete' else 'Removed'} #{item['id']}: {item['text']}." if item \
            else f"No open task matches '{text}'."
    items = mem.tasks()
    return "Open tasks:\n" + "\n".join(f"#{t['id']} {t['text']}" for t in items) if items else "No open tasks."


def parse_when(minutes: float | None = None, at: str = "") -> float | None:
    now = datetime.now()
    if minutes is not None:
        return now.timestamp() + float(minutes) * 60
    s = at.strip().lower()
    if not s:
        return None
    day = now.date()
    if s.startswith("tomorrow"):
        day, s = day + timedelta(days=1), s[len("tomorrow"):].strip(" ,at")
    elif s.startswith("today"):
        s = s[len("today"):].strip(" ,at")
    s = s.replace(".", "").replace("noon", "12:00 pm").replace("midnight", "12:00 am")
    for fmt in ("%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%Y-%m-%d %I:%M %p", "%Y-%m-%d %I %p"):
        try:
            return datetime.strptime(s, fmt).timestamp()
        except ValueError:
            pass
    for fmt in ("%H:%M", "%I:%M %p", "%I:%M%p", "%I %p", "%I%p"):
        try:
            t = datetime.strptime(s, fmt).time()
        except ValueError:
            continue
        when = datetime.combine(day, t)
        if when <= now and not at.lower().startswith("tomorrow"):
            when += timedelta(days=1)
        return when.timestamp()
    return None


@registry.tool(
    "reminder",
    "Spoken reminders and timers. set: use 'minutes' for relative times (e.g. 25) or 'at' for clock times "
    "('17:30', '5:30 pm', 'tomorrow 9am', '2026-11-02 09:00'). list: pending reminders. cancel: by number or words.",
    {"action": P("string", "set, list or cancel", enum=["set", "list", "cancel"]),
     "message": P("string", "What to remind about (set) or which one (cancel)", required=False),
     "minutes": P("number", "Minutes from now", required=False),
     "at": P("string", "Clock time or date-time", required=False)},
)
def reminder(action: str, message: str = "", minutes: float | None = None, at: str = "") -> str:
    mem = get_memory()
    if action == "set":
        due = parse_when(minutes, at)
        if due is None:
            return "Error: give 'minutes' or a clock time in 'at'."
        item = mem.add_reminder(message or "Timer finished", due)
        return outcome.done(f"Reminder #{item['id']} set for {datetime.fromtimestamp(due):%A %H:%M}: "
                            f"{item['message']}. (It is spoken while Jarvis is running.)")
    if action == "cancel":
        item = mem.cancel_reminder(message)
        return f"Cancelled reminder: {item['message']}." if item else f"No pending reminder matches '{message}'."
    items = mem.pending_reminders()
    if not items:
        return "No pending reminders."
    return "Pending reminders:\n" + "\n".join(
        f"#{r['id']} {datetime.fromtimestamp(r['due']):%a %H:%M} - {r['message']}" for r in items)


# ---- calendar (iCal / ICS links) -----------------------------------------------
_ics_cache: dict[str, tuple[float, bytes]] = {}


def calendar_events(start: datetime, end: datetime) -> list[dict] | None:
    """Events from the ICS links in CALENDAR_ICS_URL, or None when no calendar is configured."""
    urls = [u for u in re.split(r"[\s,;]+", secret("CALENDAR_ICS_URL")) if u]
    if not urls:
        return None
    import icalendar
    import recurring_ical_events
    events = []
    for url in urls:
        url = url.replace("webcal://", "https://")
        cached = _ics_cache.get(url)
        if cached and time.time() - cached[0] < 600:
            data = cached[1]
        else:
            data = requests.get(url, timeout=15).content
            _ics_cache[url] = (time.time(), data)
        cal = icalendar.Calendar.from_ical(data)
        for ev in recurring_ical_events.of(cal).between(start, end):
            s, e = ev.get("DTSTART").dt, ev.get("DTEND").dt if ev.get("DTEND") else None
            all_day = not isinstance(s, datetime)
            if not all_day and s.tzinfo:
                s = s.astimezone().replace(tzinfo=None)
            if isinstance(e, datetime) and e.tzinfo:
                e = e.astimezone().replace(tzinfo=None)
            events.append({"title": str(ev.get("SUMMARY", "(no title)")), "start": s, "end": e,
                           "all_day": all_day, "location": str(ev.get("LOCATION", "") or "")})
    events.sort(key=lambda x: (x["start"] if not x["all_day"] else datetime.combine(x["start"], datetime.min.time())))
    return events


def format_events(events: list[dict]) -> str:
    lines = []
    for ev in events:
        if ev["all_day"]:
            when = f"{ev['start']:%a %d %b} (all day)"
        else:
            when = f"{ev['start']:%a %H:%M}" + (f"-{ev['end']:%H:%M}" if isinstance(ev["end"], datetime) else "")
        lines.append(f"- {when}: {ev['title']}" + (f" @ {ev['location']}" if ev["location"] else ""))
    return "\n".join(lines)


# Fallback when Google isn't connected (the calendar tool itself lives in gservices.py).
def calendar_ics(day: str = "today") -> str:
    today = datetime.combine(date.today(), datetime.min.time())
    start, end = {"today": (today, today + timedelta(days=1)),
                  "tomorrow": (today + timedelta(days=1), today + timedelta(days=2)),
                  "week": (today, today + timedelta(days=7))}.get(day or "today", (today, today + timedelta(days=1)))
    events = calendar_events(start, end)
    if events is None:
        return "No calendar is configured (set CALENDAR_ICS_URL in .env, or connect a calendar MCP server)."
    if not events:
        return f"Nothing on the calendar {'this week' if day == 'week' else day}."
    # Event titles are written by whoever sent the invite - outside content.
    return f"Calendar ({day}):\n" + outcome.untrusted("calendar", format_events(events))


# ---- email (IMAP, read-only) ------------------------------------------------------
def _decode(value: str) -> str:
    try:
        return str(email.header.make_header(email.header.decode_header(value or "")))
    except Exception:
        return value or ""


def inbox_summary(limit: int = 5) -> str | None:
    user, password = secret("IMAP_USER"), secret("IMAP_PASSWORD")
    if not user or not password:
        return None
    with imaplib.IMAP4_SSL(secret("IMAP_HOST") or "imap.gmail.com", timeout=15) as box:
        box.login(user, password)
        box.select("INBOX", readonly=True)
        _, data = box.search(None, "UNSEEN")
        ids = data[0].split()
        lines = []
        for msg_id in reversed(ids[-limit:]):
            _, parts = box.fetch(msg_id, "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE)])")
            msg = email.message_from_bytes(parts[0][1])
            sender = re.sub(r"\s*<[^>]+>", "", _decode(msg.get("From", ""))).strip('" ')
            lines.append(f"- {sender}: {_decode(msg.get('Subject', '(no subject)'))}")
    return f"{len(ids)} unread email(s)." + (
        "\nLatest unread:\n" + outcome.untrusted("email", "\n".join(lines)) if lines else "")


# Fallback when Google isn't connected (the email tool itself lives in gservices.py).
def check_email(limit: int = 5) -> str:
    summary = inbox_summary(max(1, min(int(limit or 5), 20)))
    if summary is None:
        return ("Email isn't configured. Add IMAP_USER and IMAP_PASSWORD (an app password) to .env, "
                "or connect an email MCP server.")
    return summary
