"""Watches how hard the PC is working, and says so when it's being pushed too far.

One background sampler (every 3 s) feeds both the HUD's system panels and the spoken alerts. "Strained"
means *sustained*: a 5-second spike while something loads isn't worth a word. Each alert names what's
responsible ("mostly Chrome"), and games are taken into account - a maxed-out GPU is the point of a game,
so in full-screen only heat and running out of memory are mentioned.
"""
from __future__ import annotations

import ctypes
import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field

import psutil

log = logging.getLogger(__name__)

SAMPLE_EVERY = 3.0
CURRENT: "StrainMonitor | None" = None  # the running monitor (system_status reads its verdict)

FRIENDLY = {"chrome": "Chrome", "msedge": "Edge", "firefox": "Firefox", "code": "VS Code", "discord": "Discord",
            "steam": "Steam", "steamwebhelper": "Steam", "obs64": "OBS", "javaw": "Java (Minecraft?)",
            "spotify": "Spotify", "explorer": "File Explorer", "msmpeng": "Windows Defender",
            "searchhost": "Windows Search", "dwm": "Windows' display manager", "python": "Python",
            "pythonw": "Python", "ollama": "Ollama", "ollama app": "Ollama", "valorant-win64-shipping": "Valorant",
            "cyberpunk2077": "Cyberpunk 2077", "riotclientservices": "Riot Client", "teams": "Teams",
            "ms-teams": "Teams", "onedrive": "OneDrive", "vmmem": "a virtual machine (WSL/Docker)",
            "vmmemwsl": "WSL", "node": "Node.js"}


def friendly(exe: str) -> str:
    """'chrome.exe' -> 'Chrome': how a person would say it."""
    name = exe.removesuffix(".exe").removesuffix(".EXE").strip()
    return FRIENDLY.get(name.lower(), name[:1].upper() + name[1:])


@dataclass
class Snapshot:
    cpu: float = 0.0
    ram: float = 0.0
    ram_used_gb: float = 0.0
    ram_total_gb: float = 0.0
    gpu: float = -1.0           # -1 = no NVIDIA GPU / unknown
    gpu_temp: float = -1.0
    vram: float = -1.0
    vram_used_gb: float = 0.0
    vram_total_gb: float = 0.0
    up_kbps: float = 0.0
    down_kbps: float = 0.0
    top_cpu: list = field(default_factory=list)   # [(name, % of whole CPU)]
    top_ram: list = field(default_factory=list)   # [(name, GB)]
    level: str = "ok"           # ok | busy | strained
    reasons: list = field(default_factory=list)


class _NVML:
    """NVIDIA's management library, straight through ctypes (no extra packages)."""

    def __init__(self):
        self.lib = self.dev = None
        for name in ("nvml", r"C:\Windows\System32\nvml.dll"):
            try:
                lib = ctypes.WinDLL(name)
                if lib.nvmlInit_v2() != 0:
                    continue
                dev = ctypes.c_void_p()
                if lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev)) == 0:
                    self.lib, self.dev = lib, dev
                    return
            except Exception:
                continue

    def read(self) -> tuple[float, float, float, float, float]:
        """(utilisation %, temp °C, vram %, vram used GB, vram total GB) or -1s."""
        if self.lib is None:
            return -1.0, -1.0, -1.0, 0.0, 0.0

        class Util(ctypes.Structure):
            _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]

        class Mem(ctypes.Structure):
            _fields_ = [("total", ctypes.c_ulonglong), ("free", ctypes.c_ulonglong), ("used", ctypes.c_ulonglong)]

        u, t, m = Util(), ctypes.c_uint(), Mem()
        gpu = float(u.gpu) if self.lib.nvmlDeviceGetUtilizationRates(self.dev, ctypes.byref(u)) == 0 else -1.0
        temp = float(t.value) if self.lib.nvmlDeviceGetTemperature(self.dev, 0, ctypes.byref(t)) == 0 else -1.0
        if self.lib.nvmlDeviceGetMemoryInfo(self.dev, ctypes.byref(m)) == 0 and m.total:
            return gpu, temp, 100.0 * m.used / m.total, m.used / 2**30, m.total / 2**30
        return gpu, temp, -1.0, 0.0, 0.0


class StrainMonitor:
    def __init__(self, settings, on_alert=None):
        self.settings = settings
        self.on_alert = on_alert            # on_alert(key, text, urgent)
        self.now = Snapshot()
        self.history: dict[str, deque] = {k: deque(maxlen=120) for k in ("cpu", "ram", "gpu", "up", "down")}
        self._since: dict[str, float] = {}  # condition -> when it started
        self._alerted: dict[str, float] = {}
        self._nv = _NVML()
        self._net = psutil.net_io_counters()
        self._net_t = time.time()
        self._procs_t = 0.0
        self._stop = threading.Event()
        psutil.cpu_percent(None)

    def start(self) -> "StrainMonitor":
        global CURRENT
        CURRENT = self
        threading.Thread(target=self._loop, name="strain-monitor", daemon=True).start()
        return self

    def stop(self) -> None:
        self._stop.set()

    # ---- sampling --------------------------------------------------------------------------
    def _loop(self) -> None:
        while not self._stop.wait(SAMPLE_EVERY):
            try:
                self._sample()
                self._judge()
            except Exception:
                log.exception("strain sample failed")

    def _sample(self) -> None:
        s = Snapshot()
        s.cpu = psutil.cpu_percent(None)
        vm = psutil.virtual_memory()
        s.ram, s.ram_used_gb, s.ram_total_gb = vm.percent, vm.used / 2**30, vm.total / 2**30
        s.gpu, s.gpu_temp, s.vram, s.vram_used_gb, s.vram_total_gb = self._nv.read()
        nc, t = psutil.net_io_counters(), time.time()
        dt = max(1e-3, t - self._net_t)
        s.up_kbps = (nc.bytes_sent - self._net.bytes_sent) / dt / 1024
        s.down_kbps = (nc.bytes_recv - self._net.bytes_recv) / dt / 1024
        self._net, self._net_t = nc, t
        # Who's responsible - refreshed every ~10 s (walking every process isn't free)
        if t - self._procs_t > 10 or not self.now.top_cpu:
            self._procs_t = t
            s.top_cpu, s.top_ram = self._top_processes()
        else:
            s.top_cpu, s.top_ram = self.now.top_cpu, self.now.top_ram
        for k, v in (("cpu", s.cpu), ("ram", s.ram), ("gpu", s.gpu), ("up", s.up_kbps), ("down", s.down_kbps)):
            self.history[k].append(v)
        s.level, s.reasons = self.now.level, self.now.reasons
        self.now = s

    def _top_processes(self) -> tuple[list, list]:
        cores = psutil.cpu_count() or 1
        cpu, ram = {}, {}
        for p in psutil.process_iter(["name", "memory_info"]):
            try:
                name = friendly(p.info["name"] or "")
                if not name or name in ("System Idle Process", "Idle", "System", "Registry", "Memory Compression"):
                    continue
                c = p.cpu_percent(None) / cores  # since the last walk (first walk reads 0)
                cpu[name] = cpu.get(name, 0.0) + c
                mi = p.info["memory_info"]
                if mi:
                    ram[name] = ram.get(name, 0.0) + mi.rss / 2**30
            except psutil.Error:
                continue
        return (sorted(cpu.items(), key=lambda kv: kv[1], reverse=True)[:5],
                sorted(ram.items(), key=lambda kv: kv[1], reverse=True)[:5])

    # ---- judging -----------------------------------------------------------------------------
    def _held(self, key: str, condition: bool, seconds: float) -> bool:
        """True once `condition` has held continuously for `seconds`."""
        now = time.time()
        if not condition:
            self._since.pop(key, None)
            return False
        start = self._since.setdefault(key, now)
        return now - start >= seconds

    def _judge(self) -> None:
        g, s = self.settings.get, self.now
        cpu_hi, ram_hi = float(g("proactive.cpu_high", 90)), float(g("proactive.memory_high", 90))
        hot = float(g("proactive.gpu_hot", 85))
        reasons, level = [], "ok"

        def name_of(top, unit=""):
            if not top:
                return ""
            n, v = top[0]
            return f"{n} ({v:.0f}{unit})" if unit else n

        cpu_strained = self._held("cpu", s.cpu >= cpu_hi, 60)
        ram_strained = self._held("ram", s.ram >= ram_hi, 30)
        ram_critical = self._held("ram95", s.ram >= 96, 10)
        gpu_hot = self._held("heat", s.gpu_temp >= hot, 30)
        vram_full = self._held("vram", s.vram >= 97, 30)
        if cpu_strained:
            reasons.append(f"CPU at {s.cpu:.0f}% for over a minute" +
                           (f" - mostly {name_of(s.top_cpu)}" if s.top_cpu and s.top_cpu[0][1] >= 10 else ""))
        if ram_strained:
            reasons.append(f"memory {s.ram:.0f}% full ({s.ram_used_gb:.1f} of {s.ram_total_gb:.0f} GB)" +
                           (f" - {name_of(s.top_ram)} is using {s.top_ram[0][1]:.1f} GB" if s.top_ram else ""))
        if gpu_hot:
            reasons.append(f"graphics card running hot at {s.gpu_temp:.0f}°C")
        if vram_full:
            reasons.append(f"graphics memory full ({s.vram_used_gb:.1f} of {s.vram_total_gb:.0f} GB)")
        if reasons:
            level = "strained"
        elif s.cpu >= cpu_hi - 15 or s.ram >= ram_hi - 10 or (s.gpu_temp >= hot - 8 > 0):
            level = "busy"
        self.now.level, self.now.reasons = level, reasons

        if not self.on_alert or not g("proactive.strain_alerts", True):
            return
        from . import winutil
        gaming = winutil.is_fullscreen_foreground()
        cooldown = float(g("proactive.strain_cooldown_minutes", 20)) * 60
        t = str(g("user.title", "sir"))
        alerts = []
        if cpu_strained and not gaming:
            who = f" {s.top_cpu[0][0]} is the main culprit." if s.top_cpu and s.top_cpu[0][1] >= 15 else ""
            alerts.append(("cpu", f"{t.capitalize()}, your processor has been flat out at {s.cpu:.0f} percent for over a "
                                  f"minute.{who}", False))
        if ram_strained and (not gaming or ram_critical):
            who = (f" {s.top_ram[0][0]} alone is using {s.top_ram[0][1]:.1f} gigabytes." if s.top_ram else "")
            alerts.append(("ram", f"Memory's nearly full, {t} - {s.ram:.0f} percent.{who} Closing something would "
                                  "help.", ram_critical))
        if gpu_hot:
            alerts.append(("heat", f"Your graphics card is running hot, {t} - {s.gpu_temp:.0f} degrees. It might be "
                                   "worth easing off or checking the fans.", s.gpu_temp >= hot + 5))
        if vram_full and not gaming:
            alerts.append(("vram", f"The graphics card's memory is full, {t}, which can cause stutters.", False))
        now = time.time()
        for key, text, urgent in alerts:
            if now - self._alerted.get(key, 0) >= cooldown:
                self._alerted[key] = now
                try:
                    self.on_alert(key, text, urgent)
                except Exception:
                    log.exception("strain alert failed")

    def summary(self) -> str:
        s = self.now
        bits = [f"CPU {s.cpu:.0f}%", f"memory {s.ram:.0f}%"]
        if s.gpu >= 0:
            bits.append(f"GPU {s.gpu:.0f}%" + (f" at {s.gpu_temp:.0f}°C" if s.gpu_temp >= 0 else ""))
        text = ", ".join(bits)
        if s.reasons:
            text += ". Under strain: " + "; ".join(s.reasons)
        return text
