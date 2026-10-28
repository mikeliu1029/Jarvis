"""The daily briefing: weather, calendar, headlines, tasks, email and PC status in one go."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta

from ..config import get_settings
from ..memory import get_memory
from . import gservices, news, outcome, productivity, system, weather
from .registry import registry

log = logging.getLogger(__name__)


def _news_section() -> str:
    cats = get_settings().get("briefing.news_categories", ["top", "world", "tech"])
    per = int(get_settings().get("briefing.headlines_per_category", 2))
    seen, items = set(), []
    for cat in cats:
        for it in news.headlines(cat, count=per + 2):
            if it["title"] not in seen and sum(1 for i in items if i.get("_cat") == cat) < per:
                seen.add(it["title"])
                items.append({**it, "_cat": cat})
    return ("Headlines:\n" + outcome.untrusted("news feeds", news.format_items(items))
            if items else "Headlines: (news feeds unreachable)")


def _calendar_section() -> str:
    today = datetime.combine(date.today(), datetime.min.time())
    events = gservices.events_between(today, today + timedelta(days=1))
    if events is None:
        return ""
    return ("Calendar today:\n" + outcome.untrusted("calendar", productivity.format_events(events))
            if events else "Calendar today: nothing scheduled.")


def _tasks_section() -> str:
    mem = get_memory()
    tasks = mem.tasks()
    rem = [r for r in mem.pending_reminders() if r["due"] < (datetime.now() + timedelta(hours=18)).timestamp()]
    parts = []
    if tasks:
        parts.append("Open tasks: " + "; ".join(t["text"] for t in tasks[:6]))
    if rem:
        parts.append("Reminders: " + "; ".join(f"{datetime.fromtimestamp(r['due']):%H:%M} {r['message']}" for r in rem))
    return "\n".join(parts) or "No open tasks or reminders."


def _system_section() -> str:
    import psutil
    lines = []
    for part in psutil.disk_partitions():
        try:
            du = psutil.disk_usage(part.mountpoint)
            if du.free < 20 * 2**30:
                lines.append(f"Drive {part.device} is low on space: {du.free / 2**30:.0f} GB free.")
        except OSError:
            pass
    bat = psutil.sensors_battery()
    if bat and bat.percent < 40 and not bat.power_plugged:
        lines.append(f"Battery at {bat.percent:.0f}%.")
    return "PC: " + (" ".join(lines) if lines else "all systems normal.")


def _last_session_section() -> str:
    """Yesterday's conversation, mentioned once and then consumed so it never repeats."""
    last = get_memory().pop_last_session()
    if not last:
        return ""
    try:
        days = (date.today() - datetime.strptime(last["date"], "%Y-%m-%d").date()).days
        when = "Earlier today" if days == 0 else "Yesterday" if days == 1 else f"{days} days ago"
    except (KeyError, ValueError):
        when = "Last time"
    return f"{when} you talked about: {last.get('summary', '')}"


SECTIONS = {
    "last_session": _last_session_section,
    "weather": lambda: weather.get_weather(),
    "calendar": _calendar_section,
    "news": _news_section,
    "tasks": _tasks_section,
    "email": lambda: gservices.unread_summary(3) or "",
    "school": gservices.school_summary,
    "system": _system_section,
}


@registry.tool(
    "daily_briefing",
    "Gather the user's daily briefing: date, weather, calendar, top headlines, tasks and reminders, unread email, "
    "their son's schoolwork and PC status. Use for 'daily briefing', 'morning update', 'what's my day look like', 'catch me up'.",
    {},
    slow=True,
)
def daily_briefing() -> str:
    wanted = [s for s in get_settings().get("briefing.sections", list(SECTIONS)) if s in SECTIONS]
    with ThreadPoolExecutor(max_workers=len(wanted) or 1) as pool:
        futures = {name: pool.submit(SECTIONS[name]) for name in wanted}
        parts = [f"Now: {datetime.now():%A %d %B %Y, %H:%M}"]
        for name, fut in futures.items():
            try:
                text = fut.result(timeout=20)
            except Exception as e:
                log.warning("briefing section %s failed: %s", name, e)
                text = f"{name.capitalize()}: unavailable right now."
            if text:
                parts.append(text)
    get_memory().set("last_briefing", date.today().isoformat())
    return ("\n\n".join(parts) +
            "\n\nDeliver this as a spoken briefing of about 120-170 words: a greeting suited to the time of day, a "
            "brief nod to what you last talked about (if given), the weather in a sentence, today's schedule, then "
            "the headlines as one short sentence each, then tasks, then any email worth knowing about, then the son's "
            "schoolwork if given (missing work first, kindly). Only flag PC issues if there are any. No lists "
            "or headings - flowing speech. The full headlines are shown on screen.")
