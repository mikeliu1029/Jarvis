"""The pop-up: a small bar that glides down from the top edge of the screen while Jarvis is talking,
shows his words as he says them, and slides back up out of sight when he's done.

It never takes focus and clicks pass straight through it, so it can sit over a game or a document.
"""
from __future__ import annotations

import math
import time
from typing import TYPE_CHECKING

from PyQt6.QtCore import QEasingCurve, QPoint, QPropertyAnimation, QRectF, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QColor, QFont, QFontMetrics, QGuiApplication, QPainter, QPainterPath, QPen
from PyQt6.QtWidgets import QWidget

from ..events import bus

if TYPE_CHECKING:
    from ..assistant import Jarvis

W, H = 540, 78
CYAN = QColor("#00d4ff")
AMBER = QColor("#ffcc00")


class SpeechPopup(QWidget):
    _event = pyqtSignal(object)

    def __init__(self, jarvis: "Jarvis"):
        super().__init__(None, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint |
                         Qt.WindowType.Tool | Qt.WindowType.WindowDoesNotAcceptFocus |
                         Qt.WindowType.WindowTransparentForInput)
        self.jarvis = jarvis
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setFixedSize(W, H)
        self._caption = ""      # everything he has said so far in this reply (as heard, not as generated)
        self._typed = 0.0       # how much of it is on screen - typed out at speaking pace
        self._queue: list[tuple[float, str]] = []  # captions waiting for their audio to be heard
        self._state = "idle"
        self._level = 0.0
        self._phase = 0.0
        self._shown = False
        self._hide_at = 0.0
        self._since = time.time()
        self._caption_at = 0.0
        from ..voice import audio
        self._audio = audio

        self._anim = QPropertyAnimation(self, b"pos", self)
        self._anim.finished.connect(self._parked)
        self._place(hidden=True)  # waits above the top edge, hidden, until he speaks

        self._timer = QTimer(self)
        self._timer.timeout.connect(self._tick)
        self._timer.start(16)
        self._event.connect(self._on_event)
        bus.subscribe(self._from_bus)

    # ---- geometry & motion ------------------------------------------------------------------
    def _positions(self) -> tuple[QPoint, QPoint]:
        screen = QGuiApplication.primaryScreen().geometry()
        x = screen.x() + (screen.width() - W) // 2
        return QPoint(x, screen.y() - H - 6), QPoint(x, screen.y() + 12)

    def _place(self, hidden: bool) -> None:
        up, down = self._positions()
        self.move(up if hidden else down)

    def _slide(self, show: bool) -> None:
        if show == self._shown:
            return
        self._shown = show
        up, down = self._positions()
        if show and not self.isVisible():
            self.move(up)
            self.show()
        self._anim.stop()
        self._anim.setStartValue(self.pos())
        self._anim.setEndValue(down if show else up)
        self._anim.setEasingCurve(QEasingCurve.Type.OutBack if show else QEasingCurve.Type.InCubic)
        self._anim.setDuration(460 if show else 360)
        self._anim.start()

    def _parked(self) -> None:
        if not self._shown:
            self.hide()  # fully gone, so it can't peek onto a monitor that sits above this one

    # ---- events ---------------------------------------------------------------------------
    def _from_bus(self, event: dict) -> None:
        if event["type"] in ("state", "jarvis", "jarvis_partial"):
            self._event.emit(event)  # hop onto the GUI thread

    def _on_event(self, e: dict) -> None:
        if e["type"] == "state":
            self._state = e["value"]
            self._since = time.time()
            if self._state in ("speaking", "thinking"):
                self._hide_at = 0.0
                if not self._shown and time.time() - self._caption_at > 2.5:
                    self._set_caption("")  # don't flash the last thing he said before
                self._slide(True)
            else:
                self._queue.clear()  # cut off mid-sentence: words he never got to say don't appear
                self._hide_at = time.time() + 1.3  # linger a moment so sentences don't make it bob
        elif e["type"] == "jarvis_partial":
            # Stamped with the moment its audio is heard (live voice) - held back until then.
            self._queue.append((float(e.get("at") or 0.0), e.get("text", "")))
        elif e["type"] == "jarvis" and not getattr(self.jarvis, "live_active", False):
            # Local voice: the whole reply arrives as he starts saying it; typing paces it.
            self._set_caption(e.get("text", ""))
            if not self._shown and self._state == "speaking":
                self._slide(True)

    def _set_caption(self, text: str) -> None:
        shown = self._caption[:int(self._typed)]
        if not text.startswith(shown):
            self._typed = 0.0  # a new reply rather than more of the same one
        self._caption = text
        self._caption_at = time.time()

    def _tick(self) -> None:
        self._phase += 0.016
        target = self._audio.output_level() if self._state == "speaking" else 0.0
        self._level += (max(target, self._level * 0.85) - self._level) * 0.4
        now = time.time()
        while self._queue and self._queue[0][0] <= now:
            self._set_caption(self._queue.pop(0)[1])
            if not self._shown:
                self._slide(True)
        # Type the words out a touch faster than he speaks (~20 characters a second), catching up quickly
        # if the text has run ahead of the screen.
        behind = len(self._caption) - self._typed
        if behind > 0:
            self._typed = min(len(self._caption), self._typed + 0.016 * (20 if behind < 20 else 20 + 2 * behind))
        if self._hide_at and now > self._hide_at and self._state not in ("speaking", "thinking"):
            self._hide_at = 0.0
            self._slide(False)
        elif self._shown and self._state == "thinking" and now - self._since > 25:
            self._slide(False)  # never hang around if something got stuck
        if self._shown or self._anim.state() == QPropertyAnimation.State.Running:
            self.update()

    # ---- drawing ----------------------------------------------------------------------------
    def paintEvent(self, _):
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        r = QRectF(2, 2, W - 4, H - 4)
        path = QPainterPath()
        path.addRoundedRect(r, H / 2 - 2, H / 2 - 2)
        p.fillPath(path, QColor(2, 10, 16, 236))
        glow = QColor(CYAN)
        glow.setAlpha(int(90 + 120 * min(1.0, self._level * 1.5)))
        p.setPen(QPen(glow, 1.6))
        p.drawPath(path)

        # mini reactor: rings that turn, and a core that swells with his voice
        cx, cy, rad = H / 2 + 4, H / 2, H / 2 - 14
        accent = AMBER if self._state == "thinking" else CYAN
        speed = 3.0 if self._state == "thinking" else 1.2
        for k, (rr, span, w) in enumerate(((1.0, 110, 2.0), (0.72, 70, 1.4))):
            col = QColor(accent if k == 0 else CYAN)
            col.setAlpha(210 if k == 0 else 140)
            p.setPen(QPen(col, w))
            box = QRectF(cx - rad * rr, cy - rad * rr, 2 * rad * rr, 2 * rad * rr)
            base = (self._phase * speed * (90 if k == 0 else -130)) % 360
            for s in range(2):
                p.drawArc(box, int((base + s * 180) * 16), int(span * 16))
        core = rad * (0.30 + 0.28 * min(1.0, self._level * 1.8))
        p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor(200, 245, 255, 230))
        p.drawEllipse(QRectF(cx - core, cy - core, 2 * core, 2 * core))

        # waveform under the caption
        x0, wy = H + 8, H - 19
        p.setPen(Qt.PenStyle.NoPen)
        bars = int((W - x0 - 30) / 6.5)
        for i in range(bars):
            env = 1 - abs(i - (bars - 1) / 2) / ((bars - 1) / 2)
            hgt = 2 + (self._level * 20 * env * (0.6 + 0.4 * math.sin(self._phase * 9 + i * 0.7)))
            c = QColor(CYAN)
            c.setAlpha(170 if self._level > 0.05 else 70)
            p.setBrush(c)
            p.drawRoundedRect(QRectF(x0 + i * 6.5, wy - hgt / 2, 3.5, max(2.0, hgt)), 1.5, 1.5)

        # caption: the end of what he's saying, so the newest words are always visible
        font = QFont("Segoe UI", 11)
        p.setFont(font)
        p.setPen(QColor("#d8f8ff"))
        text = self._caption[:int(self._typed)] or ("Thinking…" if self._state == "thinking" else "")
        fm = QFontMetrics(font)
        avail = int(W - x0 - 26)
        if fm.horizontalAdvance(text) > avail:
            words = text.split()
            while len(words) > 1 and fm.horizontalAdvance("… " + " ".join(words)) > avail:
                words.pop(0)  # drop whole words from the front so the newest ones stay readable
            text = "… " + " ".join(words)
        p.drawText(QRectF(x0, 12, avail, 26), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, text)
        p.end()
