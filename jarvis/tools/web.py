"""Web search and page reading, so Jarvis can answer questions with current information."""
from __future__ import annotations

import logging
import re
from concurrent.futures import ThreadPoolExecutor

import requests

from . import outcome
from .apps import UA
from .registry import P, registry

log = logging.getLogger(__name__)


def _strip_html(page: str) -> str:
    page = re.sub(r"(?is)<(script|style|noscript|svg|header|footer|nav)[^>]*>.*?</\1>", " ", page)
    page = re.sub(r"(?s)<[^>]+>", " ", page)
    return re.sub(r"[ \t\r\f\v]+", " ", page)


def fetch_text(url: str, max_chars: int = 3000, timeout: float = 8) -> str:
    r = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=timeout)
    r.raise_for_status()
    ctype = r.headers.get("content-type", "")
    if not any(k in ctype for k in ("html", "xml", "text", "json")):
        return f"(not a text page: {ctype})"
    text = ""
    try:
        import trafilatura
        text = trafilatura.extract(r.text, url=url, include_comments=False, include_tables=True,
                                   favor_precision=True) or ""
    except Exception:
        log.debug("trafilatura failed for %s", url, exc_info=True)
    if not text:
        text = _strip_html(r.text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    return text[:max_chars]


def _safe_fetch(url: str, max_chars: int) -> str:
    try:
        return fetch_text(url, max_chars)
    except Exception as e:
        log.debug("fetch failed %s: %s", url, e)
        return ""


@registry.tool(
    "web_search",
    "Search the internet. Returns the top results plus extracts from the best pages. Use for current events, "
    "facts, prices, sports scores, people, products, how-tos - anything you aren't certain of. Then answer in "
    "your own words.",
    {"query": P("string", "Search query"),
     "max_results": P("integer", "Number of results, 3-8 (default 5)", required=False)},
    slow=True,
)
def web_search(query: str, max_results: int = 5) -> str:
    from ddgs import DDGS
    max_results = max(3, min(int(max_results or 5), 8))
    try:
        results = list(DDGS().text(query, max_results=max_results) or [])
    except Exception as e:
        return f"Search failed ({type(e).__name__}: {e}). Try again or rephrase."
    if not results:
        return f"No results for '{query}'."
    out = []
    for i, r in enumerate(results, 1):
        out.append(f"[{i}] {r.get('title', '')}\n{r.get('href', '')}\n{r.get('body', '')}")
    urls = [r.get("href") for r in results[:3] if r.get("href")]
    with ThreadPoolExecutor(max_workers=3) as pool:
        extracts = list(pool.map(lambda u: _safe_fetch(u, 1800), urls))
    for i, (url, text) in enumerate(zip(urls, extracts), 1):
        if text:
            out.append(f"--- Extract from [{i}] {url} ---\n{text}")
    return f"Search results for: {query}\n" + outcome.untrusted("web search", "\n\n".join(out))


@registry.tool(
    "read_webpage",
    "Fetch a web page and return its main text (articles, docs, recipes...).",
    {"url": P("string", "Full URL")},
    slow=True,
)
def read_webpage(url: str) -> str:
    if not url.startswith(("http://", "https://")):
        url = "https://" + url
    text = fetch_text(url, max_chars=7000)
    return f"Text of {url}:\n{outcome.untrusted('web page', text)}" if text else f"No readable text found at {url}."
