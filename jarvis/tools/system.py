"""PC health, processes, PowerShell commands, media/volume keys and power actions."""
from __future__ import annotations

import ctypes
import re
import shutil
import subprocess
import time
from datetime import datetime

import psutil

from .. import undo as undo_stack
from .. import winutil
from ..config import get_settings
from . import outcome
from .registry import P, registry


def gpu_status() -> str:
    exe = shutil.which("nvidia-smi")
    if not exe:
        return ""
    try:
        out = subprocess.run([exe, "--query-gpu=name,utilization.gpu,memory.used,memory.total,temperature.gpu",
                              "--format=csv,noheader,nounits"], capture_output=True, text=True, timeout=5,
                             creationflags=winutil.NO_WINDOW).stdout.strip()
        name, util, used, total, temp = [x.strip() for x in out.splitlines()[0].split(",")]
        return f"GPU {name}: {util}% busy, {int(used) / 1024:.1f} of {int(total) / 1024:.1f} GB VRAM, {temp}°C"
    except Exception:
        return ""


def _top_processes(n: int = 5) -> tuple[list, list]:
    procs = list(psutil.process_iter(["name"]))
    for p in procs:
        try:
            p.cpu_percent(None)
        except psutil.Error:
            pass
    time.sleep(0.6)
    cpu, mem = {}, {}
    cores = psutil.cpu_count() or 1
    for p in procs:
        try:
            name = p.info["name"] or "?"
            if name in ("System Idle Process", "Idle"):
                continue
            cpu[name] = cpu.get(name, 0) + p.cpu_percent(None) / cores
            mem[name] = mem.get(name, 0) + p.memory_info().rss
        except psutil.Error:
            continue
    top_cpu = sorted(cpu.items(), key=lambda kv: kv[1], reverse=True)[:n]
    top_mem = sorted(mem.items(), key=lambda kv: kv[1], reverse=True)[:n]
    return top_cpu, top_mem


@registry.tool(
    "system_status",
    "Check the PC: CPU, memory, GPU, disk space, battery, uptime, the busiest processes and the active window.",
    {},
)
def system_status() -> str:
    vm = psutil.virtual_memory()
    lines = [f"CPU {psutil.cpu_percent(interval=0.4):.0f}% busy ({psutil.cpu_count()} threads)",
             f"Memory {vm.percent:.0f}% used ({vm.used / 2**30:.1f} of {vm.total / 2**30:.0f} GB)"]
    gpu = gpu_status()
    if gpu:
        lines.append(gpu)
    for part in psutil.disk_partitions():
        if "fixed" in part.opts or part.fstype:
            try:
                du = psutil.disk_usage(part.mountpoint)
                lines.append(f"Drive {part.device.rstrip(chr(92))} {du.free / 2**30:.0f} GB free of {du.total / 2**30:.0f} GB")
            except (PermissionError, OSError):
                pass
    bat = psutil.sensors_battery()
    if bat:
        lines.append(f"Battery {bat.percent:.0f}% ({'charging' if bat.power_plugged else 'on battery'})")
    up = time.time() - psutil.boot_time()
    lines.append(f"Uptime {int(up // 86400)}d {int(up % 86400 // 3600)}h {int(up % 3600 // 60)}m")
    top_cpu, top_mem = _top_processes()
    lines.append("Busiest (CPU): " + ", ".join(f"{n} {v:.0f}%" for n, v in top_cpu))
    lines.append("Most memory: " + ", ".join(f"{n} {v / 2**30:.1f} GB" for n, v in top_mem))
    win = winutil.foreground_window()
    if win:
        lines.append(f"Active window: {win.title} [{win.process}]")
    return "\n".join(lines)


def _kill_question(args: dict) -> str | None:
    return f"End every process named '{args.get('name')}'?" if args.get("action") == "kill" else None


@registry.tool(
    "processes",
    "List running processes (optionally filtered by name) or end (kill) a program by process name.",
    {"action": P("string", "list or kill", enum=["list", "kill"]),
     "name": P("string", "Process name or part of it, e.g. 'chrome' or 'notepad.exe'", required=False)},
    confirm=_kill_question,
)
def processes(action: str, name: str = "") -> str:
    q = name.lower().removesuffix(".exe")
    matches = [p for p in psutil.process_iter(["pid", "name", "memory_info"])
               if not q or q in (p.info["name"] or "").lower()]
    if action == "kill":
        if not q:
            return "Error: give a process name to end."
        killed = 0
        for p in matches:
            try:
                p.terminate()
                killed += 1
            except psutil.Error:
                pass
        return f"Ended {killed} process(es) matching '{name}'." if killed else f"No running process matches '{name}'."
    groups: dict[str, list] = {}
    for p in matches:
        groups.setdefault(p.info["name"] or "?", []).append(p)
    rows = sorted(groups.items(), key=lambda kv: -sum((x.info["memory_info"].rss if x.info["memory_info"] else 0) for x in kv[1]))
    return "\n".join(f"- {n}: {len(ps)} process(es), {sum((x.info['memory_info'].rss if x.info['memory_info'] else 0) for x in ps) / 2**20:.0f} MB"
                     for n, ps in rows[:40]) or f"Nothing matches '{name}'."


_SAFE = re.compile(
    r"^(get-|test-|measure-|select-|sort-|format-|where-object|out-string|convertto-|resolve-|"
    r"ipconfig|ping|tracert|nslookup|hostname|whoami|systeminfo|tasklist|netstat|nvidia-smi|where(\.exe)?\b|"
    r"dir\b|ls\b|cat\b|type\b|echo\b|git (status|log|diff|branch|remote)|python --version|winget (list|search))",
    re.I)


def _command_question(args: dict) -> str | None:
    mode = str(get_settings().get("safety.confirm_commands", "risky")).lower()
    cmd = args.get("command", "")
    if mode == "never":
        return None
    if mode == "risky" and ">" not in cmd:
        segments = [s.strip() for s in re.split(r"[|;&\n]+", cmd) if s.strip()]
        if segments and all(_SAFE.match(s) for s in segments):
            return None
    return f"I'd like to run this PowerShell command: {cmd}"


_POWER_CMD = re.compile(r"(stop-computer|restart-computer|shutdown(\.exe)?\s|logoff|"
                        r"rundll32.*(lockworkstation|setsuspendstate))", re.I)


@registry.tool(
    "run_command",
    "Run a PowerShell command on this PC and return its output - for inspecting the system (network, drivers, "
    "installed software, git status...) or doing something no other tool covers. Anything that changes the "
    "system asks the user first.",
    {"command": P("string", "PowerShell command"),
     "folder": P("string", "Working folder (optional)", required=False)},
    confirm=_command_question, slow=True,
)
def run_command(command: str, folder: str = "") -> str:
    if _POWER_CMD.search(command + " "):
        return outcome.failed("Not via a command - use the power tool for shutting down, restarting, sleep or "
                              "signing out (it has a safe delay and can be cancelled).")
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command",
             "[Console]::OutputEncoding=[Text.Encoding]::UTF8; " + command],
            capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=90,
            cwd=folder or None, creationflags=winutil.NO_WINDOW)
    except subprocess.TimeoutExpired:
        return "The command took longer than 90 seconds and was stopped."
    out = (proc.stdout or "").strip()
    err = (proc.stderr or "").strip()
    text = f"Exit code {proc.returncode}."
    if out:
        text += "\nOutput:\n" + out[-5000:]
    if err:
        text += "\nErrors:\n" + err[-1500:]
    return text


_MEDIA = {"play_pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2,
          "volume_up": 0xAF, "volume_down": 0xAE, "mute": 0xAD}


def _tap(vk: int, times: int = 1) -> None:
    for _ in range(times):
        winutil.user32.keybd_event(vk, 0, 0x1, 0)
        winutil.user32.keybd_event(vk, 0, 0x1 | 0x2, 0)
        time.sleep(0.03)


def _speakers():
    from pycaw.pycaw import AudioUtilities
    return AudioUtilities.GetSpeakers().EndpointVolume


@registry.tool(
    "media_control",
    "Control music/video playback and system volume: play_pause, next, previous, stop, volume_up, volume_down, "
    "mute (toggle), set_volume (0-100), get_volume.",
    {"action": P("string", "What to do", enum=[*_MEDIA, "set_volume", "get_volume"]),
     "level": P("integer", "Volume 0-100 for set_volume, or steps for volume_up/down (default 5)", required=False)},
)
def media_control(action: str, level: int | None = None) -> str:
    if action == "get_volume":
        vol = _speakers()
        muted = " (muted)" if vol.GetMute() else ""
        return f"Volume is at {round(vol.GetMasterVolumeLevelScalar() * 100)}%{muted}."
    if action == "set_volume":
        if level is None:
            return outcome.ask("What level, from 0 to 100?")
        target = max(0, min(int(level), 100))
        vol = _speakers()
        before, was_muted = vol.GetMasterVolumeLevelScalar(), vol.GetMute()
        vol.SetMute(0, None)
        vol.SetMasterVolumeLevelScalar(target / 100, None)
        now = round(vol.GetMasterVolumeLevelScalar() * 100)  # read it back - don't assume

        def reverse() -> str:
            v = _speakers()
            v.SetMasterVolumeLevelScalar(before, None)
            v.SetMute(was_muted, None)
            return f"Volume back to {round(before * 100)}%."
        undo_stack.register(f"volume {round(before * 100)}% -> {target}%", reverse)
        return outcome.done(f"Volume is now {now}%.") if abs(now - target) <= 1 \
            else outcome.unverified(f"Asked for {target}%, but it reads {now}%.")
    if action in ("volume_up", "volume_down"):
        _tap(_MEDIA[action], max(1, min(int(level or 5), 50)))
        return f"Volume {'up' if action == 'volume_up' else 'down'}."
    _tap(_MEDIA[action])
    return {"play_pause": "Toggled play/pause.", "next": "Skipped to the next track.",
            "previous": "Back to the previous track.", "stop": "Stopped playback.", "mute": "Toggled mute."}[action]


_THEME_KEY = r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize"


def _read_theme() -> tuple[int, int]:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _THEME_KEY) as k:
        apps = winreg.QueryValueEx(k, "AppsUseLightTheme")[0]
        system = winreg.QueryValueEx(k, "SystemUsesLightTheme")[0]
    return int(apps), int(system)


def _write_theme(apps: int, system: int) -> None:
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, _THEME_KEY, 0, winreg.KEY_SET_VALUE) as k:
        winreg.SetValueEx(k, "AppsUseLightTheme", 0, winreg.REG_DWORD, apps)
        winreg.SetValueEx(k, "SystemUsesLightTheme", 0, winreg.REG_DWORD, system)
    # Tell running apps the colour scheme changed (HWND_BROADCAST, WM_SETTINGCHANGE).
    winutil.user32.SendMessageTimeoutW(0xFFFF, 0x001A, 0, "ImmersiveColorSet", 0x0002, 300, None)


@registry.tool(
    "display_settings",
    "Windows display settings: dark mode on/off/toggle, and screen brightness (laptop screens; most external "
    "monitors don't let Windows change it).",
    {"action": P("string", "What to change", enum=["dark_mode_on", "dark_mode_off", "toggle_dark_mode",
                                                   "set_brightness", "get_brightness"]),
     "level": P("integer", "Brightness 0-100 for set_brightness", required=False)},
)
def display_settings(action: str, level: int | None = None) -> str:
    if "dark" in action:
        before = _read_theme()
        dark_now = before[0] == 0
        want_dark = {"dark_mode_on": True, "dark_mode_off": False}.get(action, not dark_now)
        value = 0 if want_dark else 1
        _write_theme(value, value)
        after = _read_theme()
        undo_stack.register(f"dark mode {'on' if want_dark else 'off'}",
                            lambda: (_write_theme(*before), "Theme restored.")[1])
        return outcome.done(f"Dark mode is {'on' if want_dark else 'off'}.") if after == (value, value) \
            else outcome.unverified("Changed the theme setting, but it didn't read back as expected.")
    if action == "get_brightness":
        proc = winutil.powershell("(Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness)"
                                  ".CurrentBrightness", timeout=10)
        val = (proc.stdout or "").strip()
        return f"Brightness is {val}%." if val.isdigit() else outcome.failed(
            "This display doesn't report brightness to Windows (common for desktop monitors).")
    if level is None:
        return outcome.ask("What brightness, from 0 to 100?")
    target = max(0, min(int(level), 100))
    proc = winutil.powershell(
        "$m = Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightnessMethods -ErrorAction Stop; "
        f"Invoke-CimMethod -InputObject $m -MethodName WmiSetBrightness -Arguments @{{Timeout=1; Brightness={target}}}"
        " | Out-Null; (Get-CimInstance -Namespace root/WMI -ClassName WmiMonitorBrightness).CurrentBrightness",
        timeout=15)
    val = (proc.stdout or "").strip().splitlines()[-1:] or [""]
    if val[0].isdigit():
        return outcome.done(f"Brightness is now {val[0]}%.")
    return outcome.failed("This display doesn't let Windows change its brightness - external monitors usually "
                          "need their own buttons.")


def _power_question(args: dict) -> str | None:
    action = args.get("action")
    if action in ("lock", "cancel") or not get_settings().get("safety.confirm_power", True):
        return None
    return {"sleep": "Put the PC to sleep?", "shutdown": "Shut down the PC in one minute?",
            "restart": "Restart the PC in one minute?", "sign_out": "Sign out of Windows?",
            "shutdown_now": "Shut the PC down right now? Unsaved work could be lost.",
            "restart_now": "Restart the PC right now? Unsaved work could be lost."}.get(action)


@registry.tool(
    "open_settings",
    "Open Jarvis's own Settings window - for 'open settings', 'change your voice', 'use my headset mic', "
    "'connect Google', 'change your personality' and similar. The user changes things there themselves.",
    {"page": P("string", "Which page to open", required=False,
               enum=["start", "you", "voice", "google", "briefing", "display", "apps", "safety", "personality",
                     "offline", "system"])},
)
def open_settings(page: str = "") -> str:
    from ..events import bus
    from ..hud import settings_window
    if settings_window.ATTACHED:
        bus.emit("open_settings", page=page or "")
    else:
        import sys

        from ..config import PROJECT_DIR
        subprocess.Popen([sys.executable, "-m", "jarvis", "--settings", page or ""], cwd=str(PROJECT_DIR),
                         creationflags=winutil.NO_WINDOW)
    return outcome.done("Opened the Settings window" + (f" on the {page} page" if page else "") + ".")


@registry.tool(
    "power",
    "Lock the PC, sleep, sign out, or shut down / restart the PC (with a one-minute delay, or _now when the user "
    "wants it immediately); 'cancel' aborts a pending shutdown or restart. close_jarvis closes Jarvis himself - "
    "that's what 'Jarvis, shut down / power down / go to sleep / that's all for today' means; the PC is only "
    "shut down when the user says PC or computer.",
    {"action": P("string", "What to do", enum=["lock", "sleep", "sign_out", "shutdown", "restart", "shutdown_now",
                                               "restart_now", "cancel", "close_jarvis"])},
    confirm=_power_question,
)
def power(action: str) -> str:
    if action == "lock":
        winutil.user32.LockWorkStation()
        return "Locked."
    if action == "sleep":
        ctypes.windll.powrprof.SetSuspendState(0, 1, 0)
        return "Going to sleep."
    if action == "close_jarvis":
        import threading

        from ..events import bus
        threading.Timer(6.0, lambda: bus.emit("quit")).start()  # time to say goodbye first
        return outcome.done("Jarvis closes in a few seconds. Say a short goodbye - no tool calls.")
    commands = {"shutdown": ["shutdown", "/s", "/t", "60"], "restart": ["shutdown", "/r", "/t", "60"],
                "shutdown_now": ["shutdown", "/s", "/t", "0"], "restart_now": ["shutdown", "/r", "/t", "0"],
                "cancel": ["shutdown", "/a"], "sign_out": ["shutdown", "/l"]}
    subprocess.run(commands[action], creationflags=winutil.NO_WINDOW)
    return {"shutdown": f"Shutting down at {datetime.fromtimestamp(time.time() + 60):%H:%M}. Say 'cancel shutdown' to stop it.",
            "restart": "Restarting in one minute. Say 'cancel restart' to stop it.",
            "cancel": "Cancelled the pending shutdown.", "sign_out": "Signing out.",
            "shutdown_now": "Shutting down now.", "restart_now": "Restarting now."}[action]
