"""The desktop HUD: a native PyQt6 window.

Layout, palette, the reactor core, metric bars and the typewriter activity log are adapted from
MARK LVI's ui.py by FatihMakes (https://www.youtube.com/@FatihMakes), licensed CC BY-NC 4.0 -
used here for personal, non-commercial purposes. Changes: wired to this project's event bus,
local-model status, Jarvis's own audio levels, and a trimmed set of panels.
"""
from __future__ import annotations

import ctypes
import math
import threading
import time
from typing import TYPE_CHECKING

import psutil
from PyQt6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QIcon, QKeySequence, QPainter, QPen, QPixmap,
                         QRadialGradient, QShortcut)
from PyQt6.QtWidgets import (QApplication, QFrame, QHBoxLayout, QLabel, QLineEdit, QMainWindow,
                             QPushButton, QScrollArea, QSizePolicy, QSplitter, QTextEdit, QVBoxLayout,
                             QWidget)

from ..events import bus

if TYPE_CHECKING:
    from ..assistant import Jarvis

_LEFT_W, _RIGHT_W = 150, 350


class C:
    BG = "#00060a"
    PANEL = "#010d14"
    PANEL2 = "#010f18"
    BORDER = "#0d3347"
    BORDER_B = "#1a5c7a"
    BORDER_A = "#0f4060"
    PRI = "#00d4ff"
    PRI_DIM = "#007a99"
    PRI_GHO = "#001f2e"
    ACC = "#ff6b00"
    ACC2 = "#ffcc00"
    GREEN = "#00ff88"
    GREEN_D = "#00aa55"
    RED = "#ff3355"
    MUTED_C = "#ff3366"
    TEXT = "#8ffcff"
    TEXT_DIM = "#3a8a9a"
    TEXT_MED = "#5ab8cc"
    WHITE = "#d8f8ff"
    DARK = "#000d14"
    BAR_BG = "#011520"


def qcol(h: str, a: int = 255) -> QColor:
    c = QColor(h)
    c.setAlpha(a)
    return c


# ---- system metrics (background thread; NVML via ctypes, no subprocess) -----------------
class _Metrics:
    def __init__(self):
        self.data = {"cpu": 0.0, "mem": 0.0, "net": 0.0, "gpu": -1.0, "gtemp": -1.0}
        self._last_net = psutil.net_io_counters()
        self._last_t = time.time()
        self._nv = None
        self._nv_dev = None
        try:
            for name in ("nvml", r"C:\Windows\System32\nvml.dll"):
                try:
                    lib = ctypes.WinDLL(name)
                    lib.nvmlInit_v2()
                    dev = ctypes.c_void_p()
                    lib.nvmlDeviceGetHandleByIndex_v2(0, ctypes.byref(dev))
                    self._nv, self._nv_dev = lib, dev
                    break
                except Exception:
                    continue
        except Exception:
            pass
        psutil.cpu_percent(None)
        threading.Thread(target=self._loop, daemon=True, name="hud-metrics").start()

    def _loop(self):
        while True:
            try:
                self._update()
            except Exception:
                pass
            time.sleep(2.0)

    def _update(self):
        nc, now = psutil.net_io_counters(), time.time()
        dt = max(1e-3, now - self._last_t)
        net = ((nc.bytes_sent - self._last_net.bytes_sent) + (nc.bytes_recv - self._last_net.bytes_recv)) / dt / 2**20
        self._last_net, self._last_t = nc, now
        gpu = gtemp = -1.0
        if self._nv is not None:
            class _Util(ctypes.Structure):
                _fields_ = [("gpu", ctypes.c_uint), ("memory", ctypes.c_uint)]
            u, t = _Util(), ctypes.c_uint()
            if self._nv.nvmlDeviceGetUtilizationRates(self._nv_dev, ctypes.byref(u)) == 0:
                gpu = float(u.gpu)
            if self._nv.nvmlDeviceGetTemperature(self._nv_dev, 0, ctypes.byref(t)) == 0:
                gtemp = float(t.value)
        self.data = {"cpu": psutil.cpu_percent(None), "mem": psutil.virtual_memory().percent,
                     "net": net, "gpu": gpu, "gtemp": gtemp}


# ---- widgets -------------------------------------------------------------------------------
class MetricBar(QWidget):
    def __init__(self, label: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self._label, self._color, self._value, self._text = label, color, 0.0, "--"
        self.setFixedHeight(38)

    def set_value(self, pct: float, text: str):
        v = max(0.0, min(100.0, pct))
        if (v, text) != (self._value, self._text):
            self._value, self._text = v, text
            self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setBrush(QBrush(qcol(C.PANEL2)))
        p.setPen(QPen(qcol(C.BORDER_A), 1))
        p.drawRoundedRect(QRectF(1, 1, W - 2, H - 2), 4, 4)
        bar_y, bar_w = H - 9, W - 12
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(qcol(C.BAR_BG)))
        p.drawRoundedRect(QRectF(6, bar_y, bar_w, 4), 2, 2)
        col = qcol(C.RED) if self._value > 85 else qcol(C.ACC) if self._value > 65 else qcol(self._color)
        if self._value > 0:
            p.setBrush(QBrush(col))
            p.drawRoundedRect(QRectF(6, bar_y, bar_w * self._value / 100, 4), 2, 2)
        p.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(8, 5, 60, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self._label)
        p.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
        p.setPen(QPen(col if self._text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 4, W - 6, 16), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter, self._text)
        p.end()


class LogWidget(QTextEdit):
    """Activity log that types itself out, coloured by who is speaking."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setReadOnly(True)
        self.document().setMaximumBlockCount(600)
        self.setFont(QFont("Courier New", 9))
        self.setStyleSheet(f"""
            QTextEdit {{ background: {C.PANEL}; color: {C.TEXT}; border: 1px solid {C.BORDER};
                         border-radius: 4px; padding: 6px; selection-background-color: {C.PRI_GHO}; }}
            QScrollBar:vertical {{ background: {C.BG}; width: 8px; border: none; }}
            QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 20px; }}
        """)
        self._queue: list[tuple[str, str]] = []
        self._typing, self._text, self._pos, self._tag = False, "", 0, "sys"
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)

    def add(self, text: str, tag: str = "sys"):
        self._queue.append((text, tag))
        if not self._typing:
            self._next()

    def _next(self):
        if not self._queue:
            self._typing = False
            return
        self._typing = True
        self._text, self._tag = self._queue.pop(0)
        self._pos = 0
        self._tmr.start(6)

    def _step(self):
        col = {"you": qcol(C.WHITE), "ai": qcol(C.PRI), "err": qcol(C.RED), "tool": qcol(C.TEXT_DIM),
               "sys": qcol(C.TEXT_MED)}.get(self._tag, qcol(C.TEXT))
        cur = self.textCursor()
        cur.movePosition(cur.MoveOperation.End)
        fmt = cur.charFormat()
        fmt.setForeground(QBrush(col))
        if self._pos < len(self._text):
            # a few characters per tick so a long backlog never falls minutes behind
            n = 2 if len(self._queue) < 3 else 12
            cur.insertText(self._text[self._pos:self._pos + n], fmt)
            self._pos += n
        else:
            self._tmr.stop()
            cur.insertText("\n", fmt)
            QTimer.singleShot(20, self._next)
        self.setTextCursor(cur)
        self.ensureCursorVisible()


class HudCanvas(QWidget):
    """The reactor core: rings turn at a rate set by the state; the spectrum ring is the real audio."""

    def __init__(self, name: str, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)
        self.setMinimumSize(300, 300)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.muted, self.speaking, self.state = False, False, "idle"
        self._name = name
        self._tick, self._blink, self._blink_tick, self._paint_tick = 0, True, 0, 0
        self._live_amp = self._amp_disp = 0.0
        self._core_phase, self._step_t = 0.0, time.time()
        self._grid, self._grid_key = None, None
        from ..voice import audio
        self._audio = audio
        self._tmr = QTimer(self)
        self._tmr.timeout.connect(self._step)
        self._tmr.start(16)

    def _step(self):
        self._tick += 1
        now = time.time()
        level = 0.0
        if self.speaking:
            level = self._audio.output_level()
        elif not self.muted:  # the mic is always on, so the waveform shows your voice whenever you talk
            level = self._audio.mic_level()
        self._live_amp = max(self._live_amp * 0.86, level)
        self._amp_disp += (self._live_amp - self._amp_disp) * 0.45
        dt, self._step_t = now - self._step_t, now
        self._core_phase += min(0.10, max(0.0, dt))
        self._blink_tick += 1
        blinked = self._blink_tick >= 38
        if blinked:
            self._blink, self._blink_tick = not self._blink, 0
        self._paint_tick = (self._paint_tick + 1) % 6
        active = self.speaking or self._amp_disp > 0.02 or self.state == "thinking"
        if blinked or (self._paint_tick % 2 == 0 if active else self._paint_tick % 3 == 0):
            win = self.window()
            if self.isVisible() and not win.isMinimized():
                self.update()

    def _colours(self):
        if self.muted:
            return qcol(C.MUTED_C), qcol(C.MUTED_C)
        if self.speaking:
            return qcol(C.PRI), qcol(C.ACC)
        if self.state == "thinking":
            return qcol(C.PRI), qcol(C.ACC2)
        if self.state == "listening":
            return qcol(C.PRI), qcol(C.GREEN)
        return qcol(C.PRI), qcol(C.PRI_DIM)

    def _paint_core(self, p: QPainter, cx: float, cy: float, r: float, W: float, H: float):
        main, acc = self._colours()
        bg = qcol(C.BG)
        amp, t = self._amp_disp, self._core_phase
        live = (self.speaking or amp > 0.04) and not self.muted

        def blend(col: QColor, a: float) -> QColor:
            k = max(0.0, min(1.0, a))
            return QColor(int(bg.red() + (col.red() - bg.red()) * k), int(bg.green() + (col.green() - bg.green()) * k),
                          int(bg.blue() + (col.blue() - bg.blue()) * k))

        # atmosphere
        lift = 1.0 + 0.55 * amp + (0.18 if self.speaking else 0.0)
        p.setPen(Qt.PenStyle.NoPen)
        for gr, a0 in ((r * 0.70, 0.30), (r * 0.34, 0.34)):
            g = QRadialGradient(cx, cy, gr)
            g.setColorAt(0.00, blend(main, min(0.95, a0 * lift)))
            g.setColorAt(0.45, blend(main, min(0.95, a0 * lift * 0.52)))
            g.setColorAt(0.78, blend(main, min(0.95, a0 * lift * 0.18)))
            g.setColorAt(1.00, blend(main, 0.0))
            p.setBrush(QBrush(g))
            p.drawEllipse(QRectF(cx - gr, cy - gr, gr * 2, gr * 2))
        p.setBrush(Qt.BrushStyle.NoBrush)
        # frame marks + crosshair
        m, arm = min(W, H) * 0.035, min(W, H) * 0.055
        p.setPen(QPen(blend(main, 0.45), 1.4))
        for sx, sy in ((1, 1), (-1, 1), (1, -1), (-1, -1)):
            x, y = cx + sx * (W / 2 - m), cy + sy * (H / 2 - m)
            p.drawLine(QLineF(x, y, x - sx * arm, y))
            p.drawLine(QLineF(x, y, x, y - sy * arm))
        p.setPen(QPen(blend(main, 0.16), 1))
        gap = r * 0.62
        p.drawLine(QLineF(cx - W / 2, cy, cx - gap, cy))
        p.drawLine(QLineF(cx + gap, cy, cx + W / 2, cy))
        p.drawLine(QLineF(cx, cy - H / 2, cx, cy - gap))
        p.drawLine(QLineF(cx, cy + gap, cx, cy + H / 2))
        # outer circles + graduations
        for rr, a in ((1.00, 0.34), (0.93, 0.16)):
            p.setPen(QPen(blend(main, a), 1))
            p.drawEllipse(QRectF(cx - r * rr, cy - r * rr, r * rr * 2, r * rr * 2))
        major, minor = [], []
        for i in range(72):
            a = math.radians(i * 5.0)
            ca, sa = math.cos(a), math.sin(a)
            (major if i % 3 == 0 else minor).append(
                QLineF(cx + ca * r * (0.885 if i % 3 == 0 else 0.945), cy + sa * r * (0.885 if i % 3 == 0 else 0.945),
                       cx + ca * r * 0.985, cy + sa * r * 0.985))
        p.setPen(QPen(blend(main, 0.42), 1.3))
        p.drawLines(major)
        p.setPen(QPen(blend(main, 0.18), 1))
        p.drawLines(minor)
        # sweeping arcs - speed is the state
        rate = 1.0 + (1.9 if self.state == "thinking" else 0.0) + (1.2 if self.speaking else 0.0)
        for k, (rr, span, count, dirn, col, a, wid) in enumerate((
                (0.955, 118, 2, +1, acc, 0.75, 2.0), (0.845, 82, 3, -1, main, 0.38, 1.3),
                (0.760, 150, 1, +1, acc, 0.45, 1.6), (0.660, 64, 4, -1, main, 0.26, 1.1),
                (0.545, 128, 2, +1, main, 0.30, 1.2))):
            rad = r * rr
            p.setPen(QPen(blend(col, a), wid))
            box = QRectF(cx - rad, cy - rad, rad * 2, rad * 2)
            base = (t * rate * (9 + k * 6) * dirn) % 360.0
            for s in range(count):
                p.drawArc(box, int((base + s * (360.0 / count)) * 16), int(span * 16))
        # the voice: a ring of spikes that grow with the audio level
        ring, spikes = r * 0.415, []
        for i in range(60):
            a = math.radians(i * 6.0)
            ca, sa = math.cos(a), math.sin(a)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            h = r * (0.018 + 0.012 * math.sin(t * 1.2 + i * 0.7) + (amp * 0.20 * wob if live else 0.0))
            spikes.append(QLineF(cx + ca * ring, cy + sa * ring, cx + ca * (ring + h), cy + sa * (ring + h)))
        p.setPen(QPen(blend(acc if live else main, 0.25 + 0.5 * amp), 1.6))
        p.drawLines(spikes)
        inner = r * 0.355
        p.setPen(QPen(blend(acc, 0.30 + 0.45 * amp), 1.5))
        p.drawEllipse(QRectF(cx - inner, cy - inner, inner * 2, inner * 2))
        # the name, sized from the string so any name fits inside the ring
        if self._name:
            space = max(1.0, r * 0.018)
            fsz = max(8, int(min(r * 0.105, (inner * 1.75) / max(1, len(self._name)) * 1.6 - space)))
            f = QFont("Courier New", fsz, QFont.Weight.Bold)
            f.setLetterSpacing(QFont.SpacingType.AbsoluteSpacing, space)
            p.setFont(f)
            p.setPen(QPen(blend(qcol(C.WHITE), 0.6 + 0.4 * min(1.0, amp * 2)), 1))
            p.drawText(QRectF(cx - r, cy - fsz, r * 2, fsz * 2), Qt.AlignmentFlag.AlignCenter, self._name)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.fillRect(self.rect(), qcol(C.BG))
        W, H = self.width(), self.height()
        cx, fw = W / 2, min(W, H)
        if self._grid is None or self._grid_key != (W, H):
            self._grid = QPixmap(max(1, W), max(1, H))
            self._grid.fill(Qt.GlobalColor.transparent)
            gp = QPainter(self._grid)
            gp.setPen(QPen(qcol(C.PRI_GHO), 1))
            for x in range(0, W, 48):
                for y in range(0, H, 48):
                    gp.drawPoint(x, y)
            gp.end()
            self._grid_key = (W, H)
        p.drawPixmap(0, 0, self._grid)

        sy = min(H / 2 + fw * 0.40, H - 58.0)  # keep status + waveform inside when the panel below opens
        band_h = max(60.0, sy - 24.0)
        self._paint_core(p, cx, 12 + band_h / 2.0, min(W * 0.46, band_h / 2.0), W, band_h)

        if self.muted:
            txt, col = "⊘  MUTED", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING", qcol(C.ACC)
        elif self.state == "thinking":
            txt, col = f"{'◈' if self._blink else '◇'}  THINKING", qcol(C.ACC2)
        elif self.state == "listening":
            txt, col = f"{'●' if self._blink else '○'}  LISTENING", qcol(C.GREEN)
        else:
            txt, col = f"{'●' if self._blink else '○'}  STANDING BY", qcol(C.PRI)
        p.setPen(QPen(col, 1))
        p.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        p.drawText(QRectF(0, sy, W, 26), Qt.AlignmentFlag.AlignCenter, txt)

        wy, N, bw = sy + 30, 36, 8
        wx0, amp, mid = (W - N * bw) / 2, self._amp_disp, (36 - 1) / 2.0
        for i in range(N):
            if self.muted:
                hgt, cl = 2, qcol(C.MUTED_C)
            else:
                env = (1.0 - abs(i - mid) / mid) ** 0.7
                shimmer = 0.55 + 0.45 * math.sin(self._tick * 0.18 + i * 0.7)
                idle = 3.0 + 2.0 * math.sin(self._tick * 0.09 + i * 0.6)
                hgt = int(max(2, min(24, idle + amp * 22.0 * env * shimmer)))
                cl = (qcol(C.PRI) if hgt > 12 else qcol(C.PRI_DIM)) if amp > 0.05 else qcol(C.BORDER_B)
            p.fillRect(QRectF(wx0 + i * bw, wy + 20 - hgt, bw - 1, hgt), cl)
        p.end()


class ConfirmBanner(QWidget):
    """The gate in front of a change: the answer comes from a person, not the model."""

    def __init__(self, text: str, on_answer, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("ConfirmBanner")
        self.setStyleSheet(f"QWidget#ConfirmBanner {{ background: rgba(14, 3, 0, 250); border: 1px solid {C.ACC}; "
                           f"border-radius: 6px; }}")
        self.setFixedWidth(440)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(20, 16, 20, 16)
        lay.setSpacing(8)
        hdr = QLabel("⚠  CONFIRM")
        hdr.setFont(QFont("Courier New", 11, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.ACC}; background: transparent;")
        lay.addWidget(hdr)
        body = QLabel(text)
        body.setWordWrap(True)
        body.setFont(QFont("Courier New", 9))
        body.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
        lay.addWidget(body)
        row = QHBoxLayout()
        yes, no = QPushButton("▸  CONFIRM"), QPushButton("CANCEL")
        for b, col in ((yes, C.ACC), (no, C.TEXT_MED)):
            b.setFixedHeight(32)
            b.setFont(QFont("Courier New", 9, QFont.Weight.Bold))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setStyleSheet(f"QPushButton {{ background: transparent; color: {col}; border: 1px solid {col}; "
                            f"border-radius: 3px; }} QPushButton:hover {{ background: rgba(255,107,0,40); }}")
            row.addWidget(b)
        yes.clicked.connect(lambda: on_answer(True))
        no.clicked.connect(lambda: on_answer(False))
        no.setDefault(True)  # Enter without reading picks the safe answer
        lay.addLayout(row)


class MemoryOverlay(QWidget):
    """Everything Jarvis remembers about you, newest first, with a ✕ to forget each one."""

    def __init__(self, memory, parent=None):
        super().__init__(parent)
        self.memory = memory
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setObjectName("MemoryOverlay")
        self.setStyleSheet(f"QWidget#MemoryOverlay {{ background: rgba(0, 6, 10, 246); border: 1px solid "
                           f"{C.BORDER_B}; border-radius: 6px; }}")
        self.setFixedWidth(540)
        self._lay = QVBoxLayout(self)
        self._lay.setContentsMargins(20, 16, 20, 16)
        self._rebuild()

    def _rebuild(self):
        while self._lay.count():
            item = self._lay.takeAt(0)
            if item.widget():
                item.widget().hide()
                item.widget().deleteLater()
        hdr = QLabel("🧠  WHAT JARVIS REMEMBERS")
        hdr.setFont(QFont("Courier New", 12, QFont.Weight.Bold))
        hdr.setStyleSheet(f"color: {C.PRI}; background: transparent;")
        self._lay.addWidget(hdr)
        rows = self.memory.entries()
        cap = QLabel(f"{len(rows)} stored facts, newest first - kept in memory/long_term.json on this PC only.")
        cap.setWordWrap(True)
        cap.setFont(QFont("Courier New", 7))
        cap.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
        self._lay.addWidget(cap)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFixedHeight(min(380, 34 * max(1, len(rows)) + 12))
        scroll.setStyleSheet(f"QScrollArea {{ border: 1px solid {C.BORDER}; background: transparent; }}")
        inner = QWidget()
        inner.setStyleSheet("background: transparent;")
        il = QVBoxLayout(inner)
        il.setContentsMargins(6, 6, 6, 6)
        if not rows:
            empty = QLabel("Nothing stored yet - tell Jarvis about yourself and he'll remember.")
            empty.setStyleSheet(f"color: {C.TEXT_MED}; background: transparent;")
            il.addWidget(empty)
        for r in rows:
            line = QHBoxLayout()
            txt = QLabel(f"<b>{r['key'].replace('_', ' ')}</b> <span style='color:{C.TEXT_MED}'>— {r['value']}</span>")
            txt.setWordWrap(True)
            txt.setFont(QFont("Courier New", 8))
            txt.setStyleSheet(f"color: {C.TEXT}; background: transparent;")
            line.addWidget(txt, 1)
            meta = QLabel(f"{r['category'][:5]} · {r['updated'][:10]}")
            meta.setFont(QFont("Courier New", 7))
            meta.setStyleSheet(f"color: {C.TEXT_DIM}; background: transparent;")
            line.addWidget(meta)
            rm = QPushButton("✕")
            rm.setFixedSize(20, 20)
            rm.setCursor(Qt.CursorShape.PointingHandCursor)
            rm.setToolTip("Forget this")
            rm.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_DIM}; border: 1px solid "
                             f"{C.BORDER}; border-radius: 3px; }} QPushButton:hover {{ color: {C.RED}; }}")
            rm.clicked.connect(lambda _=False, c=r["category"], k=r["key"]: self._forget(c, k))
            line.addWidget(rm)
            holder = QWidget()
            holder.setLayout(line)
            il.addWidget(holder)
        il.addStretch()
        scroll.setWidget(inner)
        self._lay.addWidget(scroll)
        close = QPushButton("CLOSE")
        close.setFixedHeight(30)
        close.setCursor(Qt.CursorShape.PointingHandCursor)
        close.setStyleSheet(f"QPushButton {{ background: transparent; color: {C.TEXT_MED}; border: 1px solid "
                            f"{C.BORDER}; border-radius: 3px; }} QPushButton:hover {{ color: {C.TEXT}; }}")
        close.clicked.connect(self.hide)
        self._lay.addWidget(close)
        self.adjustSize()

    def _forget(self, category: str, key: str):
        self.memory.forget(key, category)
        QTimer.singleShot(0, self._rebuild)  # never destroy the sender inside its own click


def _reactor_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.GlobalColor.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.RenderHint.Antialiasing)
    p.setBrush(QBrush(qcol(C.BG)))
    p.setPen(QPen(qcol(C.PRI), 3))
    p.drawEllipse(QRectF(3, 3, 58, 58))
    p.setPen(QPen(qcol(C.PRI_DIM), 2))
    p.drawEllipse(QRectF(14, 14, 36, 36))
    g = QRadialGradient(32, 32, 14)
    g.setColorAt(0, qcol(C.WHITE))
    g.setColorAt(1, qcol(C.PRI, 0))
    p.setBrush(QBrush(g))
    p.setPen(Qt.PenStyle.NoPen)
    p.drawEllipse(QPointF(32, 32), 14, 14)
    p.end()
    return QIcon(pm)


class MainWindow(QMainWindow):
    """The HUD. Layout after a classic Rainmeter JARVIS desktop; every panel does something."""
    event_sig = pyqtSignal(object)

    def __init__(self, jarvis: "Jarvis"):
        super().__init__()
        from . import dashboard as D
        self.D = D
        self.jarvis = jarvis
        s = jarvis.settings
        self._name = str(s.get("assistant.name", "J.A.R.V.I.S.") or "J.A.R.V.I.S.")
        self.setWindowTitle(f"{self._name} — HUD")
        self.setWindowIcon(_reactor_icon())
        self.setMinimumSize(1180, 760)
        avail = QApplication.primaryScreen().availableGeometry()
        self.resize(min(1560, avail.width() - 40), min(940, avail.height() - 40))
        self._confirm: ConfirmBanner | None = None
        self._memory_ov: MemoryOverlay | None = None

        central = D.HudBackground()
        self.setCentralWidget(central)
        root = QVBoxLayout(central)
        root.setContentsMargins(12, 8, 12, 8)
        root.setSpacing(8)
        root.addWidget(self._header())
        body = QHBoxLayout()
        body.setSpacing(10)

        left = QVBoxLayout()
        left.setSpacing(10)
        left.setContentsMargins(0, 0, 0, 0)
        left.addWidget(D.LaunchPanel(jarvis))
        left.addWidget(D.CalendarPanel(jarvis))
        left.addWidget(D.WeatherPanel())
        left.addWidget(D.TasksPanel(jarvis))
        left.addStretch()
        lw = QWidget()
        lw.setLayout(left)
        lw.setFixedWidth(270)
        body.addWidget(lw)

        centre = QVBoxLayout()
        centre.setSpacing(8)
        centre.addLayout(self._command_bar())
        core_row = QHBoxLayout()
        core_row.setSpacing(4)
        core_row.addWidget(D.LinksPanel(jarvis))
        self.hud = D.SuitCore(jarvis)
        core_row.addWidget(self.hud, 1)
        core_row.addLayout(self._chips())
        centre.addLayout(core_row, 5)
        centre.addWidget(self._tabs(), 2)
        body.addLayout(centre, 1)

        right = QVBoxLayout()
        right.setSpacing(10)
        right.setContentsMargins(0, 0, 0, 0)
        right.addWidget(D.SystemPanel(jarvis))
        right.addWidget(D.DiskPanel())
        right.addWidget(D.NetworkPanel(jarvis))
        right.addWidget(D.FoldersPanel())
        right.addWidget(D.MediaPanel())
        right.addStretch()
        rw = QWidget()
        rw.setLayout(right)
        rw.setFixedWidth(310)
        body.addWidget(rw)
        root.addLayout(body, 1)
        root.addWidget(self._footer())

        self.event_sig.connect(self._on_event)
        bus.subscribe(self._from_bus)
        for e in list(bus.history):
            if e["type"] in ("notice", "user", "jarvis"):
                self._on_event(e)
        from . import settings_window
        settings_window.ATTACHED = True
        if settings_window.SettingsWindow.needs_key():  # first run: straight to "paste your key"
            QTimer.singleShot(700, lambda: self.open_settings("start"))

        for keys, fn in (("Escape", self.jarvis.interrupt), ("F4", self._toggle_mic), ("F11", self._toggle_full)):
            QShortcut(QKeySequence(keys), self).activated.connect(fn)
        self._clock = QTimer(self)
        self._clock.timeout.connect(self._tick)
        self._clock.start(1000)
        self._tick()

    # ---- bus -> GUI thread ---------------------------------------------------------------
    def _from_bus(self, event: dict) -> None:
        self.event_sig.emit(event)  # queued onto the GUI thread

    def _on_event(self, e: dict) -> None:
        kind = e["type"]
        if kind == "state":
            self.hud.state = e["value"]
            self.hud.speaking = e["value"] == "speaking"
        elif kind == "user":
            self._log.add(f"You{' (voice)' if e.get('source') == 'voice' else ''}: {e['text']}", "you")
        elif kind == "jarvis":
            self._log.add(f"{self._name.upper()}: {e['text']}", "ai")
        elif kind == "tool":
            self._log.add(f"  ↳ {e['name']}({e.get('args', '')})", "tool")
        elif kind == "notice":
            self._log.add(f"SYS: {e['text']}", "err" if e.get("level") == "warning" else "sys")
        elif kind == "confirm":
            self._show_confirm(e["text"])
        elif kind == "confirm_done":
            self._hide_confirm()
        elif kind == "content":
            self._show_content(e.get("title", ""), e.get("text", ""))
        elif kind == "mic":
            self._style_mic()
        elif kind == "open_settings":
            self.open_settings(e.get("page", ""))
        elif kind == "quit":
            self.close()

    # ---- layout pieces ---------------------------------------------------------------------
    def _label(self, text, size=8, color=C.TEXT_MED, bold=False):
        lb = QLabel(text)
        f = QFont("Courier New")
        f.setPointSizeF(size)
        f.setBold(bold)
        lb.setFont(f)
        lb.setStyleSheet(f"color: {color}; background: transparent; border: none;")
        return lb

    def _header(self) -> QWidget:
        D = self.D
        w = QWidget()
        w.setFixedHeight(70)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(4, 0, 4, 0)
        icon = QLabel()
        icon.setPixmap(_reactor_icon().pixmap(46, 46))
        lay.addWidget(icon)
        col = QVBoxLayout()
        col.setSpacing(0)
        col.addStretch()
        col.addWidget(self._label(self._name.upper(), 17, C.PRI, True))
        self._engine_lbl = self._label(self.jarvis.core_label, 7.5, C.PRI_DIM, True)
        col.addWidget(self._engine_lbl)
        col.addStretch()
        lay.addLayout(col)
        lay.addStretch()
        lay.addWidget(D.PowerBar())
        lay.addStretch()
        lay.addWidget(D.ClockWidget())
        return w

    def _command_bar(self) -> QHBoxLayout:
        D = self.D
        row = QHBoxLayout()
        self._input = QLineEdit()
        self._input.setPlaceholderText(f"What can I do for you, {self.jarvis.title}?")
        self._input.setFont(QFont("Segoe UI", 11))
        self._input.setFixedHeight(38)
        self._input.setStyleSheet(f"QLineEdit {{ background: rgba(0, 13, 20, 220); color: {C.WHITE}; "
                                  f"border: 1px solid {C.BORDER_B}; border-radius: 4px; padding: 4px 12px; }}"
                                  f"QLineEdit:focus {{ border-color: {C.PRI}; }}")
        self._input.returnPressed.connect(self._send)
        send = D.HexButton("SEND ▸", C.PRI, height=38)
        send.setFixedWidth(100)
        send.clicked.connect(self._send)
        row.addWidget(self._input, 1)
        row.addWidget(send)
        return row

    def _chips(self) -> QVBoxLayout:
        """Status you can click: core (keys), voice (voice settings), ears (mute), memory, settings."""
        D = self.D
        col = QVBoxLayout()
        col.setSpacing(8)
        col.addSpacing(30)
        self._core_chip = D.HexButton("AI CORE\n…", C.GREEN, slant=8, height=46)
        self._core_chip.clicked.connect(lambda: self.open_settings("start"))
        self._voice_chip = D.HexButton("VOICE\n…", C.PRI, slant=8, height=46)
        self._voice_chip.clicked.connect(lambda: self.open_settings("voice"))
        self._ears_chip = D.HexButton("EARS\n…", C.GREEN, slant=8, height=46)
        self._ears_chip.clicked.connect(self._toggle_mic)
        mem = D.HexButton("MEMORY", C.TEXT_MED, slant=8, height=34)
        mem.clicked.connect(self._open_memory)
        cfg = D.HexButton("SETTINGS", C.TEXT_MED, slant=8, height=34)
        cfg.clicked.connect(lambda: self.open_settings())
        for chip in (self._core_chip, self._voice_chip, self._ears_chip, mem, cfg):
            chip.setFixedWidth(130)
            chip.setFont(QFont("Courier New", 7, QFont.Weight.Bold))
            col.addWidget(chip)
        col.addStretch()
        return col

    def _tabs(self) -> QWidget:
        from PyQt6.QtWidgets import QTabWidget
        tabs = QTabWidget()
        tabs.setStyleSheet(f"""
            QTabWidget::pane {{ border: 1px solid {C.BORDER_B}; background: rgba(1, 13, 20, 225); top: -1px; }}
            QTabBar::tab {{ background: transparent; color: {C.TEXT_MED}; font-family: 'Courier New';
                            font-weight: bold; font-size: 8pt; padding: 6px 16px; border: 1px solid {C.BORDER};
                            border-bottom: none; margin-right: 3px; }}
            QTabBar::tab:selected {{ color: {C.PRI}; border-color: {C.BORDER_B}; background: rgba(0, 31, 46, 200); }}
        """)
        self._log = LogWidget()
        self._log.setStyleSheet(self._log.styleSheet().replace(f"background: {C.PANEL}", "background: transparent")
                                .replace(f"border: 1px solid {C.BORDER}", "border: none"))
        tabs.addTab(self._log, "▸ CONVERSATION")
        self._content_text = QTextEdit()
        self._content_text.setReadOnly(True)
        self._content_text.setFont(QFont("Segoe UI", 9))
        self._content_text.setStyleSheet(f"QTextEdit {{ background: transparent; color: {C.TEXT}; border: none; "
                                         f"padding: 6px 8px; }}")
        self._content_text.setPlaceholderText("News, search results and briefings appear here.")
        tabs.addTab(self._content_text, "▸ RESULTS")
        self._tabs_w = tabs
        return tabs

    def _footer(self) -> QWidget:
        D = self.D
        w = QWidget()
        w.setFixedHeight(34)
        lay = QHBoxLayout(w)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(8)
        self._mic_btn = D.HexButton("", C.GREEN, height=28)
        self._mic_btn.setFixedWidth(200)
        self._mic_btn.clicked.connect(self._toggle_mic)
        talk = D.HexButton("TALK  [CTRL+ALT+J]", C.GREEN, height=28)
        talk.setFixedWidth(180)
        talk.clicked.connect(self.jarvis.push_to_talk)
        stop = D.HexButton("INTERRUPT  [ESC]", C.MUTED_C, height=28)
        stop.setFixedWidth(160)
        stop.clicked.connect(self.jarvis.interrupt)
        full = D.HexButton("FULLSCREEN  [F11]", C.TEXT_MED, height=28)
        full.setFixedWidth(160)
        full.clicked.connect(self._toggle_full)
        for b in (self._mic_btn, talk, stop, full):
            lay.addWidget(b)
        lay.addStretch()
        lay.addWidget(self._label('Say "Jarvis, ..." anywhere in a sentence', 7, C.TEXT_DIM))
        lay.addSpacing(14)
        lay.addWidget(self._label("Core HUD elements after MARK LVI by FatihMakes (CC BY-NC 4.0)", 7, C.PRI_DIM))
        self._style_mic()
        return w

    # ---- behaviour ------------------------------------------------------------------------
    def _tick(self):
        j = self.jarvis
        s = j.strain.now
        bat = psutil.sensors_battery()
        if bat:
            trend = "charging" if bat.power_plugged else ("holding steady" if bat.percent > 40 else "dropping")
            self.hud.power_line = f"Power level at {bat.percent:.0f} percent and {trend}."
        else:
            self.hud.power_line = "Running on mains power. All systems online."
        self.hud.strain_level = s.level
        if s.reasons:
            self.hud.strain_line = "Under strain: " + "; ".join(s.reasons)
        else:
            gpu = f" · GPU {s.gpu:.0f}% at {s.gpu_temp:.0f}°C" if s.gpu >= 0 else ""
            word = "Working hard" if s.level == "busy" else "All systems nominal"
            self.hud.strain_line = f"{word} · CPU {s.cpu:.0f}% · RAM {s.ram:.0f}%{gpu}"
        awaiting = getattr(j, "awaiting_key", False)
        core_ok = j.ok and not awaiting
        self._core_chip.setText("AI CORE\n" + ("ONLINE" if core_ok else "NEEDS KEY" if awaiting else "FAULT"))
        self._core_chip._color = C.GREEN if core_ok else C.RED
        self._core_chip.setToolTip(j.brain_problem or j.core_label)
        self._voice_chip.setText("VOICE\n" + j.voice_label[:16])
        if j.live_active:
            ears = "MUTED" if j.mic_muted else "LIVE"
        else:
            ears = "OFF" if not j.listener else ("ERROR" if j.listener.error else ("MUTED" if j.mic_muted else "ON"))
        self._ears_chip.setText(f"EARS\n{ears}")
        self._ears_chip._color = C.MUTED_C if ears in ("MUTED", "OFF", "ERROR") else C.GREEN
        for chip in (self._core_chip, self._ears_chip):
            chip.update()
        self._engine_lbl.setText(j.core_label)

    def _send(self):
        text = self._input.text().strip()
        if text:
            self._input.clear()
            self.jarvis.submit(text, "hud")

    def _toggle_mic(self):
        self.jarvis.set_mic_muted(not self.jarvis.mic_muted)
        self.hud.muted = self.jarvis.mic_muted
        self._style_mic()

    def _style_mic(self):
        muted = self.jarvis.mic_muted
        self.hud.muted = muted
        self._mic_btn.setText("MIC MUTED  [F4]" if muted else "MIC LIVE  [F4]")
        self._mic_btn._color = C.MUTED_C if muted else C.GREEN
        self._mic_btn.update()

    def _toggle_full(self):
        self.showNormal() if self.isFullScreen() else self.showFullScreen()

    def _show_content(self, title: str, text: str):
        self._content_text.setPlainText(f"{title.upper()}  ·  {time.strftime('%H:%M')}\n\n{text}")
        self._tabs_w.setTabText(1, f"▸ RESULTS · {title.upper()[:28]}")
        self._tabs_w.setCurrentIndex(1)

    def _centre(self, w: QWidget):
        cw = self.centralWidget()
        w.adjustSize()
        w.move(max(0, (cw.width() - w.width()) // 2), max(0, (cw.height() - w.height()) // 2))
        w.show()
        w.raise_()

    def _show_confirm(self, text: str):
        self._hide_confirm()
        self._confirm = ConfirmBanner(text, lambda ok: self.jarvis.submit("yes" if ok else "no", "hud"),
                                      self.centralWidget())
        self._centre(self._confirm)

    def _hide_confirm(self):
        if self._confirm is not None:
            self._confirm.hide()
            self._confirm.deleteLater()
            self._confirm = None

    def _open_memory(self):
        if self._memory_ov is not None:
            self._memory_ov.hide()
            self._memory_ov.deleteLater()
        self._memory_ov = MemoryOverlay(self.jarvis.memory, self.centralWidget())
        self._centre(self._memory_ov)

    def open_settings(self, page: str = "") -> None:
        from .settings_window import SettingsWindow
        win = getattr(self, "_settings_win", None)
        if win is None or not win.isVisible():
            self._settings_win = win = SettingsWindow(self.jarvis, page, on_restart=self.restart)
        elif page:
            win.show_page(page)
        win.show()
        win.raise_()
        win.activateWindow()

    def restart(self) -> None:
        """Start a fresh Jarvis (it waits for this one to finish) and close this one."""
        from .. import shortcuts
        self._log.add("SYS: Restarting…", "sys")
        shortcuts.relaunch_self()
        QTimer.singleShot(300, self.close)

    def closeEvent(self, event):
        bus.unsubscribe(self._from_bus)
        win = getattr(self, "_settings_win", None)
        if win is not None:
            win.close()
        super().closeEvent(event)


def create_app():
    app = QApplication.instance() or QApplication([])
    app.setStyle("Fusion")
    return app
