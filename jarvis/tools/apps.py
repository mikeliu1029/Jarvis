"""Opening apps, the Windows taskbar search, links, folders, and searches inside sites and apps."""
from __future__ import annotations

import html
import json
import logging
import os
import re
import subprocess
import time
import urllib.parse
import webbrowser
from difflib import SequenceMatcher
from pathlib import Path

import requests

from .. import winutil
from ..config import CACHE_DIR, get_settings
from . import outcome
from .registry import P, registry

log = logging.getLogger(__name__)

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/131.0 Safari/537.36")
_APPS_CACHE = CACHE_DIR / "start_apps.json"
_JUNK = ("uninstall", "readme", "read me", "help", "documentation", "release notes", "license",
         "website", "manual", "support", "user guide")


# ---- browser --------------------------------------------------------------------------
def _chrome_path() -> str | None:
    import winreg
    for hive in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
        try:
            with winreg.OpenKey(hive, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\chrome.exe") as k:
                path = winreg.QueryValue(k, None)
                if path and os.path.exists(path):
                    return path
        except OSError:
            continue
    for path in (r"C:\Program Files\Google\Chrome\Application\chrome.exe",
                 os.path.expandvars(r"%LOCALAPPDATA%\Google\Chrome\Application\chrome.exe")):
        if os.path.exists(path):
            return path
    return None


def open_url(url: str) -> None:
    """Open a web link in the browser set in settings ([apps] browser): Chrome by default."""
    if url.lower().startswith(("http://", "https://")):
        choice = str(get_settings().get("apps.browser", "chrome")).lower()
        if choice == "chrome":
            chrome = _chrome_path()
            if chrome:
                subprocess.Popen([chrome, url], creationflags=winutil.NO_WINDOW)
                return
    webbrowser.open(url)


# ---- the Start-menu app index --------------------------------------------------------
def start_apps(refresh: bool = False) -> list[dict]:
    """Every app in the Start menu (desktop and Store apps), cached for a day."""
    if not refresh and _APPS_CACHE.exists() and time.time() - _APPS_CACHE.stat().st_mtime < 86400:
        try:
            return json.loads(_APPS_CACHE.read_text(encoding="utf-8"))
        except ValueError:
            pass
    proc = winutil.powershell("Get-StartApps | Select-Object Name, AppID | ConvertTo-Json -Compress", timeout=45)
    try:
        data = json.loads(proc.stdout or "[]")
    except ValueError:
        log.warning("Get-StartApps returned unexpected output: %s", proc.stderr[:300])
        data = []
    if isinstance(data, dict):
        data = [data]
    apps = [{"name": d["Name"], "id": d["AppID"]} for d in data if d.get("Name") and d.get("AppID")]
    if apps:
        _APPS_CACHE.parent.mkdir(parents=True, exist_ok=True)
        _APPS_CACHE.write_text(json.dumps(apps, ensure_ascii=False), encoding="utf-8")
    return apps


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9+#]+", " ", text.lower()).strip()


def match_app(query: str, apps: list[dict]) -> tuple[dict | None, float]:
    aliases = {_norm(k): v for k, v in get_settings().section("apps.aliases").items()}
    q = _norm(query)
    q = _norm(aliases.get(q, q))
    if not q:
        return None, 0.0
    best, best_score = None, 0.0
    short = len(q) < 4  # "wp" must not match "WPS Office": short names only match a whole word
    for app in apps:
        n = _norm(app["name"])
        if not n:
            continue
        if short:
            if n == q or q in n.split():
                score = 0.95 if n == q else 0.8
                if score > best_score:
                    best, best_score = app, score
            continue
        if n == q:
            score = 1.0
        elif n.startswith(q):
            score = 0.9 - min(len(n) - len(q), 30) * 0.004
        elif re.search(rf"\b{re.escape(q)}\b", n):
            score = 0.82 - min(len(n) - len(q), 30) * 0.004
        elif q in n:
            score = 0.7
        else:
            score = SequenceMatcher(None, q, n).ratio() * 0.85
        if any(j in n for j in _JUNK) and not any(j in q for j in _JUNK):
            score -= 0.35
        if score > best_score:
            best, best_score = app, score
    return best, best_score


def launch_app_id(app_id: str) -> None:
    subprocess.Popen(["explorer.exe", f"shell:AppsFolder\\{app_id}"], creationflags=winutil.NO_WINDOW)


# ---- tools ---------------------------------------------------------------------------
def _app_windows(app_name: str) -> list[winutil.WindowInfo]:
    protected = winutil.protected_pids()
    return [w for w in winutil.list_windows()
            if winutil.window_matches(w, app_name) and not winutil.is_protected(w, protected)]


def _wait_for_window(app_name: str, before: set[int], timeout: float = 9.0) -> winutil.WindowInfo | None:
    """Watch for a window of the app to appear - the evidence that it actually opened."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.4)
        for w in winutil.list_windows():
            if w.hwnd not in before and winutil.window_matches(w, app_name):
                return w
    return None


@registry.tool(
    "open_app",
    "Open an installed application by name, e.g. 'Spotify', 'Chrome', 'Word', 'Calculator', 'Settings', "
    "'File Explorer', 'Discord', 'Steam'. If it's already running it is brought to the front instead of "
    "starting a second copy. Games (Steam/Epic) also come back with their latest news. Falls back to Windows search.",
    {"name": P("string", "Application name")},
    slow=True,
)
def open_app(name: str) -> str:
    app, score = match_app(name, start_apps())
    if score < 0.75:  # perhaps newly installed - refresh the index once
        app2, score2 = match_app(name, start_apps(refresh=True))
        if score2 > score:
            app, score = app2, score2
    label = app["name"] if app and score >= 0.62 else name

    # Raise, don't duplicate: "open Spotify" while it sits behind three windows means "show me Spotify".
    running = _app_windows(label)
    if running and winutil.focus_window(running[0].hwnd):
        return outcome.done(f"{label} was already running - brought it to the front.")

    before = {w.hwnd for w in winutil.list_windows()}
    if app and score >= 0.62:
        if _is_game(app):
            return _launch_game(app)
        launch_app_id(app["id"])
        win = _wait_for_window(label, before)
        if win:
            return outcome.done(f"Opened {label}; its window is up ('{win.title}').")
        return outcome.unverified(f"Asked Windows to open {label}, but no window has appeared yet - it may still be "
                                  "loading or start in the tray. Do NOT try to open it another way; just tell the "
                                  "user it's on its way.")
    windows_search(name, open_result=True)
    return outcome.unverified(f"'{name}' isn't in the Start menu, so I searched for it with Windows search and "
                              "opened the top result - I can't confirm what that was. Don't try other ways.")


# ---- games ---------------------------------------------------------------------------
_GAME_SCHEMES = ("steam://", "com.epicgames.launcher://", "uplay://", "origin2://", "goggalaxy://")


KNOWN_GAMES = ("valorant", "league of legends", "fortnite", "minecraft", "overwatch", "apex legends",
               "rocket league", "roblox", "genshin impact", "call of duty", "battlefield", "counter-strike",
               "world of warcraft", "diablo", "hearthstone", "rainbow six", "destiny 2", "elden ring")


def _is_game(app: dict) -> bool:
    """Store launchers register games as protocol links (steam://rungameid/1091500); Riot, Battle.net and
    friends use ordinary shortcuts, so well-known titles (plus [apps] games in settings) count too."""
    if str(app.get("id", "")).lower().startswith(_GAME_SCHEMES):
        return True
    name = app.get("name", "").lower()
    extra = [g.lower() for g in get_settings().get("apps.games", [])]
    return any(g in name for g in (*KNOWN_GAMES, *extra))


def _game_news(title: str) -> str:
    """The latest about a game: recent headlines plus a quick web search for its newest update."""
    from concurrent.futures import ThreadPoolExecutor

    from .news import format_items, headlines

    def news():
        return headlines(topic=title, count=5)

    def search():
        from ddgs import DDGS
        return list(DDGS().text(f"{title} latest update patch news", max_results=4) or [])

    with ThreadPoolExecutor(max_workers=2) as pool:
        f_news, f_search = pool.submit(news), pool.submit(search)
        parts = []
        try:
            items = f_news.result(timeout=12)
            if items:
                parts.append("Recent headlines:\n" + format_items(items))
        except Exception as e:
            log.debug("game headlines failed: %s", e)
        try:
            results = f_search.result(timeout=12)
            if results:
                parts.append("Web results:\n" + "\n".join(f"- {r.get('title', '')}: {r.get('body', '')}"
                                                          for r in results))
        except Exception as e:
            log.debug("game web search failed: %s", e)
    return "\n\n".join(parts)


def _launch_game(app: dict) -> str:
    title = app["name"].replace("®", "").replace("™", "").strip()
    if str(app["id"]).lower().startswith(_GAME_SCHEMES):
        os.startfile(app["id"])  # hands the link to Steam / Epic / Ubisoft, which starts the game
    else:
        launch_app_id(app["id"])  # Riot, Battle.net, ... shortcuts
    _remember_game(title)
    msg = (f"{_launcher_name(app['id'])} is launching {title}. Games take a minute or so to appear - do NOT try to "
           "open it again, search for it, or click anything.")
    if not get_settings().get("apps.game_news_on_launch", True):
        return outcome.started(msg)
    news = _game_news(title)
    if not news:
        return outcome.started(msg + " Tell the user it's starting.")
    return (outcome.started(msg) +
            f"\nFirst finish anything else the user asked for in the same breath (another app, an article) - but don't "
            f"open, search for or click on {title} again. Then tell the user, in at most three spoken sentences: that "
            f"{title} is starting; the newest update or news about {title} from the results below (a new patch, DLC, "
            "an event); and one line of context if the results give it (a related show or anime, a sequel). Don't "
            "bring back the start-up headlines. Use only facts from these results. Don't promise to say when it has "
            "loaded - you can't tell."
            f"\n\nResults about {title}:\n" + outcome.untrusted(f"news about {title}", news))


def _remember_game(title: str) -> None:
    """Games you play are interests: they steer the start-up article and 'what's relevant' suggestions."""
    try:
        from ..memory import get_memory
        get_memory().save("preferences", "plays_" + re.sub(r"[^a-z0-9]+", "_", title.lower()).strip("_"),
                          f"plays {title} (last launched {time.strftime('%d %b %Y')})")
    except Exception:
        log.debug("couldn't record game in memory", exc_info=True)


def _launcher_name(app_id: str) -> str:
    if not app_id.lower().startswith(_GAME_SCHEMES):
        return "Windows"
    for scheme, name in (("steam", "Steam"), ("epicgames", "Epic"), ("uplay", "Ubisoft Connect"),
                         ("origin", "EA"), ("goggalaxy", "GOG Galaxy")):
        if scheme in app_id.lower():
            return name
    return "The launcher"


def _close_question(args: dict) -> str | None:
    """Closing the window the user is working in needs a yes - "quit" misheard as "close this"
    must never take their editor down."""
    name = args.get("name", "")
    protected = winutil.protected_pids()
    targets = [w for w in winutil.list_windows()
               if winutil.window_matches(w, name) and not winutil.is_protected(w, protected)]
    fg = winutil.foreground_window()
    if fg and any(w.hwnd == fg.hwnd for w in targets):
        return f"Close {name} - the window you're working in right now?"
    if len(targets) > 3:
        return f"Close all {len(targets)} {name} windows?"
    return None


@registry.tool(
    "close_app",
    "Close a running application the user names (politely, like clicking its X, so it can ask to save work). "
    "Only when the user explicitly asks to close an app - never to end the conversation. Never closes Jarvis.",
    {"name": P("string", "Application name, e.g. 'Spotify', 'Notepad', 'Chrome'")},
    confirm=_close_question,
)
def close_app(name: str) -> str:
    protected = winutil.protected_pids()
    matches = [w for w in winutil.list_windows() if winutil.window_matches(w, name)]
    targets = [w for w in matches if not winutil.is_protected(w, protected)]
    if not targets:
        if matches:
            return outcome.failed(f"The only '{name}' windows open belong to me - I won't close myself.")
        return outcome.failed(f"Nothing called '{name}' is open.")
    for w in targets:
        winutil.user32.PostMessageW(w.hwnd, 0x0010, 0, 0)  # WM_CLOSE: asks, never kills
    deadline = time.time() + 4
    remaining = targets
    while time.time() < deadline and remaining:
        time.sleep(0.4)
        open_now = {w.hwnd for w in winutil.list_windows()}
        remaining = [w for w in targets if w.hwnd in open_now]
    closed = len(targets) - len(remaining)
    if not remaining:
        return outcome.done(f"Closed {name} ({closed} window{'s' if closed != 1 else ''}).")
    return outcome.unverified(f"Asked {name} to close; {len(remaining)} window(s) are still open - it may be "
                              "asking whether to save your work.")


@registry.tool(
    "windows_search",
    "Use the Windows taskbar search box: opens it, types the query, then opens the top result (default) or "
    "reads back what's listed. Good for apps, Settings pages, files, and anything else on this PC.",
    {"query": P("string", "What to search for"),
     "open_result": P("boolean", "Press Enter to open the top result (default true); false just shows and reads "
                                 "the results", required=False)},
)
def windows_search(query: str, open_result: bool = True) -> str:
    from .keyboard_mouse import hotkey, press, type_into_focus
    hotkey("win", "s")
    time.sleep(0.9)
    type_into_focus(query)
    time.sleep(1.4)
    if open_result:
        before = winutil.foreground_window()
        press("enter")
        time.sleep(1.5)
        after = winutil.foreground_window()
        if after and (not before or after.hwnd != before.hwnd) and "search" not in after.title.lower():
            return outcome.done(f"Searched Windows for '{query}' and opened the top result: '{after.title}'.")
        return outcome.unverified(f"Searched Windows for '{query}' and pressed Enter on the top result, but I "
                                  "can't confirm what opened.")
    from .screen import ocr_window_text
    text = ocr_window_text(winutil.foreground_window())
    return (f"Windows search for '{query}' - visible results:\n{outcome.untrusted('screen', text)}\n"
            "(Use click_on_screen with a result's name to open it, or press_keys 'esc' to close search.)")


def _looks_like_domain(text: str) -> bool:
    return bool(re.fullmatch(r"(www\.)?[a-z0-9-]+(\.[a-z0-9-]+)*\.[a-z]{2,}(/\S*)?", text.lower()))


@registry.tool(
    "open_link_or_path",
    "Open a website (URL or domain), a file or folder path, a standard folder (Downloads, Documents, Desktop, "
    "Pictures, Music, Videos), or an app link such as 'ms-settings:bluetooth', 'ms-settings:display', "
    "'spotify:', 'mailto:someone@example.com', 'steam://open/games'.",
    {"target": P("string", "URL, domain, path, folder name or URI")},
)
def open_link_or_path(target: str) -> str:
    t = target.strip().strip('"').strip("'")
    folder = winutil.known_folder(t)
    if folder:
        os.startfile(folder)
        return f"Opened {folder}."
    if re.match(r"^[a-z][a-z0-9+.-]*:", t, re.I) and not re.match(r"^[a-z]:[\\/]", t, re.I):
        if t.lower().startswith(("http://", "https://")):
            open_url(t)
        else:
            os.startfile(t)
        return f"Opened {t}."
    if _looks_like_domain(t):
        open_url("https://" + t)
        return f"Opened https://{t}."
    first, _, rest = t.replace("/", "\\").partition("\\")
    base = winutil.known_folder(first) if not os.path.isabs(t) else None
    path = Path(base) / rest if base else Path(os.path.expandvars(os.path.expanduser(t)))
    if path.exists():
        os.startfile(str(path))
        return f"Opened {path}."
    return f"Couldn't find '{target}'. Try find_files to locate it, or windows_search."


SITES = {
    "google": "https://www.google.com/search?q={q}",
    "youtube": "https://www.youtube.com/results?search_query={q}",
    "maps": "https://www.google.com/maps/search/{q}",
    "images": "https://www.google.com/search?tbm=isch&q={q}",
    "news": "https://news.google.com/search?q={q}",
    "wikipedia": "https://en.wikipedia.org/w/index.php?search={q}",
    "amazon": "https://www.amazon.com/s?k={q}",
    "github": "https://github.com/search?q={q}",
    "reddit": "https://www.reddit.com/search/?q={q}",
    "netflix": "https://www.netflix.com/search?q={q}",
    "steam": "https://store.steampowered.com/search/?term={q}",
    "spotify": "spotify:search:{q}",
}


def first_youtube_video(query: str) -> tuple[str | None, str | None]:
    url = "https://www.youtube.com/results?search_query=" + urllib.parse.quote_plus(query)
    page = requests.get(url, headers={"User-Agent": UA, "Accept-Language": "en-US,en;q=0.9"}, timeout=10).text
    m = re.search(r'"videoRenderer":\{"videoId":"([\w-]{11})"', page)
    if m:
        chunk = page[m.end(): m.end() + 4000]
        t = re.search(r'"title":\{"runs":\[\{"text":"((?:[^"\\]|\\.)*)"', chunk)
        title = json.loads(f'"{t.group(1)}"') if t else None
        return f"https://www.youtube.com/watch?v={m.group(1)}", title
    ids = re.findall(r"watch\?v=([\w-]{11})", page)
    return (f"https://www.youtube.com/watch?v={ids[0]}", None) if ids else (None, None)


def _spotify_play_top_result() -> bool:
    """Best effort: press the 'Play <song>' button of Spotify's top search result."""
    import uiautomation as auto
    win = winutil.find_window("spotify")
    if not win:
        return False
    with auto.UIAutomationInitializerInThread(debug=False):
        root = auto.ControlFromHandle(win.hwnd)
        deadline = time.time() + 3
        for ctrl, _ in auto.WalkControl(root, includeTop=False, maxDepth=40):
            if time.time() > deadline:
                break
            try:
                name = ctrl.Name or ""
                if ctrl.ControlTypeName == "ButtonControl" and name.lower().startswith("play ") and len(name) > 6:
                    ctrl.Click(simulateMove=False)
                    return True
            except Exception:
                continue
    return False


@registry.tool(
    "search_site",
    "Open a search on a website or app (web pages open in Chrome): google, youtube, maps, images, news, wikipedia, "
    "amazon, github, reddit, netflix, steam, spotify. With play_first=true on youtube it plays the top video - use "
    "that for trailers ('the trailer' = youtube, query '<game or film> official trailer', play_first=true). On "
    "spotify it tries to play the top result.",
    {"site": P("string", "Where to search", enum=list(SITES)),
     "query": P("string", "What to search for"),
     "play_first": P("boolean", "Play the top result (youtube/spotify)", required=False)},
)
def search_site(site: str, query: str, play_first: bool = False) -> str:
    site = site.lower().strip()
    q = urllib.parse.quote_plus(query) if site != "spotify" else urllib.parse.quote(query)
    if site == "youtube" and play_first:
        url, title = first_youtube_video(query)
        if url:
            open_url(url)
            return f"Playing '{html.unescape(title)}' on YouTube." if title else f"Playing the top YouTube result for '{query}'."
    if site == "spotify":
        try:
            os.startfile(SITES["spotify"].format(q=q))
        except OSError:
            open_url(f"https://open.spotify.com/search/{q}")
            return f"Spotify app not found - opened the Spotify web search for '{query}'."
        if play_first:
            time.sleep(3.5)
            if _spotify_play_top_result():
                return f"Playing the top Spotify result for '{query}'."
            return f"Spotify search for '{query}' is open, but I couldn't find a play button to press."
        return f"Searched Spotify for '{query}'."
    url = SITES.get(site, SITES["google"]).format(q=q)
    open_url(url)
    return f"Opened a {site} search for '{query}'."
