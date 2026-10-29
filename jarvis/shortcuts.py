"""Desktop button, start-with-Windows, and restarting Jarvis."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .config import PROJECT_DIR

LAUNCHER = PROJECT_DIR / "run_jarvis.bat"
ICON = PROJECT_DIR / "jarvis.ico"
NO_WINDOW = 0x08000000


def _special(name: str) -> Path:
    out = subprocess.run(["powershell", "-NoProfile", "-Command", f"[Environment]::GetFolderPath('{name}')"],
                         capture_output=True, text=True, creationflags=NO_WINDOW, timeout=20)
    return Path(out.stdout.strip())


def desktop_link() -> Path:
    return _special("Desktop") / "Jarvis.lnk"


def startup_link() -> Path:
    return _special("Startup") / "Jarvis.lnk"


def make_link(path: Path) -> None:
    """A shortcut to run_jarvis.bat with the arc-reactor icon; the console starts minimised."""
    def q(p) -> str:
        return str(p).replace("'", "''")
    script = (f"$s = (New-Object -ComObject WScript.Shell).CreateShortcut('{q(path)}'); "
              f"$s.TargetPath = '{q(LAUNCHER)}'; $s.WorkingDirectory = '{q(PROJECT_DIR)}'; "
              f"$s.IconLocation = '{q(ICON)},0'; $s.WindowStyle = 7; $s.Description = 'Start J.A.R.V.I.S.'; $s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", script], check=True, creationflags=NO_WINDOW, timeout=30)


def set_startup(enabled: bool) -> None:
    link = startup_link()
    if enabled:
        make_link(link)
    else:
        link.unlink(missing_ok=True)


def is_running() -> bool:
    """Is a Jarvis already running? (The same named lock __main__ takes.)"""
    import ctypes
    k32 = ctypes.windll.kernel32
    handle = k32.OpenMutexW(0x00100000, False, "Local\\JARVIS-assistant-single-instance")  # SYNCHRONIZE
    if handle:
        k32.CloseHandle(handle)
        return True
    return False


def launch(after_pid: int | None = None) -> None:
    """Start Jarvis (minimised console). after_pid: wait for that process - the one restarting - to exit."""
    args = ["cmd", "/c", "start", "", "/min", str(LAUNCHER)]
    if after_pid:
        args += ["--wait-pid", str(after_pid)]
    subprocess.Popen(args, cwd=str(PROJECT_DIR), creationflags=NO_WINDOW)


def relaunch_self() -> None:
    launch(after_pid=os.getpid())
