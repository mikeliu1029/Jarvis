"""Builds Jarvis's system prompt: persona.md + what he can do, can't do, and remembers.

The capability and limits sections are generated from the live tool registry rather than
written by hand (an idea from MARK LVI by FatihMakes, CC BY-NC 4.0): add a plugin and Jarvis
knows about it; remove one and he stops claiming it.
"""
from __future__ import annotations

import re

from .config import CONFIG_DIR, Settings
from .memory import Memory

FALLBACK = "You are {{name}}, a concise, dry-witted British AI assistant. Address the user as {{title}}."


def describe_tools(tools) -> str:
    lines = []
    for t in sorted(tools, key=lambda t: (t.source != "builtin", t.name)):
        first = re.split(r"(?<=[.!?])\s", " ".join(t.description.split()), maxsplit=1)[0]
        lines.append(f"- {t.name}: {first[:110]}")
    return "\n".join(lines)


def describe_limits(tools, settings: Settings) -> str:
    names = {t.name for t in tools}
    out = [
        "- Anything not listed above is out of reach: say so in one clause and offer the nearest thing you can do. "
        "Never mime an action you can't take or report a result you didn't get.",
        "- You act on this PC only - not the user's phone, other devices or online accounts, except through tools above.",
        "- You can't type into or click windows running as administrator (Windows blocks it) unless you were started as "
        "administrator yourself.",
        "- You remember only what is in the memory section and this conversation; anything else told to you before is "
        "gone unless it was saved.",
    ]
    if "look_at_screen" in names:
        out.append("- Your sight isn't continuous: you see the screen only when you call read_screen or look_at_screen, "
                   "and only that single moment.")
    if str(settings.get("brain.provider", "ollama")) == "ollama":
        out.append("- You run entirely on this computer; web tools are your only window onto the internet.")
    return "\n".join(out)


def build_system_prompt(settings: Settings, memory: Memory, tools=None) -> str:
    path = CONFIG_DIR / "persona.md"
    template = path.read_text(encoding="utf-8-sig") if path.exists() else FALLBACK
    title = str(settings.get("user.title", "sir") or "sir")
    name = str(settings.get("assistant.name", "J.A.R.V.I.S.") or "J.A.R.V.I.S.")
    user_name = str(settings.get("user.name", "") or "").strip()
    tools = tools or []
    budget = int(settings.get("memory.prompt_budget_chars", 1400))
    values = {
        "name": name,
        "title": title,
        "user": user_name or f"the user (whom you address as \"{title}\")",
        "memory": (f"- Their name is {user_name}.\n" if user_name else "") + memory.prompt_block(budget) +
                  (f"\n- Last session ({last['date']}): {last['summary']}" if (last := memory.last_session()) else ""),
        "capabilities": describe_tools(tools) if tools else "- (tools load at startup)",
        "limits": describe_limits(tools, settings),
    }
    out = template
    for key, val in values.items():
        out = out.replace("{{" + key + "}}", val)
    return out
