"""The HUD's panels - each one live and clickable, not decoration.

  SuitCore       the centrepiece: an original suit-and-helmet wireframe whose eyes and arc reactor react to
                 his voice, with his state and the PC's condition underneath
  SystemPanel    CPU / memory rings, GPU load, heat and memory, and the strain verdict (with the culprit)
  DiskPanel      each drive's space - click to open it
  NetworkPanel   addresses and live up/down speeds - click an address to copy it
  LaunchPanel    angled tabs that open your apps
  CalendarPanel  this week, and what's next on your calendar (Google) - click to open it
  WeatherPanel   now, today and tomorrow
  LinksPanel     your sites, opened in your browser
  FoldersPanel   Downloads, Documents & co. in Explorer
  MediaPanel     what's playing anywhere in Windows, with controls and volume
  PowerBar       shut down / restart / sleep / lock (each asks first)
"""
from __future__ import annotations

import math
import socket
import subprocess
import threading
import time
from datetime import date, datetime, timedelta
from typing import TYPE_CHECKING, Callable

import psutil
from PyQt6.QtCore import QPointF, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import (QBrush, QColor, QFont, QFontMetrics, QLinearGradient, QPainter, QPainterPath, QPen,
                         QPixmap, QPolygonF, QRadialGradient)
from PyQt6.QtWidgets import (QApplication, QFrame, QGridLayout, QHBoxLayout, QLabel, QMessageBox, QPushButton,
                             QSizePolicy, QSlider, QVBoxLayout, QWidget)

from .desktop import C, HudCanvas, qcol

if TYPE_CHECKING:
    from ..assistant import Jarvis

MONO = "Courier New"
SANS = "Segoe UI"
DIGITS = "Bahnschrift"
NO_WINDOW = 0x08000000


def _font(family: str, size: float, bold: bool = False) -> QFont:
    f = QFont(family)
    f.setPointSizeF(size)
    f.setBold(bold)
    return f


def _bg(work: Callable, done: Callable | None, owner: QWidget) -> None:
    """Run `work` on a thread; hand the result to `done` on the GUI thread (via the owner's signal)."""
    def run():
        try:
            result = work()
        except Exception as e:
            result = e
        if done is not None and hasattr(owner, "_gui"):
            try:
                owner._gui.emit(lambda: done(result))
            except RuntimeError:
                pass  # the widget is gone
    threading.Thread(target=run, daemon=True).start()


def open_url(url: str) -> None:
    from ..tools.apps import open_url as go
    threading.Thread(target=go, args=(url,), daemon=True).start()


# ---- the frame every panel sits in -----------------------------------------------------------
class Panel(QFrame):
    """A HUD panel: cut corners, a faint glow on the edge, and a title tab."""
    _gui = pyqtSignal(object)

    def __init__(self, title: str = "", accent: str = C.PRI, parent=None):
        super().__init__(parent)
        self._title, self._accent = title, accent
        self._gui.connect(lambda fn: fn())
        self.body = QVBoxLayout(self)
        self.body.setContentsMargins(12, 26 if title else 10, 12, 10)
        self.body.setSpacing(6)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, c = self.width() - 1, self.height() - 1, 12
        poly = QPolygonF([QPointF(c, 0), QPointF(w, 0), QPointF(w, h - c), QPointF(w - c, h), QPointF(0, h),
                          QPointF(0, c)])
        g = QLinearGradient(0, 0, 0, h)
        g.setColorAt(0, QColor(2, 18, 28, 225))
        g.setColorAt(1, QColor(1, 9, 15, 215))
        p.setBrush(QBrush(g))
        p.setPen(QPen(qcol(C.BORDER_B, 200), 1))
        p.drawPolygon(poly)
        p.setPen(QPen(qcol(self._accent), 2))  # bright corner ticks
        p.drawLine(QPointF(c, 0), QPointF(c + 26, 0))
        p.drawLine(QPointF(0, c), QPointF(c, 0))
        p.drawLine(QPointF(w, h - c), QPointF(w - c, h))
        p.drawLine(QPointF(w - c - 26, h), QPointF(w - c, h))
        if self._title:
            p.setFont(_font(MONO, 8, True))
            p.setPen(QPen(qcol(self._accent), 1))
            p.drawText(QRectF(14, 6, w - 28, 14), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                       "▸ " + self._title)
            fm = QFontMetrics(_font(MONO, 8, True))
            x = 22 + fm.horizontalAdvance("▸ " + self._title)
            p.setPen(QPen(qcol(C.BORDER_B), 1))
            p.drawLine(QPointF(x, 13), QPointF(w - 14, 13))
        p.end()


def label(text: str = "", size: float = 8, color: str = C.TEXT_MED, bold: bool = False, family: str = MONO) -> QLabel:
    lb = QLabel(text)
    lb.setFont(_font(family, size, bold))
    lb.setStyleSheet(f"color: {color}; background: transparent;")
    return lb


class HexButton(QPushButton):
    """An angled HUD button (the tab shape from the reference design)."""

    def __init__(self, text: str, color: str = C.PRI, slant: int = 10, height: int = 28, parent=None):
        super().__init__(text, parent)
        self._color, self._slant = color, slant
        self.setFixedHeight(height)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setFont(_font(MONO, 8, True))
        self.setStyleSheet("background: transparent; border: none;")

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h, s = self.width() - 1, self.height() - 1, self._slant
        poly = QPolygonF([QPointF(s, 0), QPointF(w, 0), QPointF(w - s, h), QPointF(0, h)])
        hover = self.underMouse()
        down = self.isDown()
        p.setBrush(QBrush(qcol(self._color, 90 if down else 46 if hover else 16)))
        p.setPen(QPen(qcol(self._color, 255 if hover else 150), 1))
        p.drawPolygon(poly)
        p.setPen(QPen(qcol(C.WHITE if hover else self._color), 1))
        p.setFont(self.font())
        p.drawText(QRectF(s, 0, w - 2 * s, h), Qt.AlignmentFlag.AlignCenter, self.text())
        p.end()

    def enterEvent(self, e):
        self.update()
        super().enterEvent(e)

    def leaveEvent(self, e):
        self.update()
        super().leaveEvent(e)


# ---- background --------------------------------------------------------------------------------
class HudBackground(QWidget):
    """Deep blue-black with a faint hex grid and a glow behind the centre."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cache: QPixmap | None = None
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, False)

    def resizeEvent(self, e):
        self._cache = None
        super().resizeEvent(e)

    def paintEvent(self, _):
        if self._cache is None or self._cache.size() != self.size():
            pm = QPixmap(self.size())
            pm.fill(qcol(C.BG))
            p = QPainter(pm)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            W, H = self.width(), self.height()
            g = QRadialGradient(W / 2, H * 0.45, max(W, H) * 0.6)
            g.setColorAt(0, QColor(0, 60, 90, 70))
            g.setColorAt(1, QColor(0, 0, 0, 0))
            p.fillRect(self.rect(), QBrush(g))
            p.setPen(QPen(QColor(0, 120, 160, 16), 1))
            r = 18
            hw, hh = r * math.sqrt(3), r * 1.5
            row = 0
            y = 0.0
            while y < H + r:
                x0 = (hw / 2) if row % 2 else 0
                x = x0
                while x < W + hw:
                    pts = [QPointF(x + r * math.cos(math.radians(60 * k + 30)), y + r * math.sin(math.radians(60 * k + 30)))
                           for k in range(6)]
                    p.drawPolygon(QPolygonF(pts))
                    x += hw
                y += hh
                row += 1
            p.end()
            self._cache = pm
        p = QPainter(self)
        p.drawPixmap(0, 0, self._cache)
        p.end()


# ---- the centrepiece -----------------------------------------------------------------------------
def _mirror(pts):
    return [(1 - u, v) for u, v in pts]


# An original wireframe (unit square, symmetric about u = 0.5): helmet, faceplate, shoulders, chest.
HELMET = [(0.50, 0.035), (0.575, 0.05), (0.625, 0.10), (0.652, 0.17), (0.656, 0.235), (0.648, 0.29),
          (0.62, 0.345), (0.578, 0.39), (0.535, 0.412), (0.50, 0.418)]
FACE = [(0.50, 0.115), (0.56, 0.123), (0.598, 0.152), (0.612, 0.2), (0.607, 0.252), (0.59, 0.30), (0.565, 0.348),
        (0.532, 0.38), (0.50, 0.388)]
EYE = [(0.512, 0.212), (0.538, 0.204), (0.592, 0.197), (0.598, 0.207), (0.585, 0.216), (0.527, 0.226)]
TEMPLE = [(0.612, 0.2), (0.645, 0.175)]
CHEEK = [(0.59, 0.30), (0.632, 0.305)]
BROW = [(0.515, 0.17), (0.6, 0.168)]
EAR = [(0.628, 0.165), (0.651, 0.2), (0.651, 0.272), (0.628, 0.296), (0.618, 0.23), (0.628, 0.165)]
JAW = [(0.565, 0.348), (0.6, 0.362), (0.622, 0.343)]
MOUTH = [(0.47, 0.338), (0.53, 0.338)]
RIDGE = [(0.5, 0.035), (0.5, 0.115)]
NECK = [(0.545, 0.414), (0.552, 0.47), (0.585, 0.49)]
SHOULDER = [(0.585, 0.49), (0.70, 0.50), (0.80, 0.53), (0.875, 0.585), (0.905, 0.66), (0.925, 0.99)]
PAULDRON = [(0.72, 0.52), (0.82, 0.555), (0.865, 0.62), (0.80, 0.64), (0.74, 0.60)]
TORSO = [(0.70, 0.50), (0.715, 0.6), (0.69, 0.71), (0.645, 0.80), (0.615, 0.90), (0.605, 0.99)]
PEC = [(0.55, 0.535), (0.64, 0.555), (0.672, 0.6), (0.655, 0.665), (0.595, 0.705)]
COLLAR = [(0.45, 0.505), (0.50, 0.515), (0.55, 0.505)]
ABS = [[(0.535, 0.80), (0.60, 0.80)], [(0.535, 0.855), (0.595, 0.855)], [(0.535, 0.91), (0.59, 0.91)]]
REACTOR = (0.50, 0.655, 0.075)  # centre u, v and radius (in units of the box width)


class SuitCore(HudCanvas):
    """The suit: eyes glow brighter as he speaks, the arc reactor pulses with his voice (or yours)."""

    def __init__(self, jarvis: "Jarvis", parent=None):
        super().__init__("", parent)
        self.jarvis = jarvis
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent, False)
        self.setMinimumSize(360, 380)
        self.power_line = ""
        self.strain_line, self.strain_level = "", "ok"

    def _path(self, pts, ox, oy, s, close=False) -> QPainterPath:
        path = QPainterPath(QPointF(ox + pts[0][0] * s, oy + pts[0][1] * s))
        for u, v in pts[1:]:
            path.lineTo(ox + u * s, oy + v * s)
        if close:
            path.closeSubpath()
        return path

    def _glow(self, p: QPainter, path: QPainterPath, col: QColor, width: float = 1.3, strength: float = 1.0):
        for w, a in ((7.0, 18), (3.5, 50), (width, 230)):
            c = QColor(col)
            c.setAlpha(int(a * strength))
            p.setPen(QPen(c, w, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
            p.drawPath(path)

    def _reactor(self, p: QPainter, cx: float, cy: float, r: float, amp: float):
        t = self._core_phase
        main, acc = self._colours()
        live = (self.speaking or amp > 0.04) and not self.muted
        g = QRadialGradient(cx, cy, r * 2.2)
        g.setColorAt(0, qcol(C.PRI, int(150 + 100 * amp)))
        g.setColorAt(0.35, qcol(C.PRI, int(50 + 60 * amp)))
        g.setColorAt(1, qcol(C.PRI, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(g))
        p.drawEllipse(QPointF(cx, cy), r * 2.2, r * 2.2)
        p.setBrush(Qt.BrushStyle.NoBrush)
        # segmented outer ring (the coils), turning slowly
        rate = 1.0 + (2.0 if self.state == "thinking" else 0.0) + (1.0 if self.speaking else 0.0)
        for k in range(10):
            a0 = (k * 36 + t * rate * 12) % 360
            p.setPen(QPen(qcol(C.PRI, 220), max(2.0, r * 0.16), Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(QRectF(cx - r, cy - r, 2 * r, 2 * r), int(a0 * 16), int(24 * 16))
        for rr, a, wdt in ((1.22, 120, 1.2), (0.62, 200, 1.6)):
            p.setPen(QPen(qcol(acc if rr < 1 else main, a), wdt))
            p.drawEllipse(QPointF(cx, cy), r * rr, r * rr)
        # sweeping arcs
        for rr, span, dirn in ((1.4, 90, 1), (1.55, 50, -1)):
            p.setPen(QPen(qcol(acc, 160), 1.6))
            base = (t * rate * 30 * dirn) % 360
            for s in range(2):
                p.drawArc(QRectF(cx - r * rr, cy - r * rr, 2 * r * rr, 2 * r * rr), int((base + s * 180) * 16),
                          int(span * 16))
        # voice spikes
        spikes = []
        for i in range(48):
            a = math.radians(i * 7.5)
            wob = 0.5 + 0.5 * math.sin(t * 2.3 + i * 0.42)
            h = r * (0.06 + (amp * 0.55 * wob if live else 0.02 * math.sin(t + i)))
            spikes.append((cx + math.cos(a) * r * 1.62, cy + math.sin(a) * r * 1.62,
                           cx + math.cos(a) * (r * 1.62 + h), cy + math.sin(a) * (r * 1.62 + h)))
        p.setPen(QPen(qcol(acc if live else main, int(90 + 140 * amp)), 1.4))
        for x1, y1, x2, y2 in spikes:
            p.drawLine(QPointF(x1, y1), QPointF(x2, y2))
        core = r * (0.42 + 0.12 * amp)
        cg = QRadialGradient(cx, cy, core)
        cg.setColorAt(0, QColor(235, 252, 255))
        cg.setColorAt(0.6, qcol(C.PRI, 230))
        cg.setColorAt(1, qcol(C.PRI, 0))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QBrush(cg))
        p.drawEllipse(QPointF(cx, cy), core, core)

    def paintEvent(self, _):
        p = QPainter(self)
        if not p.isActive():
            return
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        text_h = 64
        s = min(W * 0.92, (H - text_h) * 1.0)
        ox, oy = (W - s) / 2, 4
        amp = self._amp_disp
        main, acc = self._colours()
        line = qcol(C.MUTED_C) if self.muted else qcol(C.PRI)
        red = QColor(255, 59, 92)
        # suit
        for pts in (HELMET, FACE):
            self._glow(p, self._path(pts + list(reversed(_mirror(pts)))[1:], ox, oy, s), line, 1.4)
        for pts in (TEMPLE, CHEEK, BROW, EAR, JAW, NECK, SHOULDER, PAULDRON, TORSO):
            for side in (pts, _mirror(pts)):
                self._glow(p, self._path(side, ox, oy, s), line, 1.2, 0.85)
        for pts in (PEC,):
            for side in (pts, _mirror(pts)):
                self._glow(p, self._path(side, ox, oy, s), red, 1.2, 0.8)
        self._glow(p, self._path(COLLAR, ox, oy, s), red, 1.4, 0.9)
        self._glow(p, self._path(MOUTH, ox, oy, s), line, 1.0, 0.7)
        self._glow(p, self._path(RIDGE, ox, oy, s), line, 1.0, 0.6)
        for seg in ABS:
            for side in (seg, _mirror(seg)):
                self._glow(p, self._path(side, ox, oy, s), line, 1.0, 0.6)
        # eyes: brighter as he speaks
        bright = 0.55 + (0.45 * min(1.0, amp * 2.2) if self.speaking else 0.15 * math.sin(self._core_phase * 2))
        for pts in (EYE, _mirror(EYE)):
            path = self._path(pts, ox, oy, s, close=True)
            cx = ox + sum(u for u, _ in pts) / len(pts) * s
            cy = oy + sum(v for _, v in pts) / len(pts) * s
            g = QRadialGradient(cx, cy, s * 0.08)
            g.setColorAt(0, qcol(C.PRI, int(160 * bright)))
            g.setColorAt(1, qcol(C.PRI, 0))
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QBrush(g))
            p.drawEllipse(QPointF(cx, cy), s * 0.08, s * 0.05)
            p.setBrush(QBrush(QColor(220, 250, 255, int(120 + 135 * bright))))
            p.setPen(QPen(qcol(C.PRI), 1))
            p.drawPath(path)
            p.setBrush(Qt.BrushStyle.NoBrush)
        u, v, rr = REACTOR
        self._reactor(p, ox + u * s, oy + v * s, rr * s, amp)

        # state + the PC's condition
        y = H - text_h
        if self.muted:
            txt, col = "⊘  MIC MUTED", qcol(C.MUTED_C)
        elif self.speaking:
            txt, col = "●  SPEAKING", qcol(C.ACC)
        elif self.state == "thinking":
            txt, col = f"{'◈' if self._blink else '◇'}  THINKING", qcol(C.ACC2)
        elif self.state == "listening":
            txt, col = f"{'●' if self._blink else '○'}  LISTENING", qcol(C.GREEN)
        else:
            txt, col = f"{'●' if self._blink else '○'}  STANDING BY", qcol(C.PRI)
        p.setFont(_font(MONO, 11, True))
        p.setPen(QPen(col, 1))
        p.drawText(QRectF(0, y, W, 20), Qt.AlignmentFlag.AlignCenter, txt)
        p.setFont(_font(SANS, 10, True))
        p.setPen(QPen(qcol(C.WHITE), 1))
        p.drawText(QRectF(0, y + 21, W, 20), Qt.AlignmentFlag.AlignCenter, self.power_line)
        scol = {"ok": C.GREEN, "busy": C.ACC2, "strained": C.RED}.get(self.strain_level, C.TEXT_MED)
        p.setFont(_font(SANS, 9))
        p.setPen(QPen(qcol(scol), 1))
        p.drawText(QRectF(0, y + 41, W, 20), Qt.AlignmentFlag.AlignCenter,
                   QFontMetrics(p.font()).elidedText(self.strain_line, Qt.TextElideMode.ElideRight, int(W - 20)))
        p.end()


# ---- system ---------------------------------------------------------------------------------------
class RingGauge(QWidget):
    """Two rings: CPU outside (orange), memory inside (cyan)."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(118, 118)
        self.cpu = self.ram = 0.0

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        c = QPointF(59, 59)
        for r, val, col, wdt in ((52, self.cpu, C.ACC, 9), (38, self.ram, C.PRI, 7)):
            box = QRectF(c.x() - r, c.y() - r, 2 * r, 2 * r)
            p.setPen(QPen(qcol(C.BAR_BG), wdt, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(box, 90 * 16, -360 * 16)
            shade = C.RED if val > 90 else col
            p.setPen(QPen(qcol(shade), wdt, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            p.drawArc(box, 90 * 16, int(-360 * 16 * max(0.0, min(1.0, val / 100))))
        for i in range(60):  # tick ring
            a = math.radians(i * 6)
            p.setPen(QPen(qcol(C.BORDER_B), 1))
            p.drawLine(QPointF(c.x() + math.cos(a) * 26, c.y() + math.sin(a) * 26),
                       QPointF(c.x() + math.cos(a) * (28 if i % 5 else 30), c.y() + math.sin(a) * (28 if i % 5 else 30)))
        p.setFont(_font(DIGITS, 12, True))
        p.setPen(QPen(qcol(C.ACC), 1))
        p.drawText(QRectF(0, 38, 118, 20), Qt.AlignmentFlag.AlignCenter, f"{self.cpu:.0f}%")
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(0, 58, 118, 20), Qt.AlignmentFlag.AlignCenter, f"{self.ram:.0f}%")
        p.setFont(_font(MONO, 6, True))
        p.setPen(QPen(qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 30, 118, 10), Qt.AlignmentFlag.AlignCenter, "CPU")
        p.drawText(QRectF(0, 78, 118, 10), Qt.AlignmentFlag.AlignCenter, "MEM")
        p.end()


class Bar(QWidget):
    def __init__(self, name: str, color: str = C.PRI, parent=None):
        super().__init__(parent)
        self.name, self.color, self.value, self.text = name, color, 0.0, "--"
        self.setFixedHeight(24)

    def set(self, value: float, text: str):
        self.value, self.text = max(0.0, min(100.0, value)), text
        self.update()

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W = self.width()
        p.setFont(_font(MONO, 7, True))
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(QRectF(0, 0, W, 12), Qt.AlignmentFlag.AlignLeft, self.name)
        col = qcol(C.RED if self.value > 90 else C.ACC if self.value > 75 else self.color)
        p.setPen(QPen(col if self.text != "--" else qcol(C.TEXT_DIM), 1))
        p.drawText(QRectF(0, 0, W, 12), Qt.AlignmentFlag.AlignRight, self.text)
        segs, gap = 28, 2
        sw = (W - gap * (segs - 1)) / segs
        lit = round(segs * self.value / 100)
        for i in range(segs):
            p.fillRect(QRectF(i * (sw + gap), 15, sw, 6), col if i < lit else qcol(C.BAR_BG))
        p.end()


class SystemPanel(Panel):
    def __init__(self, jarvis: "Jarvis", parent=None):
        super().__init__("SYSTEM", parent=parent)
        self.jarvis = jarvis
        top = QHBoxLayout()
        self.ring = RingGauge()
        top.addWidget(self.ring)
        col = QVBoxLayout()
        col.setSpacing(4)
        self.gpu, self.temp, self.vram = Bar("GPU"), Bar("GPU TEMP", "#ff6688"), Bar("GPU MEM", C.ACC2)
        for b in (self.gpu, self.temp, self.vram):
            col.addWidget(b)
        top.addLayout(col, 1)
        self.body.addLayout(top)
        self.status = label("", 8, C.GREEN, True)
        self.status.setWordWrap(True)
        self.body.addWidget(self.status)
        self.detail = label("", 7.5, C.TEXT_MED, family=SANS)
        self.detail.setWordWrap(True)
        self.body.addWidget(self.detail)
        row = QHBoxLayout()
        tm = HexButton("TASK MANAGER", C.PRI_DIM, height=24)
        tm.clicked.connect(lambda: subprocess.Popen(["taskmgr"], creationflags=NO_WINDOW))
        ask = HexButton("WHAT'S SLOW?", C.ACC2, height=24)
        ask.clicked.connect(lambda: self.jarvis.submit("What's using my PC the most right now, and is it under "
                                                       "strain?", "hud"))
        row.addWidget(tm)
        row.addWidget(ask)
        self.body.addLayout(row)
        t = QTimer(self)
        t.timeout.connect(self.refresh)
        t.start(1500)

    def refresh(self):
        s = self.jarvis.strain.now
        self.ring.cpu, self.ring.ram = s.cpu, s.ram
        self.ring.update()
        if s.gpu >= 0:
            self.gpu.set(s.gpu, f"{s.gpu:.0f}%")
            self.temp.set(s.gpu_temp, f"{s.gpu_temp:.0f}°C")
            self.vram.set(max(0, s.vram), f"{s.vram_used_gb:.1f}/{s.vram_total_gb:.0f} GB" if s.vram >= 0 else "--")
        else:
            for b in (self.gpu, self.temp, self.vram):
                b.set(0, "N/A")
        verdict = {"ok": ("● ALL SYSTEMS NOMINAL", C.GREEN), "busy": ("◐ WORKING HARD", C.ACC2),
                   "strained": ("▲ UNDER STRAIN", C.RED)}[s.level]
        self.status.setText(verdict[0])
        self.status.setStyleSheet(f"color: {verdict[1]}; background: transparent;")
        if s.reasons:
            self.detail.setText("; ".join(s.reasons))
        else:
            top = ", ".join(f"{n} {v:.0f}%" for n, v in s.top_cpu[:2] if v >= 1)
            mem = f"RAM {s.ram_used_gb:.1f} of {s.ram_total_gb:.0f} GB"
            self.detail.setText(f"{mem}" + (f"  ·  busiest: {top}" if top else ""))


class DiskPanel(Panel):
    def __init__(self, parent=None):
        super().__init__("DISK", parent=parent)
        self.rows: dict[str, Bar] = {}
        for part in psutil.disk_partitions(all=False):
            if "cdrom" in part.opts or not part.fstype:
                continue
            b = ClickBar(part.device.rstrip("\\"), lambda d=part.mountpoint: subprocess.Popen(
                ["explorer", d], creationflags=NO_WINDOW))
            self.rows[part.mountpoint] = b
            self.body.addWidget(b)
        t = QTimer(self)
        t.timeout.connect(self.refresh)
        t.start(30000)
        self.refresh()

    def refresh(self):
        for mount, bar in self.rows.items():
            try:
                u = psutil.disk_usage(mount)
                bar.set(u.percent, f"{u.used / 2**30:.0f} / {u.total / 2**30:.0f} GB")
            except OSError:
                bar.set(0, "--")


class ClickBar(Bar):
    def __init__(self, name, on_click, parent=None):
        super().__init__(name, parent=parent)
        self._on_click = on_click
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open in File Explorer")

    def mouseReleaseEvent(self, e):
        self._on_click()


class Sparkline(QWidget):
    def __init__(self, series: list[tuple[str, str]], parent=None):
        super().__init__(parent)
        self.series = series  # [(history key, colour)]
        self.data: dict[str, list[float]] = {}
        self.setFixedHeight(46)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        W, H = self.width(), self.height()
        p.setPen(QPen(qcol(C.BORDER), 1))
        for y in (H * 0.33, H * 0.66):
            p.drawLine(QPointF(0, y), QPointF(W, y))
        top = max([1.0] + [max(v) for v in self.data.values() if v])
        for key, col in self.series:
            vals = self.data.get(key) or []
            if len(vals) < 2:
                continue
            path = QPainterPath()
            for i, v in enumerate(vals):
                pt = QPointF(W * i / (len(vals) - 1), H - 2 - (H - 4) * v / top)
                path.moveTo(pt) if i == 0 else path.lineTo(pt)
            p.setPen(QPen(qcol(col), 1.4))
            p.drawPath(path)
        p.end()


def _rate(kbps: float) -> str:
    return f"{kbps:.0f} KB/s" if kbps < 1024 else f"{kbps / 1024:.1f} MB/s"


class NetworkPanel(Panel):
    def __init__(self, jarvis: "Jarvis", parent=None):
        super().__init__("NETWORK", parent=parent)
        self.jarvis = jarvis
        grid = QGridLayout()
        grid.setVerticalSpacing(2)
        self.local, self.public = ClickLabel("--"), ClickLabel("--")
        self.up, self.down = label("--", 8, C.ACC, True), label("--", 8, C.GREEN, True)
        for r, (name, w) in enumerate((("LOCAL IP", self.local), ("PUBLIC IP", self.public), ("UPLOAD", self.up),
                                        ("DOWNLOAD", self.down))):
            grid.addWidget(label(name, 7, C.TEXT_DIM, True), r, 0)
            grid.addWidget(w, r, 1, Qt.AlignmentFlag.AlignRight)
        self.body.addLayout(grid)
        self.spark = Sparkline([("down", C.GREEN), ("up", C.ACC)])
        self.body.addWidget(self.spark)
        t = QTimer(self)
        t.timeout.connect(self.refresh)
        t.start(1500)
        self._addresses()

    def _addresses(self):
        def work():
            local = "--"
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
                    s.connect(("8.8.8.8", 80))
                    local = s.getsockname()[0]
            except OSError:
                pass
            public = "--"
            try:
                import requests
                public = requests.get("https://api.ipify.org", timeout=6).text.strip()[:45]
            except Exception:
                pass
            return local, public

        def done(r):
            if isinstance(r, tuple):
                self.local.setText(r[0])
                self.public.setText(r[1])
        _bg(work, done, self)
        QTimer.singleShot(30 * 60 * 1000, self._addresses)

    def refresh(self):
        s = self.jarvis.strain.now
        self.up.setText("↑ " + _rate(s.up_kbps))
        self.down.setText("↓ " + _rate(s.down_kbps))
        h = self.jarvis.strain.history
        self.spark.data = {"up": list(h["up"])[-60:], "down": list(h["down"])[-60:]}
        self.spark.update()


class ClickLabel(QLabel):
    """Click to copy."""

    def __init__(self, text: str, parent=None):
        super().__init__(text, parent)
        self.setFont(_font(MONO, 8, True))
        self.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Click to copy")

    def mouseReleaseEvent(self, e):
        QApplication.clipboard().setText(self.text())
        old = self.text()
        self.setText("copied ✓")
        QTimer.singleShot(900, lambda: self.setText(old))


# ---- left column ---------------------------------------------------------------------------------
DEFAULT_APPS = ["Google Chrome", "Steam", "Discord", "Spotify", "File Explorer", "Visual Studio Code", "OBS Studio",
                "Settings", "Control Panel"]


class LaunchPanel(Panel):
    """Angled tabs that open apps (the ones you have), via the same launcher Jarvis uses."""

    def __init__(self, jarvis: "Jarvis", parent=None):
        super().__init__("LAUNCH", parent=parent)
        self.jarvis = jarvis
        self._wanted = list(jarvis.settings.get("hud.quick_apps") or DEFAULT_APPS)
        self.grid = QVBoxLayout()
        self.grid.setSpacing(5)
        self.body.addLayout(self.grid)
        _bg(self._installed, self._build, self)

    def _installed(self) -> list[str]:
        from ..tools.apps import match_app, start_apps
        apps = start_apps()
        out = []
        for name in self._wanted:
            app, score = match_app(name, apps)
            if app and score >= 0.8:
                out.append(name)
        return out[:8]

    def _build(self, names):
        if isinstance(names, Exception) or not names:
            names = self._wanted[:6]
        for i, name in enumerate(names):
            b = HexButton(name.upper().replace("VISUAL STUDIO CODE", "VS CODE").replace("GOOGLE ", ""), C.PRI,
                          slant=12, height=28)
            b.setContentsMargins(i % 2 * 8, 0, 0, 0)
            b.clicked.connect(lambda _=False, n=name: self._open(n))
            self.grid.addWidget(b)

    def _open(self, name: str):
        from ..events import bus
        from ..tools import outcome
        from ..tools.apps import open_app
        bus.emit("notice", level="info", text=f"Opening {name}…")

        def work():
            return outcome.unwrap(str(open_app(name))).split("\n")[0]
        _bg(work, lambda r: bus.emit("notice", level="info", text=str(r)[:160]), self)


class CalendarPanel(Panel):
    def __init__(self, jarvis: "Jarvis", parent=None):
        super().__init__("CALENDAR", parent=parent)
        self.jarvis = jarvis
        self.month = label("", 9, C.PRI, True)
        self.body.addWidget(self.month)
        self.week = QGridLayout()
        self.week.setHorizontalSpacing(2)
        self.body.addLayout(self.week)
        self.events = QVBoxLayout()
        self.events.setSpacing(3)
        self.body.addLayout(self.events)
        self._draw_week()
        t = QTimer(self)
        t.timeout.connect(self.refresh)
        t.start(10 * 60 * 1000)
        self.refresh()

    def _draw_week(self):
        today = date.today()
        self.month.setText(today.strftime("%B %Y").upper())
        start = today - timedelta(days=(today.weekday() + 1) % 7)  # Sunday
        for i in range(7):
            d = start + timedelta(days=i)
            is_today = d == today
            top = label(d.strftime("%a")[:2], 7, C.TEXT_DIM, True)
            num = QLabel(str(d.day))
            num.setFont(_font(DIGITS, 11, True))
            num.setAlignment(Qt.AlignmentFlag.AlignCenter)
            num.setStyleSheet(f"color: {'#001018' if is_today else C.WHITE}; background: "
                              f"{C.PRI if is_today else 'transparent'}; border-radius: 3px;")
            top.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self.week.addWidget(top, 0, i)
            self.week.addWidget(num, 1, i)

    def refresh(self):
        def work():
            from ..tools.gservices import events_between
            now = datetime.now()
            return events_between(now, datetime.combine(date.today() + timedelta(days=3), datetime.min.time()))

        def done(events):
            while self.events.count():
                w = self.events.takeAt(0).widget()
                if w:
                    w.deleteLater()
            if isinstance(events, Exception) or events is None:
                b = HexButton("CONNECT GOOGLE CALENDAR", C.PRI_DIM, height=24)
                b.clicked.connect(lambda: self.jarvis_window().open_settings("google"))
                self.events.addWidget(b)
                return
            if not events:
                self.events.addWidget(label("Nothing in the next few days.", 8, C.TEXT_MED, family=SANS))
            for ev in events[:4]:
                when = ev["start"].strftime("%a") if ev["all_day"] else ev["start"].strftime("%a %H:%M")
                ln = LinkLabel(f"<span style='color:{C.ACC2}'>{when}</span>&nbsp; {ev['title'][:38]}",
                               lambda: open_url("https://calendar.google.com"))
                self.events.addWidget(ln)
        _bg(work, done, self)

    def jarvis_window(self):
        return self.window()


class LinkLabel(QLabel):
    def __init__(self, html: str, on_click, parent=None):
        super().__init__(html, parent)
        self._on_click = on_click
        self.setTextFormat(Qt.TextFormat.RichText)
        self.setFont(_font(SANS, 8.5))
        self.setStyleSheet(f"color: {C.WHITE}; background: transparent;")
        self.setCursor(Qt.CursorShape.PointingHandCursor)

    def mouseReleaseEvent(self, e):
        self._on_click()


class WeatherPanel(Panel):
    def __init__(self, parent=None):
        super().__init__("WEATHER", parent=parent)
        row = QHBoxLayout()
        self.temp = label("--", 26, C.WHITE, True, DIGITS)
        row.addWidget(self.temp)
        col = QVBoxLayout()
        col.setSpacing(0)
        self.cond = label("", 9, C.PRI, True, SANS)
        self.place = label("", 7.5, C.TEXT_DIM, family=SANS)
        col.addWidget(self.cond)
        col.addWidget(self.place)
        row.addLayout(col, 1)
        self.body.addLayout(row)
        self.detail = label("", 8, C.TEXT_MED, family=SANS)
        self.body.addWidget(self.detail)
        self.days = label("", 8, C.TEXT, family=SANS)
        self.days.setTextFormat(Qt.TextFormat.RichText)
        self.body.addWidget(self.days)
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.setToolTip("Open the full forecast")
        t = QTimer(self)
        t.timeout.connect(self.refresh)
        t.start(20 * 60 * 1000)
        self.refresh()

    def mouseReleaseEvent(self, e):
        open_url("https://www.google.com/search?q=weather+forecast")

    def refresh(self):
        def work():
            from ..tools.weather import weather_data
            return weather_data(days=3)

        def done(d):
            if isinstance(d, Exception):
                self.cond.setText("Weather unavailable")
                return
            from ..tools.weather import describe
            c, daily = d["current"], d["daily"]
            deg = "°F" if d["_imperial"] else "°C"
            self.temp.setText(f"{round(c['temperature_2m'])}°")
            self.cond.setText(describe(c["weather_code"]).capitalize())
            self.place.setText(d["_loc"].get("label", ""))
            self.detail.setText(f"Feels {round(c['apparent_temperature'])}{deg} · humidity "
                                f"{c['relative_humidity_2m']}% · wind {round(c['wind_speed_10m'])}")
            rows = []
            for i, name in enumerate(("Today", "Tomorrow", (date.today() + timedelta(days=2)).strftime("%A"))):
                if i < len(daily["time"]):
                    rows.append(f"<b style='color:{C.PRI}'>{name}</b>&nbsp; {round(daily['temperature_2m_max'][i])}° / "
                                f"{round(daily['temperature_2m_min'][i])}° &nbsp;<span style='color:{C.TEXT_MED}'>"
                                f"{describe(daily['weather_code'][i])}</span>")
            self.days.setText("<br>".join(rows))
        _bg(work, done, self)


class TasksPanel(Panel):
    """Your to-do list and reminders - the same ones Jarvis keeps. Tick to finish, type to add."""

    def __init__(self, jarvis: "Jarvis", parent=None):
        super().__init__("TO-DO & REMINDERS", parent=parent)
        self.jarvis = jarvis
        self.list = QVBoxLayout()
        self.list.setSpacing(3)
        self.body.addLayout(self.list)
        from PyQt6.QtWidgets import QLineEdit
        self.add = QLineEdit()
        self.add.setPlaceholderText("+ add a task, press Enter")
        self.add.setFont(_font(SANS, 8.5))
        self.add.setStyleSheet(f"QLineEdit {{ background: rgba(0,13,20,200); color: {C.WHITE}; border: 1px solid "
                               f"{C.BORDER}; border-radius: 3px; padding: 3px 6px; }}")
        self.add.returnPressed.connect(self._add)
        self.body.addWidget(self.add)
        self._last = None
        t = QTimer(self)
        t.timeout.connect(self.refresh)
        t.start(5000)
        self.refresh()

    def _add(self):
        text = self.add.text().strip()
        if text:
            self.jarvis.memory.add_task(text)
            self.add.clear()
            self.refresh()

    def _done(self, task_id):
        self.jarvis.memory.complete_task(task_id)
        self.refresh()

    def refresh(self):
        from PyQt6.QtWidgets import QCheckBox
        mem = self.jarvis.memory
        tasks = mem.tasks()[:6]
        rems = mem.pending_reminders()[:3]
        key = (tuple((t["id"], t["text"]) for t in tasks), tuple((r["id"], r["due"]) for r in rems))
        if key == self._last:
            return
        self._last = key
        while self.list.count():
            w = self.list.takeAt(0).widget()
            if w:
                w.deleteLater()
        for t in tasks:
            cb = QCheckBox(t["text"][:40])
            cb.setFont(_font(SANS, 8.5))
            cb.setStyleSheet(f"QCheckBox {{ color: {C.WHITE}; background: transparent; }} QCheckBox::indicator "
                             f"{{ width: 12px; height: 12px; border: 1px solid {C.PRI_DIM}; background: transparent; }}")
            cb.toggled.connect(lambda _v, i=t["id"]: QTimer.singleShot(250, lambda: self._done(i)))
            self.list.addWidget(cb)
        for r in rems:
            when = datetime.fromtimestamp(r["due"]).strftime("%a %H:%M")
            self.list.addWidget(label(f"⏰ {when}  {r['message'][:32]}", 8, C.ACC2, family=SANS))
        if not tasks and not rems:
            self.list.addWidget(label('Nothing on the list. Say "Jarvis, remind me…"', 8, C.TEXT_DIM, family=SANS))


# ---- centre extras ---------------------------------------------------------------------------------
DEFAULT_LINKS = [["Google", "https://www.google.com"], ["Gmail", "https://mail.google.com"],
                 ["YouTube", "https://www.youtube.com"], ["Calendar", "https://calendar.google.com"],
                 ["Drive", "https://drive.google.com"], ["Classroom", "https://classroom.google.com"],
                 ["Maps", "https://maps.google.com"], ["Wikipedia", "https://en.wikipedia.org"],
                 ["News", "https://news.google.com"], ["Twitch", "https://www.twitch.tv"]]


class LinksPanel(QWidget):
    """The column of sites beside the suit."""

    def __init__(self, jarvis: "Jarvis", parent=None):
        super().__init__(parent)
        lay = QVBoxLayout(self)
        lay.setContentsMargins(0, 30, 0, 0)
        lay.setSpacing(1)
        for name, url in (jarvis.settings.get("hud.links") or DEFAULT_LINKS)[:12]:
            b = QPushButton(name.upper())
            b.setFont(_font(MONO, 9, True))
            b.setCursor(Qt.CursorShape.PointingHandCursor)
            b.setToolTip(url)
            b.setStyleSheet(f"QPushButton {{ color: {C.WHITE}; background: transparent; border: none; "
                            f"text-align: right; padding: 3px 4px; }} QPushButton:hover {{ color: {C.PRI}; }}")
            b.clicked.connect(lambda _=False, u=url: open_url(u))
            lay.addWidget(b)
        lay.addStretch()
        self.setFixedWidth(116)


class FoldersPanel(Panel):
    FOLDERS = [("DOWNLOADS", "shell:Downloads"), ("DOCUMENTS", "shell:Personal"), ("DESKTOP", "shell:Desktop"),
               ("PICTURES", "shell:My Pictures"), ("MUSIC", "shell:My Music"), ("VIDEOS", "shell:My Video")]

    def __init__(self, parent=None):
        super().__init__("FOLDERS", parent=parent)
        grid = QGridLayout()
        grid.setSpacing(5)
        for i, (name, target) in enumerate(self.FOLDERS):
            b = HexButton(name, C.PRI_DIM, slant=8, height=24)
            b.clicked.connect(lambda _=False, t=target: subprocess.Popen(["explorer", t], creationflags=NO_WINDOW))
            grid.addWidget(b, i // 2, i % 2)
        self.body.addLayout(grid)


class MediaPanel(Panel):
    """Whatever is playing in Windows (Spotify, YouTube in Chrome, ...): title, controls, volume."""

    def __init__(self, parent=None):
        super().__init__("NOW PLAYING", parent=parent)
        self.title = label("Nothing playing", 9, C.WHITE, True, SANS)
        self.artist = label("", 8, C.TEXT_MED, family=SANS)
        self.body.addWidget(self.title)
        self.body.addWidget(self.artist)
        row = QHBoxLayout()
        self.buttons = {}
        for key, text in (("prev", "◀◀"), ("play", "▶ ❚❚"), ("next", "▶▶")):
            b = HexButton(text, C.PRI, slant=6, height=26)
            b.setFont(_font("Segoe UI Symbol", 8, True))
            b.setFixedWidth(52)
            b.clicked.connect(lambda _=False, k=key: self._control(k))
            row.addWidget(b)
            self.buttons[key] = b
        self.vol = QSlider(Qt.Orientation.Horizontal)
        self.vol.setRange(0, 100)
        self.vol.setToolTip("Volume")
        self.vol.setStyleSheet(f"QSlider::groove:horizontal {{ height: 4px; background: {C.BAR_BG}; }}"
                               f"QSlider::sub-page:horizontal {{ background: {C.PRI}; }}"
                               f"QSlider::handle:horizontal {{ background: {C.WHITE}; width: 10px; margin: -5px 0; "
                               f"border-radius: 5px; }}")
        self.vol.sliderReleased.connect(self._set_volume)
        row.addWidget(self.vol, 1)
        self.body.addLayout(row)
        self._busy = False
        t = QTimer(self)
        t.timeout.connect(self.refresh)
        t.start(2500)
        self.refresh()
        self._read_volume()

    @staticmethod
    async def _session():
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as Manager
        mgr = await Manager.request_async()
        return mgr.get_current_session()

    def refresh(self):
        if self._busy:
            return
        self._busy = True

        def work():
            import asyncio

            async def read():
                s = await self._session()
                if s is None:
                    return None
                props = await s.try_get_media_properties_async()
                status = s.get_playback_info().playback_status
                app = (s.source_app_user_model_id or "").split("!")[-1].split(".")[0]
                return props.title or "", props.artist or "", int(status) == 4, app
            return asyncio.run(read())

        def done(r):
            self._busy = False
            if isinstance(r, Exception) or r is None:
                self.title.setText("Nothing playing")
                self.artist.setText("")
                return
            title, artist, playing, app = r
            fm = QFontMetrics(self.title.font())
            self.title.setText(fm.elidedText(title or "Unknown", Qt.TextElideMode.ElideRight, self.width() - 30))
            self.artist.setText(("▶ " if playing else "❚❚ ") + " · ".join(x for x in (artist, app) if x))
        _bg(work, done, self)

    def _control(self, key: str):
        def work():
            import asyncio

            async def go():
                s = await self._session()
                if s is None:
                    return False
                return await {"prev": s.try_skip_previous_async, "play": s.try_toggle_play_pause_async,
                              "next": s.try_skip_next_async}[key]()
            ok = asyncio.run(go())
            if not ok:  # no session that accepts commands: fall back to the media keys
                from ..tools.system import media_control
                media_control({"prev": "previous", "play": "play_pause", "next": "next"}[key])
            return ok
        _bg(work, lambda _r: QTimer.singleShot(400, self.refresh), self)

    def _read_volume(self):
        try:
            from .. import winutil
            from ..tools.system import _speakers
            winutil.ensure_com()
            self.vol.setValue(round(_speakers().GetMasterVolumeLevelScalar() * 100))
        except Exception:
            self.vol.setEnabled(False)

    def _set_volume(self):
        try:
            from .. import winutil
            from ..tools.system import _speakers
            winutil.ensure_com()
            _speakers().SetMasterVolumeLevelScalar(self.vol.value() / 100, None)
        except Exception:
            pass


# ---- header pieces ---------------------------------------------------------------------------------
class ClockWidget(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(250, 64)
        t = QTimer(self)
        t.timeout.connect(self.update)
        t.start(1000)

    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        now = datetime.now()
        W = self.width()
        p.setFont(_font(DIGITS, 34, True))
        p.setPen(QPen(qcol(C.WHITE), 1))
        p.drawText(QRectF(0, -4, W - 64, 56), Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter,
                   now.strftime("%H:%M"))
        p.setFont(_font(DIGITS, 14, True))
        p.setPen(QPen(qcol(C.PRI), 1))
        p.drawText(QRectF(W - 60, 6, 60, 22), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter,
                   now.strftime("%S"))
        p.setFont(_font(MONO, 7.5, True))
        p.setPen(QPen(qcol(C.ACC), 1))
        p.drawText(QRectF(W - 60, 26, 60, 14), Qt.AlignmentFlag.AlignLeft, now.strftime("%A")[:9].upper())
        up = time.time() - psutil.boot_time()
        p.setPen(QPen(qcol(C.TEXT_MED), 1))
        p.drawText(QRectF(0, 48, W, 14), Qt.AlignmentFlag.AlignRight,
                   f"{now.strftime('%d %B %Y').upper()}  ·  UP {int(up // 86400)}d {int(up % 86400 // 3600)}h "
                   f"{int(up % 3600 // 60)}m")
        p.end()


class PowerBar(QWidget):
    """Shut down, restart, sleep, lock - each one asks first."""

    def __init__(self, parent=None):
        super().__init__(parent)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(0, 0, 0, 0)
        lay.setSpacing(6)
        for text, action, col, question in (
                ("SHUTDOWN", "shutdown", C.RED, "Shut down the PC in one minute? (Say 'cancel shutdown' to stop it.)"),
                ("RESTART", "restart", C.ACC, "Restart the PC in one minute?"),
                ("SLEEP", "sleep", C.ACC2, "Put the PC to sleep now?"),
                ("LOCK", "lock", C.PRI, "")):
            b = HexButton(text, col, slant=9, height=26)
            b.setFixedWidth(104)
            b.clicked.connect(lambda _=False, a=action, q=question: self._do(a, q))
            lay.addWidget(b)

    def _do(self, action: str, question: str):
        if question:
            box = QMessageBox(self.window())
            box.setWindowTitle("J.A.R.V.I.S.")
            box.setText(question)
            box.setStandardButtons(QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No)
            box.setDefaultButton(QMessageBox.StandardButton.No)
            box.setStyleSheet(f"QMessageBox {{ background: {C.PANEL}; }} QLabel {{ color: {C.WHITE}; }} "
                              f"QPushButton {{ color: {C.PRI}; background: transparent; border: 1px solid {C.PRI_DIM};"
                              f" padding: 5px 16px; }}")
            if box.exec() != QMessageBox.StandardButton.Yes:
                return
        from ..events import bus
        from ..tools.system import power
        bus.emit("notice", level="info", text=str(power(action)))
