"""Seeing and touching the screen: read text, click things by name, screenshots and window control."""
from __future__ import annotations

import logging
import re
import time
from datetime import datetime
from difflib import SequenceMatcher
from pathlib import Path

import pyautogui
from PIL import Image, ImageGrab

from .. import vision, winutil
from . import ocr, outcome
from .registry import P, ToolResult, registry

log = logging.getLogger(__name__)

CLICKABLE = {"ButtonControl", "MenuItemControl", "HyperlinkControl", "ListItemControl", "TabItemControl",
             "CheckBoxControl", "RadioButtonControl", "TreeItemControl", "SplitButtonControl", "EditControl",
             "ComboBoxControl", "DataItemControl", "MenuBarControl"}


def _norm(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()


def _label_score(target: str, label: str) -> float:
    if not target or not label:
        return 0.0
    if label == target:
        return 1.0
    if label.startswith(target) or target.startswith(label):
        return 0.9 - min(abs(len(label) - len(target)), 40) * 0.005
    if re.search(rf"\b{re.escape(target)}\b", label):
        return 0.82 - min(len(label) - len(target), 60) * 0.003
    return SequenceMatcher(None, target, label).ratio() * 0.9


def grab(rect: tuple[int, int, int, int] | None = None) -> Image.Image:
    return ImageGrab.grab(bbox=rect, all_screens=True)


def _window_shot(win: winutil.WindowInfo | None) -> tuple[Image.Image, tuple[int, int]]:
    """Screenshot of a window (or the whole primary screen) plus its top-left screen offset."""
    if win and not win.minimized:
        l, t, r, b = win.rect
        if r - l > 50 and b - t > 50:
            return grab((l, t, r, b)), (l, t)
    w, h = pyautogui.size()
    return grab((0, 0, w, h)), (0, 0)


def ocr_window_text(win: winutil.WindowInfo | None = None, full_screen: bool = False, limit: int = 6000) -> str:
    img, _ = _window_shot(None if full_screen else win)
    lines = ocr.recognize(img)
    text = "\n".join(line.text for line in lines)
    return text[:limit] if text else "(no readable text)"


# ---- locating things -----------------------------------------------------------
def _uia_find(target: str, hwnd: int, budget: float = 2.5):
    import uiautomation as auto
    t = _norm(target)
    best, best_score = None, 0.0
    deadline = time.time() + budget
    with auto.UIAutomationInitializerInThread(debug=False):
        root = auto.ControlFromHandle(hwnd)
        if root is None:
            return None
        for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=40):
            if time.time() > deadline:
                break
            try:
                name = ctrl.Name
                if not name or len(name) > 200:
                    continue
                score = _label_score(t, _norm(name))
                if score < 0.78:
                    continue
                rect = ctrl.BoundingRectangle
                if rect.width() <= 2 or rect.height() <= 2 or ctrl.IsOffscreen:
                    continue
                if ctrl.ControlTypeName in CLICKABLE:
                    score += 0.05
                if score > best_score:
                    best_score = score
                    best = ((rect.left + rect.right) // 2, (rect.top + rect.bottom) // 2, name, ctrl.ControlTypeName)
                    if score >= 1.05:
                        break
            except Exception:
                continue
    return best


def _ocr_find(target: str, img: Image.Image, offset: tuple[int, int], occurrence: int = 1):
    t = _norm(target)
    n = max(1, len(t.split()))
    candidates = []
    for line in ocr.recognize(img):
        words = line.words
        normed = [_norm(w.text) for w in words]
        for size in {n, n + 1, max(1, n - 1)}:
            for i in range(0, len(words) - size + 1):
                seg = " ".join(x for x in normed[i:i + size] if x)
                if not seg:
                    continue
                score = 1.0 if seg == t else SequenceMatcher(None, seg, t).ratio()
                if score >= 0.8:
                    group = words[i:i + size]
                    x0, y0 = min(w.x for w in group), min(w.y for w in group)
                    x1, y1 = max(w.x + w.w for w in group), max(w.y + w.h for w in group)
                    candidates.append((score, y0, x0, ((x0 + x1) // 2, (y0 + y1) // 2), " ".join(w.text for w in group)))
    if not candidates:
        return None
    top = max(c[0] for c in candidates)
    best = sorted((c for c in candidates if c[0] >= top - 0.02), key=lambda c: (c[1], c[2]))
    pick = best[min(max(occurrence, 1), len(best)) - 1]
    (x, y), label = pick[3], pick[4]
    return x + offset[0], y + offset[1], label


def locate(target: str, occurrence: int = 1, use_vision: bool = True):
    """Find something on screen. Returns (x, y, how, label) or None."""
    win = winutil.foreground_window()
    if win and occurrence == 1:
        try:
            hit = _uia_find(target, win.hwnd)
            if hit:
                return hit[0], hit[1], "accessibility", f"{hit[2]} ({hit[3].replace('Control', '').lower()})"
        except Exception:
            log.debug("UI Automation search failed", exc_info=True)
    img, offset = _window_shot(win)
    hit = _ocr_find(target, img, offset, occurrence)
    if hit:
        return hit[0], hit[1], "text", hit[2]
    taskbar = winutil.user32.FindWindowW("Shell_TrayWnd", None)
    if taskbar and occurrence == 1:
        try:
            hit = _uia_find(target, taskbar, budget=1.0)
            if hit:
                return hit[0], hit[1], "taskbar", hit[2]
        except Exception:
            pass
    full, origin = _window_shot(None)
    if offset != (0, 0) or img.size != full.size:
        hit = _ocr_find(target, full, origin, occurrence)
        if hit:
            return hit[0], hit[1], "text", hit[2]
    if use_vision:
        try:
            point = vision.locate(full, target)
            if point:
                return point[0] + origin[0], point[1] + origin[1], "vision", target
        except Exception:
            log.debug("vision locate failed", exc_info=True)
    return None


# ---- tools ---------------------------------------------------------------------
@registry.tool(
    "click_on_screen",
    "Click something visible on screen by its label or text, e.g. 'Sign in', 'File', 'Liked Songs', 'Play'. "
    "Searches the active window's controls, then on-screen text, then uses vision. Use occurrence=2 for the "
    "second match from the top.",
    {"target": P("string", "The visible label/text of the thing to click (or a short description)"),
     "button": P("string", "Mouse button (default left)", required=False, enum=["left", "right"]),
     "double": P("boolean", "Double-click (default false)", required=False),
     "occurrence": P("integer", "Which match to click when several exist (default 1)", required=False)},
    slow=True,
)
def click_on_screen(target: str, button: str = "left", double: bool = False, occurrence: int = 1) -> str:
    hit = locate(target, occurrence=occurrence)
    if not hit:
        return (f"Couldn't find '{target}' on screen. Try read_screen to see what's there, "
                "or look_at_screen for icons without text.")
    x, y, how, label = hit
    pyautogui.moveTo(x, y, duration=0.18)
    pyautogui.click(clicks=2 if double else 1, button=button)
    return f"Clicked '{label}' at ({x}, {y}) - found via {how}."


@registry.tool(
    "read_screen",
    "Read the text visible in the active window (default) or the whole screen, using local OCR. "
    "Use to check what's on screen, read a page or dialog, or verify an action worked.",
    {"scope": P("string", "window or screen (default window)", required=False, enum=["window", "screen"])},
)
def read_screen(scope: str = "window") -> str:
    win = winutil.foreground_window()
    text = ocr_window_text(win, full_screen=(scope == "screen"))
    title = f"'{win.title}' ({win.process})" if win and scope != "screen" else "the screen"
    return f"Text visible in {title}:\n{outcome.untrusted('screen', text)}"


@registry.tool(
    "look_at_screen",
    "Take a screenshot and look at it to answer a question - for images, layout, icons, charts, videos, or "
    "anything OCR text alone can't explain.",
    {"question": P("string", "What you want to know about the screen"),
     "scope": P("string", "screen or window (default screen)", required=False, enum=["screen", "window"])},
    slow=True,
)
def look_at_screen(question: str, scope: str = "screen") -> ToolResult:
    win = winutil.foreground_window() if scope == "window" else None
    img, _ = _window_shot(win)
    jpeg = vision.to_jpeg(img)
    return ToolResult(text=f"[Screenshot of the {scope} attached ({img.width}x{img.height}). Question: {question}]",
                      image=jpeg, image_mime="image/jpeg")


@registry.tool(
    "take_screenshot",
    "Save a screenshot (whole screen or active window) as a PNG in Pictures\\Screenshots.",
    {"scope": P("string", "screen or window (default screen)", required=False, enum=["screen", "window"])},
)
def take_screenshot(scope: str = "screen") -> str:
    win = winutil.foreground_window() if scope == "window" else None
    img = _window_shot(win)[0] if win else grab()
    folder = Path(winutil.known_folder("pictures") or Path.home() / "Pictures") / "Screenshots"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"Jarvis-{datetime.now():%Y%m%d-%H%M%S}.png"
    img.save(path)
    return f"Saved screenshot to {path}."


@registry.tool(
    "windows",
    "Manage open windows: list them, or focus / minimize / maximize / restore / close one by (part of) its "
    "title or app name. show_desktop minimizes everything.",
    {"action": P("string", "What to do", enum=["list", "focus", "minimize", "maximize", "restore", "close",
                                             "show_desktop"]),
     "title": P("string", "Part of the window title or app name, e.g. 'Chrome', 'Spotify'", required=False)},
)
def windows(action: str, title: str = "") -> str:
    if action == "list":
        wins = winutil.list_windows()
        active = winutil.foreground_window()
        lines = [f"- {w.title} [{w.process}]" + (" (minimized)" if w.minimized else "") +
                 (" <- active" if active and w.hwnd == active.hwnd else "") for w in wins]
        return "Open windows:\n" + "\n".join(lines) if lines else "No windows are open."
    if action == "show_desktop":
        pyautogui.hotkey("win", "d")
        return "Showing the desktop."
    if not title:
        return "Error: say which window (title or app name)."
    win = winutil.find_window(title)
    if not win:
        return f"No open window matches '{title}'."
    u = winutil.user32
    if action == "focus":
        ok = winutil.focus_window(win.hwnd)
        return f"Switched to '{win.title}'." if ok else f"Windows wouldn't let me focus '{win.title}'."
    if action == "close":
        if winutil.is_protected(win):
            return outcome.failed("That window is mine - I won't close myself.")
        u.PostMessageW(win.hwnd, 0x0010, 0, 0)  # WM_CLOSE
        time.sleep(1.0)
        still = any(w.hwnd == win.hwnd for w in winutil.list_windows())
        return outcome.unverified(f"Asked '{win.title}' to close; it's still open (maybe a save prompt).") if still \
            else outcome.done(f"Closed '{win.title}'.")
    u.ShowWindow(win.hwnd, {"minimize": 6, "maximize": 3, "restore": 9}[action])
    if action != "minimize":
        winutil.focus_window(win.hwnd)
    return f"{action.capitalize()}d '{win.title}'."
