"""Small Win32 helpers (ctypes only): windows, focus, known folders, PowerShell."""
from __future__ import annotations

import ctypes
import re
import subprocess
import threading
import uuid
from ctypes import wintypes
from dataclasses import dataclass
from difflib import SequenceMatcher

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32")
ole32 = ctypes.WinDLL("ole32")
dwmapi = ctypes.WinDLL("dwmapi")

NO_WINDOW = 0x08000000  # CREATE_NO_WINDOW for subprocesses

HWND = wintypes.HWND
user32.GetForegroundWindow.restype = HWND
user32.GetWindowTextLengthW.argtypes = [HWND]
user32.GetWindowTextW.argtypes = [HWND, wintypes.LPWSTR, ctypes.c_int]
user32.GetWindowThreadProcessId.argtypes = [HWND, ctypes.POINTER(wintypes.DWORD)]
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = [HWND]
user32.IsIconic.argtypes = [HWND]
user32.ShowWindow.argtypes = [HWND, ctypes.c_int]
user32.SetForegroundWindow.argtypes = [HWND]
user32.BringWindowToTop.argtypes = [HWND]
user32.GetWindowLongW.argtypes = [HWND, ctypes.c_int]
user32.GetWindowRect.argtypes = [HWND, ctypes.POINTER(wintypes.RECT)]
user32.PostMessageW.argtypes = [HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
user32.FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
user32.FindWindowW.restype = HWND
user32.MonitorFromWindow.argtypes = [HWND, wintypes.DWORD]
user32.MonitorFromWindow.restype = wintypes.HMONITOR
dwmapi.DwmGetWindowAttribute.argtypes = [HWND, wintypes.DWORD, ctypes.c_void_p, wintypes.DWORD]
kernel32.GetConsoleWindow.restype = HWND


def set_dpi_aware() -> None:
    """Use physical pixels everywhere so screenshots, OCR boxes and clicks line up."""
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception:
        try:
            user32.SetProcessDPIAware()
        except Exception:
            pass


_com = threading.local()


def ensure_com() -> None:
    """Initialise COM on the current thread (needed by UI Automation, SAPI, audio volume)."""
    if getattr(_com, "ready", False):
        return
    try:
        import comtypes
        comtypes.CoInitializeEx(comtypes.COINIT_APARTMENTTHREADED)
    except Exception:
        try:
            ole32.CoInitializeEx(None, 0x2)
        except Exception:
            pass
    _com.ready = True


def powershell(command: str, timeout: float = 30) -> subprocess.CompletedProcess:
    full = "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command
    return subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-ExecutionPolicy", "Bypass", "-Command", full],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout,
        creationflags=NO_WINDOW,
    )


# ---- windows -------------------------------------------------------------------
@dataclass
class WindowInfo:
    hwnd: int
    title: str
    process: str
    pid: int
    rect: tuple[int, int, int, int]  # left, top, right, bottom (visible frame, physical px)
    minimized: bool


def window_title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return pid.value


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    # DWMWA_EXTENDED_FRAME_BOUNDS excludes the invisible resize border Windows 10/11 adds.
    if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(rect), ctypes.sizeof(rect)) != 0:
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right, rect.bottom


def _process_name(pid: int) -> str:
    try:
        import psutil
        return psutil.Process(pid).name()
    except Exception:
        return ""


def _is_cloaked(hwnd: int) -> bool:
    cloaked = ctypes.c_int(0)
    dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(cloaked), ctypes.sizeof(cloaked))
    return cloaked.value != 0


def _info(hwnd: int) -> WindowInfo:
    pid = window_pid(hwnd)
    return WindowInfo(hwnd, window_title(hwnd), _process_name(pid), pid, window_rect(hwnd), bool(user32.IsIconic(hwnd)))


def list_windows() -> list[WindowInfo]:
    """Top-level app windows, roughly what Alt+Tab shows."""
    found: list[WindowInfo] = []
    proc = ctypes.WINFUNCTYPE(wintypes.BOOL, HWND, wintypes.LPARAM)

    def callback(hwnd, _):
        if not hwnd or not user32.IsWindowVisible(hwnd):
            return True
        title = window_title(hwnd)
        if not title.strip() or title in ("Program Manager", "Windows Input Experience"):
            return True
        if user32.GetWindowLongW(hwnd, -20) & 0x80:  # WS_EX_TOOLWINDOW
            return True
        if _is_cloaked(hwnd):
            return True
        found.append(_info(hwnd))
        return True

    user32.EnumWindows(proc(callback), 0)
    return found


def foreground_window() -> WindowInfo | None:
    hwnd = user32.GetForegroundWindow()
    return _info(hwnd) if hwnd else None


def find_window(query: str) -> WindowInfo | None:
    q = query.lower().strip()
    best, best_score = None, 0.0
    for w in list_windows():
        title, proc = w.title.lower(), w.process.lower().removesuffix(".exe")
        if q == proc or q == title:
            score = 1.0
        elif q in title or q in proc:
            score = 0.85
        else:
            score = max(SequenceMatcher(None, q, title).ratio(), SequenceMatcher(None, q, proc).ratio()) * 0.8
        if score > best_score:
            best, best_score = w, score
    return best if best_score >= 0.55 else None


_STOP_WORDS = {"microsoft", "the", "app", "for", "and", "windows", "google", "desktop", "player", "studio"}


def app_tokens(name: str) -> list[str]:
    return [w for w in re.findall(r"[a-z0-9]+", name.lower()) if len(w) >= 3 and w not in _STOP_WORDS]


def window_matches(win: WindowInfo, app_name: str) -> bool:
    """Does this window belong to the app the user named?"""
    full = app_name.lower().strip()
    title = win.title.lower()
    stem = win.process.lower().removesuffix(".exe")
    if not full:
        return False
    if stem == full.replace(" ", "") or title == full or title.endswith(" - " + full) or title.startswith(full + " "):
        return True
    tokens = app_tokens(app_name)
    return bool(tokens) and (stem in tokens or any(t == stem for t in tokens) or
                             (len(tokens) == 1 and tokens[0] in stem))


def protected_pids() -> set[int]:
    """Processes Jarvis must never close: itself, its children and what launched it
    (the terminal it runs in). File Explorer is excluded - closing an Explorer window never
    takes its children down, and protecting it would make 'close File Explorer' impossible."""
    try:
        import psutil
        me = psutil.Process()
        pids = {me.pid} | {c.pid for c in me.children(recursive=True)}
        for parent in me.parents():
            if parent.name().lower() in ("explorer.exe", "services.exe", "svchost.exe", "wininit.exe"):
                break
            pids.add(parent.pid)
        return pids
    except Exception:
        import os
        return {os.getpid()}


def is_protected(win: WindowInfo, protected: set[int] | None = None) -> bool:
    import sys
    protected = protected if protected is not None else protected_pids()
    if win.pid in protected:
        return True
    console = kernel32.GetConsoleWindow()
    if console and win.hwnd == console:
        return True
    if "j.a.r.v.i.s" in win.title.lower():
        return True
    try:
        import psutil
        return psutil.Process(win.pid).exe().lower() == sys.executable.lower()
    except Exception:
        return False


def focus_window(hwnd: int) -> bool:
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, 9)  # SW_RESTORE
    # A zero-length mouse move makes this process the last input source, which is
    # what Windows requires before it lets us change the foreground window.
    user32.mouse_event(0x0001, 0, 0, 0, 0)
    user32.SetForegroundWindow(hwnd)
    if user32.GetForegroundWindow() != hwnd:
        fg = user32.GetForegroundWindow()
        fg_thread = user32.GetWindowThreadProcessId(fg, None)
        me = kernel32.GetCurrentThreadId()
        user32.AttachThreadInput(me, fg_thread, True)
        user32.BringWindowToTop(hwnd)
        user32.SetForegroundWindow(hwnd)
        user32.AttachThreadInput(me, fg_thread, False)
    return user32.GetForegroundWindow() == hwnd


def is_fullscreen_foreground() -> bool:
    """True when a game/video is fullscreen - used to keep proactive chatter quiet."""
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return False
    cls = ctypes.create_unicode_buffer(64)
    user32.GetClassNameW(hwnd, cls, 64)
    if cls.value in ("Progman", "WorkerW", "Shell_TrayWnd"):
        return False

    class MONITORINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT),
                    ("rcWork", wintypes.RECT), ("dwFlags", wintypes.DWORD)]

    info = MONITORINFO(cbSize=ctypes.sizeof(MONITORINFO))
    user32.GetMonitorInfoW(user32.MonitorFromWindow(hwnd, 2), ctypes.byref(info))
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    m = info.rcMonitor
    return r.left <= m.left and r.top <= m.top and r.right >= m.right and r.bottom >= m.bottom


# ---- known folders (handles OneDrive redirection) ------------------------------------
class _GUID(ctypes.Structure):
    _fields_ = [("Data1", wintypes.DWORD), ("Data2", wintypes.WORD),
                ("Data3", wintypes.WORD), ("Data4", ctypes.c_ubyte * 8)]

    @classmethod
    def from_string(cls, text: str) -> "_GUID":
        u = uuid.UUID(text)
        g = cls(u.fields[0], u.fields[1], u.fields[2])
        for i, b in enumerate(u.bytes[8:]):
            g.Data4[i] = b
        return g


_FOLDERS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "home": "5E6C858F-0E22-4760-9AFE-EA3317B67173",
}
_FOLDER_ALIASES = {"my documents": "documents", "docs": "documents", "download": "downloads",
                   "photos": "pictures", "my pictures": "pictures", "video": "videos",
                   "user folder": "home", "profile": "home"}


def known_folder(name: str) -> str | None:
    key = re.sub(r"\s+folder$", "", name.strip().lower())
    key = _FOLDER_ALIASES.get(key, key)
    guid = _FOLDERS.get(key)
    if not guid:
        return None
    path = ctypes.c_wchar_p()
    hr = shell32.SHGetKnownFolderPath(ctypes.byref(_GUID.from_string(guid)), 0, None, ctypes.byref(path))
    if hr != 0:
        return None
    try:
        return path.value
    finally:
        ole32.CoTaskMemFree(path)


def known_folder_names() -> list[str]:
    return list(_FOLDERS)
