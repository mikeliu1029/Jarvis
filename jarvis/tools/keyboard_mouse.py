"""Typing, key presses, mouse actions and the clipboard."""
from __future__ import annotations

import re
import time

import pyautogui
import pyperclip
from pynput.keyboard import Controller as _KeyboardController

from .. import winutil
from .registry import P, registry

pyautogui.FAILSAFE = True   # slam the mouse into a screen corner to abort any automation
pyautogui.PAUSE = 0.02
_keyboard = _KeyboardController()

_ALIASES = {
    "windows": "win", "window": "win", "start": "win", "cmd": "win", "command": "win", "super": "win", "meta": "win",
    "control": "ctrl", "ctl": "ctrl", "escape": "esc", "return": "enter", "del": "delete", "ins": "insert",
    "page up": "pageup", "pgup": "pageup", "page down": "pagedown", "pgdn": "pagedown", "spacebar": "space",
    "up arrow": "up", "down arrow": "down", "left arrow": "left", "right arrow": "right",
    "arrowup": "up", "arrowdown": "down", "arrowleft": "left", "arrowright": "right",
    "option": "alt", "prtsc": "printscreen", "print screen": "printscreen", "caps lock": "capslock", "caps": "capslock",
    "plus": "+", "minus": "-", "equals": "=", "comma": ",", "period": ".", "dot": ".", "slash": "/",
    "backslash": "\\", "play": "playpause", "pause": "playpause", "play pause": "playpause",
    "next track": "nexttrack", "previous track": "prevtrack", "mute": "volumemute",
    "volume up": "volumeup", "volume down": "volumedown", "back space": "backspace",
}


def _key(name: str) -> str:
    k = name.strip().lower()
    k = _ALIASES.get(k, k)
    if k not in pyautogui.KEY_NAMES:
        raise ValueError(f"unknown key '{name}'")
    return k


def _split_combo(combo: str) -> list[str]:
    combo = combo.strip().lower()
    if combo in _ALIASES or combo in pyautogui.KEY_NAMES:
        return [combo]
    if combo.endswith("++"):
        return [*_split_combo(combo[:-2]), "+"] if combo[:-2] else ["+"]
    if "+" in combo:
        return [p.strip() for p in combo.split("+") if p.strip()]
    return combo.split()


def press_combo(combo: str) -> None:
    keys = [_key(k) for k in _split_combo(combo)]
    if not keys:
        raise ValueError("no keys given")
    if len(keys) == 1:
        pyautogui.press(keys[0])
    else:
        pyautogui.hotkey(*keys, interval=0.03)


def hotkey(*keys: str) -> None:
    pyautogui.hotkey(*keys, interval=0.03)


def press(key: str, presses: int = 1) -> None:
    pyautogui.press(key, presses=presses, interval=0.05)


def paste_text(text: str) -> None:
    try:
        previous = pyperclip.paste()
    except Exception:
        previous = None
    pyperclip.copy(text)
    time.sleep(0.05)
    pyautogui.hotkey("ctrl", "v")
    time.sleep(0.3)
    if previous is not None:
        try:
            pyperclip.copy(previous)
        except Exception:
            pass


def type_into_focus(text: str) -> None:
    if len(text) > 300:
        paste_text(text)  # much faster for long passages
    else:
        _keyboard.type(text)


@registry.tool(
    "type_text",
    "Type text into whatever currently has keyboard focus, like a person typing. Focus or click the right "
    "field first. Set press_enter to submit.",
    {"text": P("string", "Exact text to type"),
     "press_enter": P("boolean", "Press Enter afterwards (default false)", required=False)},
)
def type_text(text: str, press_enter: bool = False) -> str:
    win = winutil.foreground_window()
    type_into_focus(text)
    if press_enter:
        time.sleep(0.05)
        pyautogui.press("enter")
    where = f"'{win.title}'" if win and win.title else "the focused window"
    return f"Typed {len(text)} characters into {where}" + (" and pressed Enter." if press_enter else ".")


def _closes_window(keys: str) -> bool:
    """Shortcuts that close or quit whatever window has focus."""
    norm = re.sub(r"\s+", "", keys.lower())
    return any(k in norm for k in ("alt+f4", "f4+alt", "ctrl+q", "ctrl+shift+w"))


def _press_question(args: dict) -> str | None:
    if not _closes_window(args.get("keys", "")):
        return None
    win = winutil.foreground_window()
    if win is None or winutil.is_protected(win):
        return None  # no point asking - press_keys refuses this one outright
    return f"Press {args.get('keys')} - that closes '{win.title or 'the active window'}'?"


@registry.tool(
    "press_keys",
    "Press a key or keyboard shortcut. Join keys with '+', separate a sequence with commas. Examples: 'enter', "
    "'ctrl+t', 'ctrl+l', 'alt+tab', 'win+d', 'ctrl+shift+esc', 'tab, tab, enter', 'f5'. To close an app use "
    "close_app, not a shortcut.",
    {"keys": P("string", "Key, shortcut or comma-separated sequence"),
     "repeat": P("integer", "How many times to repeat the whole thing (default 1)", required=False)},
    confirm=_press_question,
)
def press_keys(keys: str, repeat: int = 1) -> str:
    combos = [c for c in re.split(r"\s*,\s*", keys.strip()) if c]
    repeat = max(1, min(int(repeat or 1), 50))
    if _closes_window(keys):
        # The loophole MARK LVI documented and this project then hit for real: when close_app refused
        # to close the window Jarvis runs in, the model pressed Alt+F4 on it instead. A close shortcut
        # aimed at Jarvis's own window (or the terminal/editor that launched it) is refused outright.
        win = winutil.foreground_window()
        if win is None or winutil.is_protected(win):
            from .outcome import failed
            return failed("Refused: that shortcut would close my own window or the program running me. "
                          "Don't try another way - tell the user to close it themselves if they want.")
        repeat = 1
    for _ in range(repeat):
        for combo in combos:
            press_combo(combo)
            time.sleep(0.06)
    return f"Pressed {keys}" + (f" x{repeat}" if repeat > 1 else "") + "."


@registry.tool(
    "mouse",
    "Mouse control at screen pixel coordinates (e.g. from read_screen or click_on_screen results): click, "
    "double_click, right_click, move, scroll_up, scroll_down, drag (x,y to to_x,to_y). Prefer click_on_screen "
    "when you know the label of what to click.",
    {"action": P("string", "What to do", enum=["click", "double_click", "right_click", "move", "scroll_up",
                                               "scroll_down", "drag"]),
     "x": P("integer", "X pixel (omit to use the current position)", required=False),
     "y": P("integer", "Y pixel (omit to use the current position)", required=False),
     "to_x": P("integer", "Drag destination X", required=False),
     "to_y": P("integer", "Drag destination Y", required=False),
     "amount": P("integer", "Scroll notches (default 5)", required=False)},
)
def mouse(action: str, x: int | None = None, y: int | None = None, to_x: int | None = None,
          to_y: int | None = None, amount: int = 5) -> str:
    pos = (x, y) if x is not None and y is not None else None
    if action in ("scroll_up", "scroll_down"):
        if pos:
            pyautogui.moveTo(*pos)
        notches = max(1, min(int(amount or 5), 50))
        delta = 120 * notches * (1 if action == "scroll_up" else -1)
        winutil.user32.mouse_event(0x0800, 0, 0, delta, 0)  # MOUSEEVENTF_WHEEL
        return f"Scrolled {'up' if delta > 0 else 'down'} {notches} notches."
    if action == "move":
        if not pos:
            return "Error: move needs x and y."
        pyautogui.moveTo(*pos, duration=0.15)
        return f"Moved the pointer to {pos}."
    if action == "drag":
        if not pos or to_x is None or to_y is None:
            return "Error: drag needs x, y, to_x and to_y."
        pyautogui.moveTo(*pos)
        pyautogui.dragTo(to_x, to_y, duration=0.4, button="left")
        return f"Dragged from {pos} to {(to_x, to_y)}."
    button = "right" if action == "right_click" else "left"
    clicks = 2 if action == "double_click" else 1
    if pos:
        pyautogui.moveTo(*pos, duration=0.12)
    pyautogui.click(clicks=clicks, button=button)
    return f"{action.replace('_', ' ').capitalize()} at {pos or pyautogui.position()}."


@registry.tool(
    "clipboard",
    "Read the clipboard text, or put text on the clipboard.",
    {"action": P("string", "get or set", enum=["get", "set"]),
     "text": P("string", "Text to copy (for set)", required=False)},
)
def clipboard(action: str, text: str = "") -> str:
    if action == "set":
        pyperclip.copy(text)
        return f"Copied {len(text)} characters to the clipboard."
    from .outcome import untrusted
    content = pyperclip.paste() or ""
    return f"Clipboard contents:\n{untrusted('clipboard', content[:6000])}" if content \
        else "The clipboard is empty (or holds no text)."
