"""Google: Calendar, Gmail, Contacts, Drive and (your son's) Classroom.

Connect with connect_google.bat. Without it, calendar falls back to an iCal link and email to IMAP
(see productivity.py), and the rest say how to connect.

Everything that comes back from Google - event titles, emails, documents, assignments - was written by
other people, so it reaches the model inside the untrusted-content boundary.
"""
from __future__ import annotations

import base64
import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from email.message import EmailMessage
from html import unescape

from .. import google_auth as ga
from .. import undo as undo_stack
from . import outcome, productivity
from .registry import P, registry

log = logging.getLogger(__name__)

CONNECT = ("Google isn't connected yet. It's set up in Jarvis's Settings window, Google page - about five minutes, "
           "once. Offer to open it (open_settings page=google).")


def _http_error_text(e: Exception) -> str:
    text = str(e)
    if "insufficient" in text.lower() or "PERMISSION_DENIED" in text:
        return ("Google refused - that permission wasn't granted at sign-in. In Settings → Google, sign in again and tick "
                "'Select all'.")
    if "has not been used" in text or "is disabled" in text:
        return "That Google service isn't switched on in the user's Google Cloud project - step 2 in Settings → Google."
    return f"Google returned an error: {text[:200]}"


def _guard(fn):
    """Common failure handling for the Google tools."""
    def wrapper(*args, **kwargs):
        try:
            return fn(*args, **kwargs)
        except ga.NotConnected as e:
            account = e.args[0] if e.args else "main"
            if len(e.args) > 1 and e.args[1] == "expired":
                opened = ga.reconnect_in_background(account)
                who = "your son's Classroom account" if account == "classroom" else "your Google account"
                return outcome.failed(
                    f"Google's weekly sign-in for {who} has expired (Google does this every 7 days for private "
                    "apps like Jarvis). " + ("I've opened the sign-in page in Chrome - tell the user to pick the "
                    "account and click Continue, then ask again." if opened else
                    "The sign-in page is already open in Chrome - tell the user to finish it, then ask again."))
            return outcome.failed(CONNECT)
        except LookupError as e:
            return outcome.failed(str(e))
        except ValueError as e:
            return outcome.failed(str(e))
        except Exception as e:
            if type(e).__name__ == "HttpError":
                return outcome.failed(_http_error_text(e))
            raise
    wrapper.__name__ = fn.__name__
    wrapper.__doc__ = fn.__doc__
    return wrapper


def _ago(ts: float) -> str:
    mins = max(0, int((time.time() - ts) / 60))
    if mins < 60:
        return f"{mins} min ago"
    if mins < 60 * 24:
        return f"{mins // 60} h ago"
    return f"{mins // (60 * 24)} days ago"


# ==== Calendar ===================================================================================
def _parse_when(text: str) -> tuple[datetime | date, bool]:
    """'2026-10-29T15:00' -> (aware datetime, False); '2026-10-29' -> (date, True)."""
    t = (text or "").strip().replace("Z", "+00:00").replace(" ", "T", 1)
    try:
        d = datetime.fromisoformat(t)
    except ValueError:
        raise ValueError(f"Couldn't read the time '{text}' - use YYYY-MM-DDTHH:MM (or YYYY-MM-DD for all day).")
    if len(t) <= 10:
        return d.date(), True
    return (d if d.tzinfo else d.astimezone()), False  # naive = local time


def _from_google(obj: dict) -> tuple[datetime | date, bool]:
    if "dateTime" in obj:
        return datetime.fromisoformat(obj["dateTime"].replace("Z", "+00:00")).astimezone().replace(tzinfo=None), False
    return date.fromisoformat(obj["date"]), True


def _when(ev: dict) -> str:
    if ev["all_day"]:
        return f"{ev['start']:%a %d %b} (all day)"
    return f"{ev['start']:%a %d %b %H:%M}"


def google_events(start: datetime, end: datetime, query: str = "") -> list[dict]:
    svc = ga.service("calendar", "v3")
    cals = svc.calendarList().list(minAccessRole="reader").execute().get("items", [])
    cals = [c for c in cals if c.get("selected") or c.get("primary")]
    tmin, tmax = start.astimezone().isoformat(), end.astimezone().isoformat()
    out = []
    for cal in cals:
        try:
            items = svc.events().list(calendarId=cal["id"], timeMin=tmin, timeMax=tmax, singleEvents=True,
                                      orderBy="startTime", maxResults=50, q=query or None).execute().get("items", [])
        except Exception as e:
            log.warning("calendar %s unreadable: %s", cal.get("summary"), e)
            continue
        for ev in items:
            if ev.get("status") == "cancelled" or any(
                    a.get("self") and a.get("responseStatus") == "declined" for a in ev.get("attendees", [])):
                continue
            s, all_day = _from_google(ev["start"])
            e, _ = _from_google(ev.get("end", ev["start"]))
            title = ev.get("summary", "(no title)")
            if not cal.get("primary"):
                title += f" [{cal.get('summaryOverride') or cal.get('summary', '')}]"
            out.append({"title": title, "start": s, "end": e, "all_day": all_day, "location": ev.get("location", ""),
                        "id": ev["id"], "calendar": cal["id"], "primary": bool(cal.get("primary")),
                        "raw_title": ev.get("summary", ""), "raw": ev})
    out.sort(key=lambda x: x["start"] if not x["all_day"] else datetime.combine(x["start"], datetime.min.time()))
    return out


def events_between(start: datetime, end: datetime) -> list[dict] | None:
    """Google Calendar when connected, else the iCal link; None when neither is set up."""
    if ga.connected("main"):
        return google_events(start, end)
    return productivity.calendar_events(start, end)


def _find_events(title: str, day: str = "", own: bool = True) -> list[dict]:
    """Events matching words in the title. own=True: only ones the user may change (their calendar / organiser)."""
    if day:
        start, end = _day_range(day)
    else:
        start = datetime.now() - timedelta(days=1)
        end = start + timedelta(days=61)
    found = [e for e in google_events(start, end, title)
             if not own or e["primary"] or e["raw"].get("organizer", {}).get("self")]
    words = [w for w in re.findall(r"\w+", title.lower()) if len(w) > 2]
    exact = [e for e in found if all(w in e["raw_title"].lower() for w in words)]
    return exact or found


def _day_range(day: str) -> tuple[datetime, datetime]:
    today = datetime.combine(date.today(), datetime.min.time())
    named = {"today": (today, today + timedelta(days=1)),
             "tomorrow": (today + timedelta(days=1), today + timedelta(days=2)),
             "week": (today, today + timedelta(days=7))}
    if (day or "today").lower() in named:
        return named[(day or "today").lower()]
    d, _ = _parse_when(day[:10])
    start = datetime.combine(d if isinstance(d, date) and not isinstance(d, datetime) else d.date(), datetime.min.time())
    return start, start + timedelta(days=1)


def _calendar_question(args: dict) -> str | None:
    if args.get("action") != "delete":
        return None
    try:
        matches = _find_events(args.get("title", ""), args.get("day", ""))
    except Exception:
        return f"Delete the calendar event '{args.get('title', '')}'?"
    if len(matches) == 1:
        return f"Delete '{matches[0]['raw_title']}' on {_when(matches[0])} from your calendar?"
    return None  # none or several: the tool refuses and asks which one


def _delete_event(cal_id: str, event_id: str) -> str:
    ga.service("calendar", "v3").events().delete(calendarId=cal_id, eventId=event_id).execute()
    return "Removed it from the calendar again."


@registry.tool(
    "calendar",
    "The user's calendar (Google Calendar when connected). agenda: what's on today, tomorrow, this week or a date. "
    "find: look an event up by words in its title (next 60 days). add: create an event. move: change an event's "
    "time. delete: remove one (the user confirms first). Times are local, as YYYY-MM-DDTHH:MM.",
    {"action": P("string", "agenda (default), find, add, move or delete", required=False,
                 enum=["agenda", "find", "add", "move", "delete"]),
     "day": P("string", "agenda: today, tomorrow, week or YYYY-MM-DD. find/move/delete: optional day to search",
              required=False),
     "title": P("string", "add: the event's title. find/move/delete: words from its title", required=False),
     "start": P("string", "add/move: start, YYYY-MM-DDTHH:MM (YYYY-MM-DD = all day)", required=False),
     "end": P("string", "add/move: end time (optional)", required=False),
     "minutes": P("integer", "add/move: length if no end is given (default 60)", required=False),
     "location": P("string", "add: where (optional)", required=False)},
    confirm=_calendar_question,
)
@_guard
def calendar(action: str = "agenda", day: str = "", title: str = "", start: str = "", end: str = "",
             minutes: int = 0, location: str = "") -> str:
    action = action or "agenda"
    if action == "agenda":
        s, e = _day_range(day or "today")
        events = events_between(s, e)
        if events is None:
            return outcome.failed(CONNECT)
        label = "this week" if (day or "").lower() == "week" else (day or "today")
        if not events:
            return f"Nothing on the calendar {label}."
        return f"Calendar ({label}):\n" + outcome.untrusted("calendar", productivity.format_events(events))
    if not ga.connected("main"):
        return outcome.failed(CONNECT)
    svc = ga.service("calendar", "v3")
    if action == "find":
        found = _find_events(title, day, own=False)
        if not found:
            return f"No upcoming event matches '{title}'."
        return "Matching events:\n" + outcome.untrusted("calendar", productivity.format_events(found[:8]))
    if action == "add":
        if not title or not start:
            return outcome.failed("To add an event I need a title and a start time.")
        body = {"summary": title, "start": {}, "end": {}}
        if location:
            body["location"] = location
        _set_times(body, start, end, minutes)
        ev = svc.events().insert(calendarId="primary", body=body).execute()
        undo_stack.register(f"add '{title}' to the calendar", lambda: _delete_event("primary", ev["id"]))
        s, all_day = _from_google(ev["start"])
        return outcome.done(f"Added '{title}' on {_when({'start': s, 'all_day': all_day})}"
                            + (f" at {location}" if location else "") + ".")
    matches = _find_events(title, day)
    if not matches:
        return outcome.failed(f"I couldn't find an event matching '{title}'.")
    if len(matches) > 1:
        return outcome.ask("Several events match - which one? " +
                           "; ".join(f"{m['raw_title']} ({_when(m)})" for m in matches[:5]))
    ev = matches[0]
    if action == "delete":
        svc.events().delete(calendarId=ev["calendar"], eventId=ev["id"]).execute()
        return outcome.done(f"Deleted '{ev['raw_title']}' ({_when(ev)}).")
    if action == "move":
        if not start:
            return outcome.failed("Move it to when?")
        old = {"start": ev["raw"]["start"], "end": ev["raw"]["end"]}
        body = {"start": {}, "end": {}}
        length = minutes or (0 if end else int((_as_dt(ev["end"]) - _as_dt(ev["start"])).total_seconds() // 60))
        _set_times(body, start, end, length)
        moved = svc.events().patch(calendarId=ev["calendar"], eventId=ev["id"], body=body).execute()
        undo_stack.register(f"move '{ev['raw_title']}'", lambda: (svc_patch(ev["calendar"], ev["id"], old),
                                                                 "Moved it back.")[1])
        s, all_day = _from_google(moved["start"])
        return outcome.done(f"Moved '{ev['raw_title']}' to {_when({'start': s, 'all_day': all_day})}.")
    return outcome.failed(f"Unknown calendar action '{action}'.")


def svc_patch(cal_id: str, event_id: str, body: dict) -> None:
    ga.service("calendar", "v3").events().patch(calendarId=cal_id, eventId=event_id, body=body).execute()


def _as_dt(v) -> datetime:
    return v if isinstance(v, datetime) else datetime.combine(v, datetime.min.time())


def _set_times(body: dict, start: str, end: str, minutes: int) -> None:
    s, all_day = _parse_when(start)
    if all_day:
        e = _parse_when(end)[0] if end else s + timedelta(days=1)
        e = e if isinstance(e, date) and not isinstance(e, datetime) else e.date()
        body["start"], body["end"] = {"date": s.isoformat()}, {"date": e.isoformat()}
        return
    e = _parse_when(end)[0] if end else s + timedelta(minutes=minutes or 60)
    if not isinstance(e, datetime):
        e = s + timedelta(minutes=minutes or 60)
    body["start"], body["end"] = {"dateTime": s.isoformat()}, {"dateTime": e.isoformat()}


# ==== Contacts ===================================================================================
_people_warm = False


def _people_search(query: str) -> list[dict]:
    """Saved contacts plus 'other contacts' (people you've emailed), best match first."""
    global _people_warm
    svc = ga.service("people", "v1")
    other = ga.has_scope("main", "contacts.other.readonly")
    if not _people_warm:  # the People API wants an empty search first to fill its search cache
        for call in ([svc.people().searchContacts(query="", readMask="names")] +
                     ([svc.otherContacts().search(query="", readMask="names")] if other else [])):
            try:
                call.execute()
            except Exception:
                pass
        _people_warm = True
        time.sleep(0.5)
    out, seen = [], set()

    def add(person: dict, saved: bool) -> None:
        emails = [e["value"] for e in person.get("emailAddresses", []) if e.get("value")]
        name = (person.get("names") or [{}])[0].get("displayName", "") or (emails[0] if emails else "")
        key = (emails[0] if emails else name).lower()
        if key and key not in seen:
            seen.add(key)
            out.append({"name": name, "emails": emails, "saved": saved,
                        "phones": [p["value"] for p in person.get("phoneNumbers", []) if p.get("value")]})

    r = svc.people().searchContacts(query=query, readMask="names,emailAddresses,phoneNumbers", pageSize=10).execute()
    for res in r.get("results", []):
        add(res["person"], True)
    if other:
        try:
            r = svc.otherContacts().search(query=query, readMask="names,emailAddresses", pageSize=10).execute()
            for res in r.get("results", []):
                add(res["person"], False)
        except Exception as e:
            log.debug("other contacts search failed: %s", e)
    return out


def _resolve(to: str) -> list[tuple[str, str]]:
    """'Mum' / 'mr smith' / 'a@b.com, Dad' -> [(name, address)]. Raises LookupError when unsure."""
    found = []
    for part in [p.strip() for p in re.split(r"[,;]|\band\b", to or "") if p.strip()]:
        m = re.search(r"[\w.+-]+@[\w-]+(\.[\w-]+)+", part)
        if m:
            found.append((part.replace(m.group(0), "").strip(" <>\"'") or m.group(0), m.group(0)))
            continue
        people = [p for p in _people_search(part) if p["emails"]]
        if not people:
            raise LookupError(f"I couldn't find an email address for '{part}' in your contacts - what's the address?")
        exact = [p for p in people if p["name"].lower() == part.lower()]
        addresses = {p["emails"][0].lower() for p in people}
        pick = exact[0] if len(exact) == 1 else people[0] if len(addresses) == 1 else None
        if pick is None:
            raise LookupError(f"'{part}' could be several people: " +
                              "; ".join(f"{p['name']} <{p['emails'][0]}>" for p in people[:4]) + ". Which one?")
        found.append((pick["name"], pick["emails"][0]))
    if not found:
        raise LookupError("Who should it go to?")
    return found


@registry.tool(
    "contacts",
    "Look someone up in the user's Google Contacts (and people they've emailed): email addresses and phone numbers.",
    {"query": P("string", "A name, nickname or part of an email address")},
)
@_guard
def contacts(query: str) -> str:
    people = _people_search(query)
    if not people:
        return f"No contact matches '{query}'."
    lines = [f"- {p['name']}: " + ", ".join(p["emails"] + p["phones"]) + ("" if p["saved"] else " (emailed before)")
             for p in people[:6]]
    return outcome.untrusted("contacts", "\n".join(lines))


# ==== Gmail ======================================================================================
def _gmail():
    return ga.service("gmail", "v1")


def _b64(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", "replace")


def _strip_html(h: str) -> str:
    h = re.sub(r"(?is)<(script|style|head).*?</\1>", "", h)
    h = re.sub(r"(?i)<br\s*/?>|</p>|</div>|</tr>|</h\d>|</li>", "\n", h)
    return unescape(re.sub(r"<[^>]+>", "", h))


def _body_text(payload: dict) -> str:
    plain, html = [], []

    def walk(p: dict) -> None:
        data = (p.get("body") or {}).get("data")
        if data and p.get("mimeType") == "text/plain":
            plain.append(_b64(data))
        elif data and p.get("mimeType") == "text/html":
            html.append(_b64(data))
        for child in p.get("parts") or []:
            walk(child)

    walk(payload)
    text = "\n".join(plain) if plain else _strip_html("\n".join(html))
    text = re.split(r"\n\s*(On .{5,120} wrote:|-----Original Message-----|From: .+\nSent: )", text)[0]
    text = re.sub(r"[ \t ]+", " ", text)
    return re.sub(r"\n\s*\n\s*\n+", "\n\n", text).strip()


def _headers(msg: dict) -> dict:
    return {h["name"].lower(): h["value"] for h in (msg.get("payload") or {}).get("headers", [])}


def _name(addr: str) -> str:
    return re.sub(r"\s*<[^>]+>", "", addr or "").strip('" ') or addr


def _list(query: str, limit: int) -> list[dict]:
    svc = _gmail()
    ids = svc.users().messages().list(userId="me", q=query, maxResults=limit).execute().get("messages", [])
    got: dict[str, dict] = {}
    batch = svc.new_batch_http_request(callback=lambda rid, resp, exc: got.__setitem__(rid, resp) if resp else None)
    for m in ids:
        batch.add(svc.users().messages().get(userId="me", id=m["id"], format="metadata",
                                             metadataHeaders=["From", "Subject", "Date"]), request_id=m["id"])
    if ids:
        batch.execute()
    out = []
    for m in ids:
        msg = got.get(m["id"])
        if not msg:
            continue
        h = _headers(msg)
        out.append({"id": m["id"], "from": _name(h.get("from", "")), "subject": h.get("subject", "(no subject)"),
                    "snippet": unescape(msg.get("snippet", ""))[:160], "ts": int(msg.get("internalDate", 0)) / 1000,
                    "unread": "UNREAD" in msg.get("labelIds", [])})
    return out


def _format_mail(items: list[dict]) -> str:
    return "\n".join(f"- [id {m['id']}] {m['from']}: {m['subject']} ({_ago(m['ts'])}) - {m['snippet']}"
                     for m in items)


PRIMARY_UNREAD = "is:unread in:inbox category:primary"


def unread_summary(limit: int = 3) -> str | None:
    """For the briefing: unread mail in the Primary tab. None when no email is set up."""
    if not ga.connected("main"):
        return productivity.inbox_summary(limit)
    svc = _gmail()
    count = svc.users().messages().list(userId="me", q=PRIMARY_UNREAD, maxResults=100).execute()
    n = len(count.get("messages", []))
    if not n:
        return "Email: nothing new in the Primary inbox."
    items = _list(PRIMARY_UNREAD, limit)
    return (f"Email: {n}{'+' if n >= 100 else ''} unread in Primary. Latest:\n" +
            outcome.untrusted("email", "\n".join(f"- {m['from']}: {m['subject']}" for m in items)))


def _email_question(args: dict) -> str | None:
    action = args.get("action")
    if action not in ("send", "reply"):
        return None
    body = re.sub(r"\s+", " ", args.get("body", "")).strip()
    preview = body[:140] + ("..." if len(body) > 140 else "")
    try:
        if action == "send":
            people = ", ".join(f"{n} <{a}>" if n != a else a for n, a in _resolve(args.get("to", "")))
            return f"Send this email to {people}, subject \"{args.get('subject', '')}\": \"{preview}\"?"
        h = _headers(_gmail().users().messages().get(userId="me", id=args.get("id", ""), format="metadata",
                                                      metadataHeaders=["From", "Subject"]).execute())
        return f"Reply to {_name(h.get('from', ''))} about \"{h.get('subject', '')}\": \"{preview}\"?"
    except Exception:
        # Never let a lookup hiccup skip the question: sending is the one thing that can't be undone.
        return f"Send this email ({args.get('to') or 'reply'}): \"{preview}\"?"


def _raw(to: str, subject: str, body: str, extra: dict | None = None) -> str:
    msg = EmailMessage()
    msg["To"] = to
    msg["Subject"] = subject
    for k, v in (extra or {}).items():
        msg[k] = v
    msg.set_content(body)
    return base64.urlsafe_b64encode(msg.as_bytes()).decode()


@registry.tool(
    "email",
    "The user's Gmail. unread: new mail in the Primary inbox (default). search: Gmail search (words, or "
    "from:/subject:/newer_than:2d...). read: one email in full (by id, or the newest match for a search). draft: "
    "write a draft for the user to check. send: send a new email; reply: reply to one by id - both ask the user "
    "first. archive: move one out of the inbox. 'to' can be names from their contacts.",
    {"action": P("string", "unread (default), search, read, draft, send, reply or archive", required=False,
                 enum=["unread", "search", "read", "draft", "send", "reply", "archive"]),
     "query": P("string", "search/read: what to look for", required=False),
     "id": P("string", "read/reply/archive: the email's id from a list", required=False),
     "to": P("string", "draft/send: recipient names or addresses, comma-separated", required=False),
     "subject": P("string", "draft/send: subject line", required=False),
     "body": P("string", "draft/send/reply: the text - written as the user, plain and natural", required=False),
     "limit": P("integer", "unread/search: how many (default 5)", required=False)},
    confirm=_email_question, slow=True,
)
@_guard
def email(action: str = "unread", query: str = "", id: str = "", to: str = "", subject: str = "",
          body: str = "", limit: int = 5) -> str:
    action = action or "unread"
    limit = max(1, min(int(limit or 5), 15))
    if not ga.connected("main"):
        if action == "unread":
            summary = productivity.inbox_summary(limit)
            if summary is not None:
                return summary
        return outcome.failed(CONNECT)
    svc = _gmail()
    if action in ("unread", "search"):
        q = PRIMARY_UNREAD if action == "unread" else (query or "in:inbox")
        items = _list(q, limit)
        if not items:
            return "No unread email in the Primary inbox." if action == "unread" else f"No email matches '{q}'."
        return (f"{'Unread' if action == 'unread' else 'Matching'} email (newest first):\n" +
                outcome.untrusted("email", _format_mail(items)))
    if action == "read":
        if not id:
            found = _list(query or PRIMARY_UNREAD, 1)
            if not found:
                return outcome.failed(f"No email matches '{query}'.")
            id = found[0]["id"]
        msg = svc.users().messages().get(userId="me", id=id, format="full").execute()
        h = _headers(msg)
        text = _body_text(msg.get("payload", {}))[:3500]
        if "UNREAD" in msg.get("labelIds", []):
            svc.users().messages().modify(userId="me", id=id, body={"removeLabelIds": ["UNREAD"]}).execute()
        return outcome.untrusted("email", f"From: {h.get('from', '')}\nTo: {h.get('to', '')}\nDate: {h.get('date', '')}\n"
                                          f"Subject: {h.get('subject', '')}\n\n{text}") + \
            "\nSummarise it in a sentence or two unless asked to read it out."
    if action == "draft":
        if not body:
            return outcome.failed("What should the email say?")
        rcpt = ", ".join(a for _, a in _resolve(to)) if to else ""
        d = svc.users().drafts().create(userId="me", body={"message": {"raw": _raw(rcpt, subject, body)}}).execute()
        undo_stack.register("draft email", lambda: (svc_draft_delete(d["id"]), "Deleted the draft.")[1])
        return outcome.done(f"Draft saved in Gmail{(' to ' + rcpt) if rcpt else ''} - it's in Drafts for you to check.")
    if action == "send":
        if not body or not to:
            return outcome.failed("I need who it's to and what it says.")
        people = _resolve(to)
        rcpt = ", ".join(f"{n} <{a}>" if n != a else a for n, a in people)
        svc.users().messages().send(userId="me", body={"raw": _raw(rcpt, subject or "(no subject)", body)}).execute()
        return outcome.done(f"Sent to {', '.join(n for n, _ in people)}.")
    if action == "reply":
        if not id or not body:
            return outcome.failed("Reply to which email (id), and saying what?")
        orig = svc.users().messages().get(userId="me", id=id, format="metadata",
                                          metadataHeaders=["From", "Reply-To", "Subject", "Message-ID"]).execute()
        h = _headers(orig)
        subj = h.get("subject", "")
        subj = subj if subj.lower().startswith("re:") else f"Re: {subj}"
        extra = {"In-Reply-To": h.get("message-id", ""), "References": h.get("message-id", "")} \
            if h.get("message-id") else {}
        svc.users().messages().send(userId="me", body={"raw": _raw(h.get("reply-to") or h.get("from", ""), subj, body,
                                                                   extra), "threadId": orig["threadId"]}).execute()
        return outcome.done(f"Replied to {_name(h.get('from', ''))}.")
    if action == "archive":
        if not id:
            return outcome.failed("Archive which email (id)?")
        svc.users().messages().modify(userId="me", id=id, body={"removeLabelIds": ["INBOX"]}).execute()
        undo_stack.register("archive email", lambda: (svc_unarchive(id), "Put it back in the inbox.")[1])
        return outcome.done("Archived.")
    return outcome.failed(f"Unknown email action '{action}'.")


def svc_draft_delete(draft_id: str) -> None:
    _gmail().users().drafts().delete(userId="me", id=draft_id).execute()


def svc_unarchive(msg_id: str) -> None:
    _gmail().users().messages().modify(userId="me", id=msg_id, body={"addLabelIds": ["INBOX"]}).execute()


# ==== Drive ======================================================================================
_DOC_EXPORT = {"application/vnd.google-apps.document": "text/plain",
               "application/vnd.google-apps.spreadsheet": "text/csv",
               "application/vnd.google-apps.presentation": "text/plain"}


def _drive_find(query: str, limit: int = 8) -> list[dict]:
    q = query.replace("\\", "\\\\").replace("'", "\\'")
    res = ga.service("drive", "v3").files().list(
        q=f"(name contains '{q}' or fullText contains '{q}') and trashed = false", pageSize=limit,
        fields="files(id,name,mimeType,modifiedTime,webViewLink)", supportsAllDrives=True,
        includeItemsFromAllDrives=True).execute().get("files", [])
    words = query.lower().split()
    named = [f for f in res if all(w in f["name"].lower() for w in words)]  # name matches first, newest first
    rest = sorted([f for f in res if f not in named], key=lambda f: f.get("modifiedTime", ""), reverse=True)
    return sorted(named, key=lambda f: f.get("modifiedTime", ""), reverse=True) + rest


@registry.tool(
    "drive",
    "The user's Google Drive. search: find files by name or contents. open: open the best match in Chrome. "
    "read: get the text of a Google Doc / Sheet / Slides file.",
    {"action": P("string", "search (default), open or read", required=False, enum=["search", "open", "read"]),
     "query": P("string", "Words from the file's name or contents")},
    slow=True,
)
@_guard
def drive(query: str, action: str = "search") -> str:
    files = _drive_find(query)
    if not files:
        return f"Nothing in Drive matches '{query}'."
    if action == "open":
        from .apps import open_url
        open_url(files[0]["webViewLink"])
        return outcome.done(f"Opened '{files[0]['name']}' in Chrome.")
    if action == "read":
        f = files[0]
        mime = _DOC_EXPORT.get(f["mimeType"])
        if not mime:
            return outcome.failed(f"'{f['name']}' isn't a Google Doc, Sheet or Slides file - I can open it instead.")
        data = ga.service("drive", "v3").files().export(fileId=f["id"], mimeType=mime).execute()
        text = (data.decode("utf-8", "replace") if isinstance(data, bytes) else str(data))[:4000]
        return outcome.untrusted(f"Drive file '{f['name']}'", text)
    lines = [f"- {f['name']} (edited {f.get('modifiedTime', '')[:10]})" for f in files[:8]]
    return "Drive files:\n" + outcome.untrusted("drive", "\n".join(lines))


# ==== Classroom ==================================================================================
def _due(work: dict) -> datetime | None:
    d = work.get("dueDate")
    if not d:
        return None
    if "dueTime" in work:  # Classroom stores due times in UTC; a missing field inside means 0
        t = work["dueTime"]
        return datetime(d["year"], d["month"], d["day"], t.get("hours", 0), t.get("minutes", 0),
                        tzinfo=timezone.utc).astimezone().replace(tzinfo=None)
    return datetime(d["year"], d["month"], d["day"], 23, 59)


def _course_work(course: dict) -> list[dict]:
    svc = ga.service("classroom", "v1", "classroom")
    cid = course["id"]
    try:
        work = svc.courses().courseWork().list(courseId=cid, pageSize=60, orderBy="dueDate desc").execute()
        subs = svc.courses().courseWork().studentSubmissions().list(courseId=cid, courseWorkId="-", userId="me",
                                                                    pageSize=200).execute()
    except Exception as e:  # one odd class shouldn't hide the others
        log.warning("Classroom course %s unreadable: %s", course.get("name"), e)
        return []
    state = {s["courseWorkId"]: s for s in subs.get("studentSubmissions", [])}
    out = []
    for w in work.get("courseWork", []):
        sub = state.get(w["id"], {})
        out.append({"course": course.get("name", ""), "title": w.get("title", ""), "due": _due(w),
                    "created": w.get("creationTime", "")[:10], "points": w.get("maxPoints"),
                    "done": sub.get("state") in ("TURNED_IN", "RETURNED"), "late": bool(sub.get("late")),
                    "grade": sub.get("assignedGrade"), "returned": sub.get("state") == "RETURNED",
                    "link": w.get("alternateLink", "")})
    return out


def _classroom_api(days: int) -> dict:
    svc = ga.service("classroom", "v1", "classroom")
    courses = svc.courses().list(studentId="me", courseStates=["ACTIVE"], pageSize=40).execute().get("courses", [])
    with ThreadPoolExecutor(max_workers=6) as pool:
        all_work = [w for ws in pool.map(_course_work, courses) for w in ws]
    now = datetime.now()
    soon = now + timedelta(days=days)
    missing = sorted([w for w in all_work if not w["done"] and w["due"] and w["due"] < now
                      and w["due"] > now - timedelta(days=45)], key=lambda w: w["due"], reverse=True)
    upcoming = sorted([w for w in all_work if not w["done"] and w["due"] and now <= w["due"] <= soon],
                      key=lambda w: w["due"])
    graded = sorted([w for w in all_work if w["returned"] and w["grade"] is not None and w["due"]
                     and w["due"] > now - timedelta(days=10)], key=lambda w: w["due"], reverse=True)
    return {"courses": courses, "missing": missing, "upcoming": upcoming, "graded": graded}


def _fmt_work(w: dict, past: bool = False) -> str:
    when = f"{w['due']:%a %d %b %H:%M}".replace(" 23:59", "")
    return f"- {w['course']}: {w['title']} ({'was due' if past else 'due'} {when})"


def _guardian_emails() -> str:
    """Fallback: the Classroom guardian summaries Google emails to parents."""
    if not ga.connected("main"):
        return ""
    items = _list("from:(no-reply@classroom.google.com) newer_than:14d", 1)
    if not items:
        return ""
    msg = _gmail().users().messages().get(userId="me", id=items[0]["id"], format="full").execute()
    h = _headers(msg)
    return (f"Latest Classroom guardian summary email ({h.get('subject', '')}, {_ago(items[0]['ts'])}):\n" +
            outcome.untrusted("Classroom email", _body_text(msg.get("payload", {}))[:3500]))


@registry.tool(
    "classroom",
    "The user's son's Google Classroom: what homework is due soon, what's missing (past due, not turned in), recent "
    "grades and teacher announcements.",
    {"action": P("string", "due (default: missing + due soon), grades, announcements or classes", required=False,
                 enum=["due", "grades", "announcements", "classes"]),
     "days": P("integer", "due: how many days ahead (default 7)", required=False)},
    slow=True,
)
@_guard
def classroom(action: str = "due", days: int = 7) -> str:
    action = action or "due"
    if not ga.connected("classroom"):
        fallback = _guardian_emails()
        if fallback:
            return fallback + "\nTell the user what's due or missing from this summary, briefly."
        return outcome.failed("Classroom isn't connected. In Settings → Google, click 'Connect son's Classroom' and sign in his school "
                              "account. (If the school blocks it, Classroom's guardian summary emails work too - "
                              "the teacher can add you as a guardian.)")
    who = ga.email_of("classroom") or "your son"
    if action == "classes":
        courses = ga.service("classroom", "v1", "classroom").courses().list(
            studentId="me", courseStates=["ACTIVE"]).execute().get("courses", [])
        return f"Classes for {who}:\n" + outcome.untrusted("Classroom", "\n".join(
            f"- {c.get('name', '')}" + (f" ({c['section']})" if c.get("section") else "") for c in courses))
    if action == "announcements":
        svc = ga.service("classroom", "v1", "classroom")
        courses = svc.courses().list(studentId="me", courseStates=["ACTIVE"]).execute().get("courses", [])
        cutoff = (datetime.now(timezone.utc) - timedelta(days=7)).isoformat()
        lines = []
        for c in courses:
            for a in svc.courses().announcements().list(courseId=c["id"], pageSize=3).execute().get("announcements", []):
                if a.get("updateTime", "") >= cutoff:
                    lines.append(f"- {c.get('name', '')} ({a['updateTime'][:10]}): {a.get('text', '')[:300]}")
        return (f"Classroom announcements this week ({who}):\n" + outcome.untrusted("Classroom", "\n".join(lines))
                if lines else "No new Classroom announcements this week.")
    data = _classroom_api(max(1, min(int(days or 7), 30)))
    if action == "grades":
        if not data["graded"]:
            return "No newly returned grades in the last ten days."
        return "Recently graded:\n" + outcome.untrusted("Classroom", "\n".join(
            f"- {w['course']}: {w['title']} - {w['grade']}" + (f"/{w['points']:g}" if w["points"] else "")
            for w in data["graded"][:10]))
    parts = []
    if data["missing"]:
        parts.append("Missing (past due, not turned in):\n" + "\n".join(_fmt_work(w, True) for w in data["missing"][:10]))
    if data["upcoming"]:
        parts.append(f"Due in the next {days or 7} days:\n" + "\n".join(_fmt_work(w) for w in data["upcoming"][:12]))
    if not parts:
        return f"Nothing missing and nothing due in the next {days or 7} days for {who}."
    return f"Google Classroom ({who}):\n" + outcome.untrusted("Classroom", "\n\n".join(parts))


def school_summary() -> str:
    """For the briefing: one compact line about the son's schoolwork (only when Classroom is connected)."""
    if not ga.connected("classroom"):
        return ""
    data = _classroom_api(3)
    if not data["missing"] and not data["upcoming"]:
        return "Schoolwork: nothing missing or due in the next three days."
    bits = []
    if data["missing"]:
        bits.append(f"{len(data['missing'])} missing: " + "; ".join(f"{w['course']} - {w['title']}"
                                                                     for w in data["missing"][:3]))
    if data["upcoming"]:
        bits.append("due soon: " + "; ".join(f"{w['course']} - {w['title']} ({w['due']:%a})"
                                             for w in data["upcoming"][:4]))
    return "Schoolwork (son's Classroom): " + outcome.untrusted("Classroom", ". ".join(bits))


# ==== status checks (setup + self-test) =============================================================
def _check_calendar() -> str:
    today = datetime.combine(date.today(), datetime.min.time())
    n = len(google_events(today, today + timedelta(days=1)))
    return f"ok - {n} event(s) today"


def _check_gmail() -> str:
    n = len(_gmail().users().messages().list(userId="me", q=PRIMARY_UNREAD, maxResults=100).execute()
            .get("messages", []))
    return f"ok - {n} unread in Primary"


def _check_contacts() -> str:
    ga.service("people", "v1").people().connections().list(resourceName="people/me", pageSize=1,
                                                           personFields="names").execute()
    return "ok"


def _check_drive() -> str:
    ga.service("drive", "v3").files().list(pageSize=1, fields="files(id)").execute()
    return "ok"


def _check_classroom() -> str:
    if not ga.connected("classroom"):
        g = _list("from:(no-reply@classroom.google.com) newer_than:30d", 1) if ga.connected("main") else []
        return ("not connected - will use the guardian summary emails in Gmail" if g
                else "not connected (optional)")
    n = len(ga.service("classroom", "v1", "classroom").courses().list(studentId="me", courseStates=["ACTIVE"])
            .execute().get("courses", []))
    return f"ok - {n} class(es) for {ga.email_of('classroom')}"


HEALTH_CHECKS = [("Calendar", _check_calendar), ("Gmail", _check_gmail), ("Contacts", _check_contacts),
                 ("Drive", _check_drive), ("Classroom", _check_classroom)]

ENABLE_APIS_URL = ("https://console.cloud.google.com/flows/enableapi?apiid=calendar-json.googleapis.com,"
                   "gmail.googleapis.com,people.googleapis.com,drive.googleapis.com,classroom.googleapis.com")


def short_error(e: Exception) -> str:
    """A one-line reason a Google check failed."""
    text = str(e)
    if "SERVICE_DISABLED" in text or "has not been used in project" in text or "is disabled" in text:
        return "switched off in your Google project - use 'Turn on missing services'"
    if "insufficient" in text.lower() or "PERMISSION_DENIED" in text:
        return "permission not granted at sign-in - sign in again and tick Select all"
    if isinstance(e, ga.NotConnected):
        return "not signed in"
    return text.splitlines()[0][:120]


def run_health_checks() -> list[tuple[str, bool, str]]:
    out = []
    for label, fn in HEALTH_CHECKS:
        try:
            out.append((label, True, fn()))
        except Exception as e:
            out.append((label, False, short_error(e)))
    return out
