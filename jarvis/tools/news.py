"""News headlines from RSS feeds (configurable in settings.toml) and Google News topic search."""
from __future__ import annotations

import calendar
import html
import logging
import re
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor

import feedparser
import requests

from ..config import get_settings
from . import outcome
from .apps import UA
from .registry import P, registry

log = logging.getLogger(__name__)

DEFAULT_FEEDS = {
    "top": ["https://feeds.npr.org/1001/rss.xml", "https://feeds.bbci.co.uk/news/world/rss.xml"],
    "world": ["https://feeds.bbci.co.uk/news/world/rss.xml"],
    "us": ["https://feeds.npr.org/1003/rss.xml"],
    "business": ["https://feeds.bbci.co.uk/news/business/rss.xml"],
    "tech": ["https://www.theverge.com/rss/index.xml", "https://feeds.arstechnica.com/arstechnica/index"],
    "science": ["https://feeds.bbci.co.uk/news/science_and_environment/rss.xml"],
    "sports": ["https://www.espn.com/espn/rss/news"],
    "entertainment": ["https://feeds.bbci.co.uk/news/entertainment_and_arts/rss.xml"],
}
TOPIC_FEED = "https://news.google.com/rss/search?q={q}+when:2d&hl=en-US&gl=US&ceid=US:en"


def _feeds(category: str) -> list[str]:
    configured = get_settings().section("news.feeds").get(category)
    urls = configured or DEFAULT_FEEDS.get(category) or DEFAULT_FEEDS["top"]
    return [urls] if isinstance(urls, str) else list(urls)


def _clean(text: str) -> str:
    text = html.unescape(re.sub(r"(?s)<[^>]+>", " ", text or ""))
    return re.sub(r"\s+", " ", text).strip()


def fetch_feed(url: str, limit: int = 10) -> list[dict]:
    r = requests.get(url, headers={"User-Agent": UA}, timeout=8)
    r.raise_for_status()
    feed = feedparser.parse(r.content)
    source = _clean(feed.feed.get("title", "")) if feed.get("feed") else ""
    items = []
    for e in feed.entries[:limit]:
        title = _clean(e.get("title", ""))
        item_source = source
        if "news.google.com" in url:
            src = e.get("source", {})
            item_source = src.get("title", "") if isinstance(src, dict) else ""
            if item_source and title.endswith(" - " + item_source):
                title = title[: -len(item_source) - 3]
            summary = ""
        else:
            summary = _clean(e.get("summary", ""))
        ts = e.get("published_parsed") or e.get("updated_parsed")
        items.append({"title": title, "summary": summary[:240], "source": item_source,
                      "published": calendar.timegm(ts) if ts else 0, "link": e.get("link", "")})
    return items


def _ago(ts: float) -> str:
    if not ts:
        return ""
    mins = int((time.time() - ts) / 60)
    if mins < 60:
        return f"{max(mins, 1)}m ago"
    if mins < 48 * 60:
        return f"{mins // 60}h ago"
    return f"{mins // 1440}d ago"


def headlines(category: str = "top", topic: str | None = None, count: int = 6) -> list[dict]:
    urls = [TOPIC_FEED.format(q=urllib.parse.quote_plus(topic))] if topic else _feeds(category)
    with ThreadPoolExecutor(max_workers=4) as pool:
        batches = list(pool.map(lambda u: _try(fetch_feed, u), urls))
    seen, items = set(), []
    for batch in batches:
        for it in batch or []:
            key = re.sub(r"\W+", "", it["title"].lower())[:60]
            if key and key not in seen:
                seen.add(key)
                items.append(it)
    items.sort(key=lambda i: i["published"], reverse=True)
    return items[:count]


def _try(fn, *args):
    try:
        return fn(*args)
    except Exception as e:
        log.warning("news feed failed (%s): %s", args[0] if args else "", e)
        return []


def format_items(items: list[dict], with_summary: bool = True, with_links: bool = False) -> str:
    lines = []
    for it in items:
        meta = ", ".join(x for x in (it["source"], _ago(it["published"])) if x)
        line = f"- {it['title']}" + (f" ({meta})" if meta else "")
        if with_summary and it["summary"]:
            line += f". {it['summary']}"
        if with_links and it.get("link"):
            line += f" [link: {it['link']}]"
        lines.append(line)
    return "\n".join(lines)


# Outlets whose articles are worth opening for someone (rather than SEO lists or forum posts).
GOOD_SOURCES = ("ign", "pc gamer", "polygon", "gamespot", "eurogamer", "gamesradar", "the verge", "kotaku",
                "vgc", "video games chronicle", "dexerto", "rock paper shotgun", "windows central", "pure xbox",
                "push square", "bbc", "reuters", "associated press", "the guardian", "ars technica", "wired",
                "techradar", "engadget", "espn", "the athletic", "variety", "hollywood reporter")
_STOP = {"the", "a", "an", "and", "or", "of", "for", "in", "on", "to", "new", "latest", "news", "update", "about"}


def _pick_article(items: list[dict], topic: str) -> dict:
    """The most relevant article from a reputable outlet, newest first among equals (items arrive newest first)."""
    words = [w for w in re.findall(r"[a-z0-9]+", topic.lower()) if w not in _STOP and not re.fullmatch(r"20\d\d", w)]

    def score(it: dict) -> float:
        title = it["title"].lower()
        hits = sum(1 for w in words if w in title) / max(1, len(words))
        source = it["source"].lower()
        good = any(re.search(rf"\b{re.escape(g)}\b", source) for g in GOOD_SOURCES)
        return hits * 2 + (1 if good else 0) - (0.5 if "youtube" in it["source"].lower() else 0)

    return max(items, key=score)  # max() keeps the first (newest) of equal scores


@registry.tool(
    "open_article",
    "Find the best fresh news article about a topic and open it in Chrome for the user to read - e.g. a new game "
    "in the genre they're playing, news about a team or product they like. Or pass a url from earlier results. "
    "Returns the headline and gist so you can tell them about it in a sentence.",
    {"topic": P("string", "What the article should be about - name a specific game, team or product (e.g. "
                "'Call of Duty 2026', 'Valorant new agent'); vague topics like 'new games' find junk", required=False),
     "url": P("string", "A specific article link to open (from earlier news results)", required=False)},
    slow=True,
)
def open_article(topic: str = "", url: str = "") -> str:
    from .apps import open_url
    if url:
        open_url(url)
        return outcome.done(f"Opened the article in Chrome: {url}")
    if not topic:
        return outcome.ask("What should the article be about?")
    items = headlines(topic=topic, count=12)
    if not items:
        return outcome.failed(f"I couldn't find a recent article about '{topic}'.")
    best = _pick_article(items, topic)
    open_url(best["link"])
    others = format_items([i for i in items if i is not best][:3], with_summary=False)
    return (outcome.done(f"Opened in Chrome: \"{best['title']}\" ({best['source']}).") +
            "\nTell the user in one or two sentences what it's about and why it's relevant to them." +
            "\n" + outcome.untrusted("article headline and other results",
                                    f"Opened: {best['title']} - {best['source']}\n{best['summary']}\n"
                                    f"Other recent headlines:\n{others}"))


@registry.tool(
    "get_news",
    "Latest news headlines. Give a topic (e.g. 'AI', 'Formula 1', 'Nvidia', 'stock market') for news about "
    "something specific, or a category: top, world, us, business, tech, science, sports, entertainment.",
    {"topic": P("string", "Specific subject to get news about", required=False),
     "category": P("string", "News category (default top)", required=False, enum=list(DEFAULT_FEEDS)),
     "count": P("integer", "How many headlines (default 6)", required=False)},
    slow=True,
)
def get_news(topic: str = "", category: str = "top", count: int = 6) -> str:
    count = max(1, min(int(count or 6), 15))
    items = headlines(category or "top", topic or None, count)
    if not items:
        return "I couldn't reach any news feeds just now."
    label = f"about '{topic}'" if topic else f"({category or 'top'})"
    return (f"Headlines {label} as of {time.strftime('%H:%M')} (open one for the user with open_article(url=...)):\n"
            + outcome.untrusted("news feeds", format_items(items, with_links=True)))
