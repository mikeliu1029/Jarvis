"""What a tool tells the model it actually achieved, and a boundary around outside content.

Adapted from the design of MARK LVI by FatihMakes (CC BY-NC 4.0): a tool that reports only
its successes invites the model to invent the rest, so every action says which of these it is.

  done        it happened, and it was checked - never assumed from a return code
  unverified  the request went out but the result couldn't be confirmed (never speak it as success)
  failed      it did not happen, and why
  ask         nothing was done because doing it would have been a guess
  started     under way; the outcome follows separately
"""
from __future__ import annotations

import re

_MARKER = re.compile(r"<<<\s*(END\s+)?UNTRUSTED", re.I)
_BLOCK = re.compile(r"<<<UNTRUSTED[^>]*>>>\n?|\n?<<<END UNTRUSTED>>>")


def done(msg: str) -> str:
    return f"[done] {msg}"


def unverified(msg: str) -> str:
    return f"[unverified] {msg}"


def failed(msg: str) -> str:
    return f"[failed] {msg}"


def ask(msg: str) -> str:
    return f"[ask] {msg}"


def started(msg: str) -> str:
    return f"[started] {msg}"


def untrusted(source: str, text: str) -> str:
    """Wrap outside content (web, files, email, screen) so the model treats it as data.

    A page that writes the closing marker itself has it defanged first, so the
    boundary can't be forged from inside."""
    safe = _MARKER.sub("<<(marker removed)", text or "")
    return f"<<<UNTRUSTED {source}>>>\n{safe}\n<<<END UNTRUSTED>>>"


def unwrap(text: str) -> str:
    """Strip the markers for display to a person - they're for the model."""
    return _BLOCK.sub("", text or "").strip()


def status_of(text: str) -> str:
    m = re.match(r"\[(done|unverified|failed|ask|started)\]", text or "")
    return m.group(1) if m else ""
