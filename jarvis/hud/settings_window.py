"""Jarvis's Settings window - every option in one place, so nobody has to edit a file.

Changes are saved the moment they're made: options to config/my_settings.json (private, never uploaded),
keys to .env. Most take effect after a restart, which the bar at the bottom offers in one click.
"""
from __future__ import annotations

import re
import threading
import time
from typing import TYPE_CHECKING, Any, Callable

from PyQt6.QtCore import QProcess, QProcessEnvironment, Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFont, QIcon
from PyQt6.QtWidgets import (QApplication, QCheckBox, QComboBox, QFileDialog, QFrame, QGridLayout, QHBoxLayout,
                             QLabel, QLineEdit, QListWidget, QListWidgetItem, QPlainTextEdit, QPushButton,
                             QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget)

from .. import config
from ..config import CONFIG_DIR, PROJECT_DIR, get_settings, save_user_setting, secret, set_secret
from .desktop import C

if TYPE_CHECKING:
    from ..assistant import Jarvis

ATTACHED = False  # True while the desktop HUD is running and can open this window itself

GEMINI_KEY_URL = "https://aistudio.google.com/apikey"
VOICES = [("Charon", "Charon - deep, calm, informative (the default)"), ("Orus", "Orus - firm"),
          ("Iapetus", "Iapetus - clear"), ("Algieba", "Algieba - smooth"), ("Algenib", "Algenib - gravelly"),
          ("Rasalgethi", "Rasalgethi - informative"), ("Sadaltager", "Sadaltager - knowledgeable"),
          ("Schedar", "Schedar - even"), ("Umbriel", "Umbriel - easy-going"), ("Achird", "Achird - friendly"),
          ("Fenrir", "Fenrir - excitable"), ("Puck", "Puck - upbeat"),
          ("Kore", "Kore - firm (female)"), ("Aoede", "Aoede - breezy (female)"), ("Leda", "Leda - youthful (female)"),
          ("Zephyr", "Zephyr - bright (female)"), ("Sulafat", "Sulafat - warm (female)")]
SECTIONS = [("last_session", "What you were doing last time"), ("weather", "Weather"), ("calendar", "Calendar"),
            ("news", "News headlines"), ("tasks", "To-do list and reminders"), ("email", "New email"),
            ("school", "Son's schoolwork (Classroom)"), ("system", "PC health")]
NEWS_CATS = [(c, "US" if c == "us" else c.capitalize()) for c in ("top", "world", "us", "business", "tech",
                                                                  "science", "sports", "entertainment")]

STYLE = f"""
QWidget {{ background: transparent; color: {C.WHITE}; font-family: 'Segoe UI'; font-size: 10pt; }}
QWidget#settingsRoot {{ background: {C.BG}; }}
QToolTip {{ background: {C.DARK}; color: {C.WHITE}; border: 1px solid {C.BORDER_B}; }}
QLabel#h1 {{ color: {C.PRI}; font-family: 'Courier New'; font-size: 15pt; font-weight: bold; }}
QLabel#h2 {{ color: {C.PRI}; font-family: 'Courier New'; font-size: 11pt; font-weight: bold; }}
QLabel#help {{ color: {C.TEXT_MED}; font-size: 9pt; }}
QLabel#ok {{ color: {C.GREEN}; }} QLabel#bad {{ color: {C.RED}; }} QLabel#warn {{ color: {C.ACC2}; }}
QFrame#card {{ background: {C.PANEL}; border: 1px solid {C.BORDER}; border-radius: 6px; }}
QFrame#welcome {{ background: #021a24; border: 1px solid {C.PRI_DIM}; border-radius: 6px; }}
QLineEdit, QComboBox, QSpinBox, QPlainTextEdit {{ background: {C.DARK}; border: 1px solid {C.BORDER_B};
    border-radius: 4px; padding: 5px 7px; color: {C.WHITE}; selection-background-color: {C.PRI_DIM}; }}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QPlainTextEdit:focus {{ border: 1px solid {C.PRI}; }}
QComboBox QAbstractItemView {{ background: {C.DARK}; border: 1px solid {C.BORDER_B}; selection-background-color: {C.PRI_DIM}; }}
QPushButton {{ background: transparent; color: {C.PRI}; border: 1px solid {C.PRI_DIM}; border-radius: 4px;
    padding: 6px 14px; font-weight: bold; }}
QPushButton:hover {{ background: {C.PRI_GHO}; border-color: {C.PRI}; }}
QPushButton:disabled {{ color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
QPushButton#primary {{ background: {C.PRI_DIM}; color: #001018; border-color: {C.PRI}; }}
QPushButton#primary:hover {{ background: {C.PRI}; }}
QPushButton#primary:disabled {{ background: transparent; color: {C.TEXT_DIM}; border-color: {C.BORDER}; }}
QPushButton#danger {{ color: {C.RED}; border-color: #662233; }}
QCheckBox {{ spacing: 8px; }}
QCheckBox::indicator {{ width: 16px; height: 16px; border: 1px solid {C.BORDER_B}; border-radius: 3px; background: {C.DARK}; }}
QCheckBox::indicator:checked {{ background: {C.PRI}; border-color: {C.PRI}; }}
QListWidget {{ background: {C.PANEL}; border: none; border-right: 1px solid {C.BORDER}; outline: 0; padding-top: 8px; }}
QListWidget::item {{ padding: 9px 14px; color: {C.TEXT_MED}; border-left: 3px solid transparent; }}
QListWidget::item:selected {{ background: {C.PRI_GHO}; color: {C.PRI}; border-left: 3px solid {C.PRI}; }}
QListWidget::item:hover {{ color: {C.TEXT}; }}
QScrollArea {{ border: none; }}
QScrollBar:vertical {{ background: {C.BG}; width: 10px; }}
QScrollBar::handle:vertical {{ background: {C.BORDER_B}; border-radius: 4px; min-height: 30px; }}
QScrollBar::add-line, QScrollBar::sub-line {{ height: 0; }}
QSlider::groove:horizontal {{ height: 4px; background: {C.BORDER}; border-radius: 2px; }}
QSlider::handle:horizontal {{ background: {C.PRI}; width: 14px; margin: -6px 0; border-radius: 7px; }}
QFrame#bar {{ background: #1a1200; border-top: 1px solid {C.ACC2}; }}
"""


def _label(text: str, name: str = "", wrap: bool = True) -> QLabel:
    lb = QLabel(text)
    lb.setWordWrap(wrap)
    lb.setTextFormat(Qt.TextFormat.RichText)
    lb.setOpenExternalLinks(False)
    if name:
        lb.setObjectName(name)
    return lb


def _devices(kind: str) -> list[tuple[str, str]]:
    """Mics or speakers as (name Jarvis matches on, full name to show) - one entry per device.

    The names come from Windows' default audio system (the one Jarvis opens), which cuts them at 31
    characters; the full name is borrowed from another audio system's list when there is one."""
    try:
        import sounddevice as sd
        api = sd.query_devices(kind=kind)["hostapi"]
        everything = sd.query_devices()
        out: list[tuple[str, str]] = []
        for d in everything:
            ch = d["max_input_channels"] if kind == "input" else d["max_output_channels"]
            name = d["name"]
            if ch <= 0 or d["hostapi"] != api or any(n == name for n, _ in out) or \
                    re.search(r"sound mapper|primary sound", name, re.I):
                continue
            full = next((o["name"] for o in everything
                         if len(o["name"]) > len(name) and o["name"].startswith(name.rstrip())), name)
            out.append((name, full.strip()))
        return out
    except Exception:
        return []


def _open_link(url: str) -> None:
    from ..tools.apps import open_url
    open_url(url)


class SettingsWindow(QWidget):
    _gui = pyqtSignal(object)  # run a function on the GUI thread (results from background work)

    def __init__(self, jarvis: "Jarvis | None" = None, page: str = "", on_restart: Callable[[], None] | None = None):
        super().__init__(None, Qt.WindowType.Window)
        self.jarvis = jarvis
        self.on_restart = on_restart
        self.settings = get_settings()
        self._gui.connect(lambda fn: fn())
        self.setWindowTitle("J.A.R.V.I.S. - Settings")
        if (PROJECT_DIR / "jarvis.ico").exists():
            self.setWindowIcon(QIcon(str(PROJECT_DIR / "jarvis.ico")))
        self.setObjectName("settingsRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet(STYLE)
        self.resize(1000, 720)
        self.setMinimumSize(820, 560)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        body = QHBoxLayout()
        body.setSpacing(0)
        self.nav = QListWidget()
        self.nav.setFixedWidth(210)
        self.stack = QStackedWidget()
        body.addWidget(self.nav)
        body.addWidget(self.stack, 1)
        root.addLayout(body, 1)
        root.addWidget(self._restart_bar())

        self._pages: dict[str, int] = {}
        for key, title, build in [
            ("start", "🔑  Getting started", self._page_start),
            ("you", "👤  You", self._page_you),
            ("voice", "🎙  Voice & microphone", self._page_voice),
            ("google", "📅  Google", self._page_google),
            ("briefing", "📰  Briefing & news", self._page_briefing),
            ("display", "🖥  Pop-up & display", self._page_display),
            ("apps", "🚀  Apps & browser", self._page_apps),
            ("safety", "🛡  Safety", self._page_safety),
            ("personality", "🎭  Personality", self._page_personality),
            ("offline", "💾  Offline mode", self._page_offline),
            ("system", "⚙  Start-up & system", self._page_system),
        ]:
            self._pages[key] = self.stack.count()
            self.stack.addWidget(self._scroll(build()))
            self.nav.addItem(QListWidgetItem(title))
        self.nav.currentRowChanged.connect(self.stack.setCurrentIndex)
        self.show_page(page or ("start" if self.needs_key() else "you"))

    # ---- navigation & saving ------------------------------------------------------------------
    def show_page(self, page: str) -> None:
        self.nav.setCurrentRow(self._pages.get(page, 0))

    @staticmethod
    def needs_key() -> bool:
        return str(get_settings().get("brain.provider", "gemini_live")) in ("gemini_live", "gemini") and \
            not secret("GEMINI_API_KEY")

    def _later(self, fn: Callable[[], Any]) -> None:
        self._gui.emit(fn)

    def _bg(self, work: Callable[[], Any], done: Callable[[Any], None]) -> None:
        """Run `work` off the GUI thread; `done(result or exception)` back on it."""
        def run():
            try:
                result = work()
            except Exception as e:
                result = e
            self._later(lambda: done(result))
        threading.Thread(target=run, daemon=True).start()

    def _save(self, key: str, value: Any, live: bool = False) -> None:
        save_user_setting(key, value)
        if not live:
            self._needs_restart()

    def _needs_restart(self, text: str = "Saved. Restart Jarvis to use the new settings.") -> None:
        self._bar_text.setText(text)
        self._bar.show()

    def _restart_bar(self) -> QFrame:
        self._bar = QFrame()
        self._bar.setObjectName("bar")
        lay = QHBoxLayout(self._bar)
        lay.setContentsMargins(18, 10, 18, 10)
        self._bar_text = QLabel("")
        self._bar_text.setStyleSheet(f"color: {C.ACC2};")
        lay.addWidget(self._bar_text, 1)
        btn = QPushButton("Restart Jarvis now" if self.on_restart else "Start Jarvis")
        btn.setObjectName("primary")
        btn.clicked.connect(self._restart)
        lay.addWidget(btn)
        self._bar.hide()
        return self._bar

    def _restart(self) -> None:
        if self.on_restart:
            self.on_restart()
            return
        from .. import shortcuts
        if shortcuts.is_running():
            self._bar_text.setText("Jarvis is running - close his window and start him again to apply the changes.")
        else:
            shortcuts.launch()
            self.close()

    # ---- building blocks ----------------------------------------------------------------------
    def _scroll(self, page: QWidget) -> QScrollArea:
        sc = QScrollArea()
        sc.setWidgetResizable(True)
        sc.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        sc.setWidget(page)
        return sc

    def _page(self, title: str, intro: str = "") -> tuple[QWidget, QVBoxLayout]:
        w = QWidget()
        lay = QVBoxLayout(w)
        lay.setContentsMargins(28, 22, 28, 22)
        lay.setSpacing(14)
        lay.addWidget(_label(title, "h1"))
        if intro:
            lay.addWidget(_label(intro, "help"))
        return w, lay

    def _card(self, parent: QVBoxLayout, title: str = "", intro: str = "") -> QVBoxLayout:
        card = QFrame()
        card.setObjectName("card")
        lay = QVBoxLayout(card)
        lay.setContentsMargins(18, 14, 18, 16)
        lay.setSpacing(10)
        if title:
            lay.addWidget(_label(title, "h2"))
        if intro:
            lay.addWidget(_label(intro, "help"))
        parent.addWidget(card)
        return lay

    def _row(self, lay: QVBoxLayout, label: str, control: QWidget, help: str = "") -> None:
        box = QVBoxLayout()
        box.setSpacing(3)
        line = QHBoxLayout()
        lb = QLabel(label)
        lb.setMinimumWidth(230)
        line.addWidget(lb)
        line.addWidget(control, 1)
        box.addLayout(line)
        if help:
            h = _label(help, "help")
            h.setContentsMargins(0, 0, 0, 0)
            box.addWidget(h)
        lay.addLayout(box)

    def _toggle(self, lay, key: str, label: str, help: str = "", default: bool = True, live: bool = False) -> QCheckBox:
        cb = QCheckBox(label)
        cb.setChecked(bool(self.settings.get(key, default)))
        cb.toggled.connect(lambda v: self._save(key, bool(v), live))
        lay.addWidget(cb)
        if help:
            h = _label(help, "help")
            h.setContentsMargins(26, 0, 0, 0)
            lay.addWidget(h)
        return cb

    def _text(self, lay, key: str, label: str, help: str = "", placeholder: str = "", live: bool = False,
              check: Callable[[str], str | None] | None = None) -> QLineEdit:
        ed = QLineEdit(str(self.settings.get(key, "") or ""))
        ed.setPlaceholderText(placeholder)

        def commit():
            value = ed.text().strip()
            problem = check(value) if check else None
            if problem:
                ed.setStyleSheet(f"border: 1px solid {C.RED};")
                ed.setToolTip(problem)
                return
            ed.setStyleSheet("")
            if value != str(self.settings.get(key, "") or ""):
                self._save(key, value, live)
        ed.editingFinished.connect(commit)
        self._row(lay, label, ed, help)
        return ed

    def _choice(self, lay, key: str, label: str, options: list[tuple[Any, str]], help: str = "",
                live: bool = False, editable: bool = False) -> QComboBox:
        cb = QComboBox()
        cb.setEditable(editable)
        current = self.settings.get(key, options[0][0] if options else "")
        for value, text in options:
            cb.addItem(text, value)
        idx = cb.findData(current)
        if idx < 0 and current not in (None, ""):
            cb.addItem(f"{current}", current)
            idx = cb.count() - 1
        cb.setCurrentIndex(max(0, idx))
        if editable:
            def typed():
                text = cb.currentText().strip()
                idx = cb.findText(text)
                value = cb.itemData(idx) if idx >= 0 else text
                if text and value != self.settings.get(key):
                    self._save(key, value, live)
            cb.lineEdit().editingFinished.connect(typed)
        cb.currentIndexChanged.connect(lambda _i: self._save(key, cb.currentData(), live))
        self._row(lay, label, cb, help)
        return cb

    def _number(self, lay, key: str, label: str, lo: int, hi: int, help: str = "", suffix: str = "",
                default: int = 0, live: bool = False) -> QSpinBox:
        sp = QSpinBox()
        sp.setRange(lo, hi)
        sp.setSuffix(suffix)
        sp.setValue(int(self.settings.get(key, default) or default))
        sp.valueChanged.connect(lambda v: self._save(key, int(v), live))
        sp.setFixedWidth(130)
        self._row(lay, label, sp, help)
        return sp

    def _multi(self, lay, key: str, label: str, options: list[tuple[str, str]], help: str = "") -> None:
        lay.addWidget(QLabel(label))
        chosen = list(self.settings.get(key, []) or [])
        grid = QGridLayout()
        boxes: list[tuple[str, QCheckBox]] = []

        def changed():
            self._save(key, [v for v, b in boxes if b.isChecked()])
        for i, (value, text) in enumerate(options):
            b = QCheckBox(text)
            b.setChecked(value in chosen)
            b.toggled.connect(lambda _v: changed())
            boxes.append((value, b))
            grid.addWidget(b, i // 2, i % 2)
        lay.addLayout(grid)
        if help:
            lay.addWidget(_label(help, "help"))

    # ---- pages ----------------------------------------------------------------------------------
    def _page_start(self) -> QWidget:
        w, lay = self._page("Getting started")
        if self.needs_key():
            box = QFrame()
            box.setObjectName("welcome")
            bl = QVBoxLayout(box)
            bl.setContentsMargins(20, 16, 20, 18)
            bl.addWidget(_label("Welcome. One thing to set up, about two minutes.", "h2"))
            bl.addWidget(_label(
                "Jarvis talks through Google's Gemini, which needs a free <b>key</b> - think of it as his "
                "password to Google's voice service. Here's how to get one:<br><br>"
                "<b>1.</b> Click <b>Get my free key</b> below. Google AI Studio opens in your browser - sign in "
                "with any Google account.<br>"
                "<b>2.</b> Click <b>Create API key</b>. If it asks for a project, pick any (or let it make one).<br>"
                "<b>3.</b> Click the <b>copy</b> icon next to the new key (it starts with <i>AIza</i> or <i>AQ.</i>).<br>"
                "<b>4.</b> Come back here, click <b>Paste</b>, then <b>Save &amp; test</b>. Jarvis restarts with his "
                "voice."))
            lay.addWidget(box)
        card = self._card(lay, "Gemini key (his voice)",
                          "Free. Stored only on this PC, in the .env file - never uploaded anywhere but Google.")
        line = QHBoxLayout()
        self._key = QLineEdit(secret("GEMINI_API_KEY"))
        self._key.setEchoMode(QLineEdit.EchoMode.Password)
        self._key.setPlaceholderText("Paste your key here")
        line.addWidget(self._key, 1)
        show = QPushButton("👁")
        show.setFixedWidth(44)
        show.setToolTip("Show / hide")
        show.clicked.connect(lambda: self._key.setEchoMode(
            QLineEdit.EchoMode.Normal if self._key.echoMode() == QLineEdit.EchoMode.Password
            else QLineEdit.EchoMode.Password))
        line.addWidget(show)
        paste = QPushButton("Paste")
        paste.clicked.connect(lambda: self._key.setText(QApplication.clipboard().text().strip()))
        line.addWidget(paste)
        card.addLayout(line)
        btns = QHBoxLayout()
        get = QPushButton("Get my free key  ↗")
        get.clicked.connect(lambda: _open_link(GEMINI_KEY_URL))
        test = QPushButton("Save && test")
        test.setObjectName("primary")
        test.clicked.connect(self._save_gemini)
        btns.addWidget(get)
        btns.addWidget(test)
        btns.addStretch()
        card.addLayout(btns)
        self._key_status = _label("", "help")
        card.addWidget(self._key_status)
        if secret("GEMINI_API_KEY"):
            self._key_status.setText("A key is saved.")

        other = self._card(lay, "Other services (optional)",
                           "Only needed if you switch to these. Each saves as soon as you press Save.")
        for name, label, hide, help in [
            ("ANTHROPIC_API_KEY", "Claude API key", True, "For Offline mode → brain: Claude (paid)."),
            ("ELEVENLABS_API_KEY", "ElevenLabs key", True, "A premium voice for offline mode."),
            ("ELEVENLABS_VOICE_ID", "ElevenLabs voice ID", False, ""),
            ("CALENDAR_ICS_URL", "Calendar iCal link", True, "Read-only calendar if you don't connect Google."),
            ("IMAP_USER", "Email address (IMAP)", False, "Read-only email if you don't connect Google."),
            ("IMAP_PASSWORD", "Email app password", True, ""),
        ]:
            self._secret_row(other, name, label, hide, help)
        lay.addStretch()
        return w

    def _secret_row(self, lay, name: str, label: str, hide: bool, help: str) -> None:
        row = QHBoxLayout()
        ed = QLineEdit(secret(name))
        if hide:
            ed.setEchoMode(QLineEdit.EchoMode.Password)
        btn = QPushButton("Save")
        btn.setFixedWidth(80)

        def save():
            set_secret(name, ed.text())
            btn.setText("Saved ✓")
            QTimer.singleShot(1500, lambda: btn.setText("Save"))
            self._needs_restart()
        btn.clicked.connect(save)
        row.addWidget(ed, 1)
        row.addWidget(btn)
        holder = QWidget()
        holder.setLayout(row)
        row.setContentsMargins(0, 0, 0, 0)
        self._row(lay, label, holder, help)

    def _save_gemini(self) -> None:
        key = self._key.text().strip()
        if not key:
            self._key_status.setText("Paste the key first.")
            self._key_status.setObjectName("bad")
            self._restyle(self._key_status)
            return
        self._key_status.setObjectName("help")
        self._key_status.setText("Checking the key with Google…")
        self._restyle(self._key_status)

        def check():
            from google import genai
            client = genai.Client(api_key=key)
            next(iter(client.models.list(config={"page_size": 1})))
            return True

        def done(result):
            if result is True:
                set_secret("GEMINI_API_KEY", key)
                self._key_status.setObjectName("ok")
                if self.on_restart and (self.jarvis is None or not self.jarvis.use_live):
                    self._key_status.setText("✓ The key works. Restarting Jarvis with his voice…")
                    QTimer.singleShot(1800, self._restart)
                else:
                    self._key_status.setText("✓ The key works and is saved.")
                    self._needs_restart("Key saved. Restart Jarvis to use it." if self.on_restart
                                        else "Key saved. Start Jarvis to hear him.")
            else:
                text = str(result)
                self._key_status.setObjectName("bad")
                if "API_KEY_INVALID" in text or "API key not valid" in text or "400" in text[:20]:
                    self._key_status.setText("✗ Google says that key isn't valid. Copy it again - all of it - and "
                                             "paste it here.")
                elif "getaddrinfo" in text or "ConnectError" in text or "timed out" in text:
                    self._key_status.setText("✗ Couldn't reach Google - check the internet connection.")
                else:
                    self._key_status.setText(f"✗ That didn't work: {text[:160]}")
            self._restyle(self._key_status)
        self._bg(check, done)

    @staticmethod
    def _restyle(widget: QWidget) -> None:
        widget.style().unpolish(widget)
        widget.style().polish(widget)

    def _page_you(self) -> QWidget:
        w, lay = self._page("You", "How Jarvis addresses you, and where you are (for the weather).")
        c = self._card(lay)
        self._text(c, "user.name", "Your first name", "Optional. He mostly calls you by your title.")
        self._choice(c, "user.title", "He calls you", [("sir", "sir"), ("ma'am", "ma'am"), ("boss", "boss"),
                                                       ("miss", "miss")], editable=True,
                     help="Pick one or type your own.")
        self._text(c, "user.location", "Your city", 'For the weather, e.g. "Austin, TX". Type auto to work it '
                   "out from your internet connection.", placeholder="auto")
        ed = QLineEdit(", ".join(str(i) for i in (self.settings.get("user.interests") or [])))
        ed.setPlaceholderText("e.g. space, Formula 1, cooking, AI, history")

        def save_interests():
            items = [t.strip() for t in re.split(r"[,;\n]", ed.text()) if t.strip()]
            if items != list(self.settings.get("user.interests") or []):
                self._save("user.interests", items, live=True)  # used from the next start-up
        ed.editingFinished.connect(save_interests)
        self._row(c, "Things you're into", ed, "Separate with commas. His start-up article rotates through these - "
                  "games are just one of many.")
        self._choice(c, "user.units", "Units", [("auto", "Automatic (imperial in the US)"),
                                                ("imperial", "Imperial - °F, miles"), ("metric", "Metric - °C, km")])
        self._text(c, "assistant.name", "Assistant's name", "Shown in the window and used in his personality.")
        lay.addStretch()
        return w

    def _page_voice(self) -> QWidget:
        w, lay = self._page("Voice & microphone")
        c = self._card(lay, "His voice")
        self._choice(c, "live.voice", "Voice", VOICES,
                     help="Every voice speaks with the British accent his personality asks for.")
        self._choice(c, "live.barge_in", "Talk over him to interrupt",
                     [("auto", "Only with headphones (speakers would hear him and cut him off)"),
                      ("true", "Always"), ("false", "Never")])
        self._toggle(c, "voice.acknowledge", 'Say "One moment, sir" before slow actions')

        d = self._card(lay, "Microphone & speakers",
                       "Pick the devices by name. Windows default follows whatever is set in Windows' sound settings.")
        mics = [("", "Windows default")] + _devices("input")
        outs = [("", "Windows default")] + _devices("output")
        self._choice(d, "voice.input_device", "Microphone", mics)
        self._choice(d, "voice.output_device", "Speakers / headphones", outs)

        lst = self._card(lay, "Listening")
        self._toggle(lst, "voice.require_name", 'Only respond when I say "Jarvis"',
                     "Recommended. Otherwise anything said near the mic counts as a command.")
        self._number(lst, "voice.conversation_seconds", "Keep listening after he answers", 0, 120, suffix=" s",
                     default=20, help="Time to ask a follow-up without saying his name again.")
        lay.addStretch()
        return w

    # ---- Google ---------------------------------------------------------------------------------
    def _page_google(self) -> QWidget:
        w, lay = self._page("Google", "Calendar, Gmail, Contacts, Drive and your son's Google Classroom.")
        self._g_status = self._card(lay, "Status")
        self._g_status_lbl = _label("")
        self._g_status.addWidget(self._g_status_lbl)
        row = QHBoxLayout()
        self._g_main = QPushButton("Sign in with Google")
        self._g_main.setObjectName("primary")
        self._g_main.clicked.connect(lambda: self._google_sign_in("main"))
        self._g_kid = QPushButton("Connect son's Classroom")
        self._g_kid.clicked.connect(lambda: self._google_sign_in("classroom"))
        self._g_test = QPushButton("Check connection")
        self._g_test.clicked.connect(self._google_check)
        self._g_fix = QPushButton("Turn on missing services ↗")
        self._g_fix.clicked.connect(self._google_fix)
        self._g_fix.hide()
        for b in (self._g_main, self._g_kid, self._g_test, self._g_fix):
            row.addWidget(b)
        row.addStretch()
        self._g_status.addLayout(row)
        row2 = QHBoxLayout()
        self._g_disc = {}
        for account, text in (("main", "Disconnect my account"), ("classroom", "Disconnect Classroom")):
            b = QPushButton(text)
            b.setObjectName("danger")
            b.clicked.connect(lambda _=False, a=account: self._google_disconnect(a))
            row2.addWidget(b)
            self._g_disc[account] = b
        row2.addStretch()
        self._g_status.addLayout(row2)
        self._g_msg = _label("", "help")
        self._g_msg.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self._g_status.addWidget(self._g_msg)

        from ..google_setup import STEPS
        self._g_setup = self._card(lay, "One-time setup: make Jarvis's Google key",
                                   "Google only lets an app read your calendar or email with a key that belongs to "
                                   "someone. Jarvis isn't a published app, so you make your own - free, private, "
                                   "about five minutes, only once. Do the steps in order; each button opens the "
                                   "right page in your browser.")
        for i, (title, url, lines) in enumerate(STEPS, 1):
            step = QHBoxLayout()
            text = f"<b>Step {i}: {title}</b><br>" + "<br>".join(f"• {ln.strip()}" for ln in lines)
            lb = _label(text)
            step.addWidget(lb, 1)
            b = QPushButton("Open page ↗")
            b.setFixedWidth(130)
            b.clicked.connect(lambda _=False, u=url: _open_link(u))
            step.addWidget(b, 0, Qt.AlignmentFlag.AlignTop)
            self._g_setup.addLayout(step)
        key_row = QHBoxLayout()
        self._g_key_lbl = _label("", "help")
        key_row.addWidget(self._g_key_lbl, 1)
        pick = QPushButton("Choose the file…")
        pick.clicked.connect(self._google_pick_file)
        key_row.addWidget(pick)
        self._g_setup.addLayout(key_row)
        lay.addWidget(_label(
            "<b>About your son's school account:</b> schools often block apps their IT admin hasn't approved, "
            "especially for students under 18. The block page usually has a 'Request access' button. Until "
            "then, Jarvis reads the Classroom guardian summary emails in your Gmail instead - ask the teacher to "
            "add you as a guardian if you don't get them.", "help"))
        lay.addStretch()
        self._watch_since = time.time() - 3600
        self._g_timer = QTimer(self)
        self._g_timer.timeout.connect(self._google_refresh)
        self._g_timer.start(1500)
        self._google_refresh()
        return w

    def _google_refresh(self) -> None:
        from .. import google_auth as ga
        if not ga.has_client():
            found = ga.find_downloaded_client(self._watch_since)
            if found:
                try:
                    ga.import_client(found)
                    self._g_msg.setText("✓ Found your key file in Downloads and saved it. Now click 'Sign in with "
                                        "Google'.")
                except Exception as e:
                    self._g_msg.setText(str(e))
        has = ga.has_client()
        main, kid = ga.connected("main"), ga.connected("classroom")
        lines = [
            ("✓ " if has else "○ ") + ("Jarvis's Google key is set up." if has else
                                       "Jarvis's Google key isn't set up yet - do the one-time setup below."),
            ("✓ Your account: " + (ga.email_of("main") or "connected")) if main else "○ Your account: not connected",
            ("✓ Son's Classroom: " + (ga.email_of("classroom") or "connected")) if kid else
            "○ Son's Classroom: not connected (optional)",
        ]
        self._g_status_lbl.setText("<br>".join(lines))
        if not getattr(self, "_signing_in", False):
            self._g_main.setEnabled(has)
            self._g_kid.setEnabled(has)
            self._g_main.setText("Sign in again" if main else "Sign in with Google")
        self._g_test.setEnabled(main)
        self._g_disc["main"].setVisible(main)
        self._g_disc["classroom"].setVisible(kid)
        self._g_setup.parentWidget().setVisible(not has or not main)
        self._g_key_lbl.setText("✓ Key file saved." if has else
                                "Waiting for the downloaded file (client_secret_….json) - Jarvis picks it up from "
                                "Downloads by itself.")

    def _google_pick_file(self) -> None:
        from .. import google_auth as ga
        path, _ = QFileDialog.getOpenFileName(self, "Choose the key file from Google", str(ga.download_dirs()[0]
                                              if ga.download_dirs() else ""), "Google key (*.json)")
        if path:
            try:
                ga.import_client(path)
                self._g_msg.setText("✓ Key saved. Now click 'Sign in with Google'.")
            except Exception as e:
                self._g_msg.setText(f"✗ {e}")
            self._google_refresh()

    def _google_sign_in(self, account: str) -> None:
        from .. import google_auth as ga
        self._signing_in = True
        for b in (self._g_main, self._g_kid):
            b.setEnabled(False)
        who = "YOUR account" if account == "main" else "your SON'S school account ('Use another account' if it isn't listed)"
        self._g_msg.setText(
            f"A Google page is opening in your browser:<br>"
            f"<b>1.</b> Choose {who}.<br>"
            "<b>2.</b> 'Google hasn't verified this app' is expected - it's your own app. Click <b>Continue</b> "
            "(or <b>Advanced → Go to Jarvis</b>).<br>"
            "<b>3.</b> Tick <b>Select all</b>, then <b>Continue</b>. Close the tab when it says Jarvis is connected.")

        def show_url(url: str) -> None:
            self._later(lambda: self._g_msg.setText(
                self._g_msg.text() + "<br><br>No page opened? Copy this link into your browser:<br>"
                f"<span style='color:{C.TEXT}'>{url}</span>"))

        def done(result):
            self._signing_in = False
            if isinstance(result, Exception):
                text = str(result)
                msg = f"✗ Sign-in didn't finish: {text[:200]}"
                if "access_denied" in text or "admin" in text.lower() or "policy" in text.lower():
                    msg += ("<br>If this was the school account: the school blocks unapproved apps - see the note "
                            "at the bottom.")
                if "timed out" in text.lower() or "timeout" in text.lower():
                    msg = "Sign-in timed out (5 minutes). Click the button to try again."
                self._g_msg.setText(msg)
            else:
                email, missing = result
                msg = f"✓ Connected: {email}."
                if missing:
                    msg += f" Some boxes were unticked ({', '.join(missing)}) - sign in again and tick Select all."
                self._g_msg.setText(msg + " Restart Jarvis to use it.")
                self._needs_restart()
            self._google_refresh()
        self._bg(lambda: ga.sign_in(account, timeout=300, on_url=show_url), done)

    def _google_check(self) -> None:
        self._g_msg.setText("Checking…")

        def work():
            from ..tools import gservices
            return gservices.run_health_checks()

        def done(results):
            if isinstance(results, Exception):
                self._g_msg.setText(f"✗ {results}")
                return
            self._g_msg.setText("<br>".join(f"{'✓' if ok else '✗'} {label}: {msg}" for label, ok, msg in results))
            self._g_fix.setVisible(any(not ok and "switched off" in msg for _, ok, msg in results))
        self._bg(work, done)

    def _google_fix(self) -> None:
        from ..tools.gservices import ENABLE_APIS_URL
        _open_link(ENABLE_APIS_URL)
        self._g_msg.setText("In the page that opened: check the project at the top says Jarvis, click NEXT, then "
                            "ENABLE. Wait a minute, then click 'Check connection' again.")

    def _google_disconnect(self, account: str) -> None:
        from .. import google_auth as ga
        ga.disconnect(account)
        self._g_msg.setText("Disconnected. (To remove Jarvis from your Google account entirely, visit "
                            "myaccount.google.com/permissions.)")
        self._google_refresh()
        self._needs_restart()

    # ---- the rest -------------------------------------------------------------------------------
    def _page_briefing(self) -> QWidget:
        w, lay = self._page("Briefing & news")
        c = self._card(lay, "When Jarvis starts")
        self._choice(c, "briefing.on_first_launch", "First start of the day",
                     [("auto", "Give the full daily briefing"), ("offer", "Offer it - ask first"),
                      ("off", "Nothing")])
        self._toggle(c, "briefing.news_on_launch", "Later starts: a quick news rundown")
        self._toggle(c, "briefing.open_article_on_launch", "Then open one article he thinks you'd like")
        self._text(c, "briefing.time", "Also brief me every day at", "24-hour time like 07:30, while Jarvis is "
                   "running. Leave empty for never.", placeholder="e.g. 07:30",
                   check=lambda v: None if not v or re.fullmatch(r"([01]?\d|2[0-3]):[0-5]\d", v)
                   else "Use a time like 07:30")
        s = self._card(lay, "What the briefing covers")
        self._multi(s, "briefing.sections", "Sections", SECTIONS)
        n = self._card(lay, "News")
        self._multi(n, "briefing.news_categories", "Headline categories", NEWS_CATS)
        self._number(n, "briefing.headlines_per_category", "Headlines per category", 1, 6, default=2)
        self._number(n, "monitor.check_every_hours", "Check watched topics every", 1, 24, suffix=" h", default=4,
                     help='Topics you ask him to "keep an eye on".')
        lay.addStretch()
        return w

    def _page_display(self) -> QWidget:
        w, lay = self._page("Pop-up & display")
        c = self._card(lay, "Top-of-screen pop-up", "The bar that slides down from the top while he talks.")
        self._toggle(c, "hud.popup", "Show the pop-up")
        val = int(self.settings.get("hud.caption_delay_ms", -250) or 0)
        line = QHBoxLayout()
        sl = QSlider(Qt.Orientation.Horizontal)
        sl.setRange(-800, 800)
        sl.setSingleStep(50)
        sl.setPageStep(100)
        sl.setValue(val)
        lbl = QLabel()

        def show(v):
            lbl.setText("on time" if v == 0 else f"{abs(v)} ms {'earlier' if v < 0 else 'later'}")
        show(val)
        sl.valueChanged.connect(show)
        sl.sliderReleased.connect(lambda: self._save("hud.caption_delay_ms", int(sl.value()), live=True))
        sl.valueChanged.connect(lambda v: None if sl.isSliderDown() else
                                self._save("hud.caption_delay_ms", int(v), live=True))
        line.addWidget(QLabel("earlier"))
        line.addWidget(sl, 1)
        line.addWidget(QLabel("later"))
        lbl.setMinimumWidth(110)
        lbl.setStyleSheet(f"color: {C.PRI};")
        line.addWidget(lbl)
        holder = QWidget()
        holder.setLayout(line)
        line.setContentsMargins(0, 0, 0, 0)
        self._row(c, "Caption timing", holder, "Words ahead of his voice? Slide toward later. Behind? Toward earlier. "
                  "Takes effect immediately.")
        from .dashboard import DEFAULT_APPS, DEFAULT_LINKS
        q = self._card(lay, "HUD shortcuts", "The LAUNCH tabs and the site links beside the suit. Changes show next "
                                             "time Jarvis starts.")
        apps_ed = QLineEdit(", ".join(self.settings.get("hud.quick_apps") or DEFAULT_APPS))

        def save_apps():
            items = [a.strip() for a in apps_ed.text().split(",") if a.strip()]
            self._save("hud.quick_apps", items)
        apps_ed.editingFinished.connect(save_apps)
        self._row(q, "Launch tabs (apps)", apps_ed, "App names as in the Start menu, separated by commas. Apps you "
                  "don't have are skipped.")
        links_ed = QPlainTextEdit("\n".join(f"{n} | {u}" for n, u in (self.settings.get("hud.links") or DEFAULT_LINKS)))
        links_ed.setFixedHeight(150)
        links_ed.setFont(QFont("Consolas", 9))
        links_btn = QPushButton("Save links")

        def save_links():
            items = []
            for line in links_ed.toPlainText().splitlines():
                if "|" in line:
                    name, url = (x.strip() for x in line.split("|", 1))
                    if name and url.startswith(("http://", "https://")):
                        items.append([name, url])
            self._save("hud.links", items)
            links_btn.setText("Saved ✓")
            QTimer.singleShot(1500, lambda: links_btn.setText("Save links"))
        links_btn.clicked.connect(save_links)
        q.addWidget(QLabel("Site links - one per line, as  Name | https://address"))
        q.addWidget(links_ed)
        q.addWidget(links_btn, 0, Qt.AlignmentFlag.AlignLeft)
        h = self._card(lay, "Main window")
        self._choice(h, "hud.mode", "Jarvis's window", [("desktop", "Desktop window (recommended)"),
                                                         ("browser", "Web page in the browser"),
                                                         ("off", "None - console only")])
        lay.addStretch()
        return w

    def _page_apps(self) -> QWidget:
        w, lay = self._page("Apps & browser")
        c = self._card(lay)
        self._choice(c, "apps.browser", "Open links, articles and trailers in",
                     [("chrome", "Google Chrome"), ("default", "My default browser")])
        self._toggle(c, "apps.game_news_on_launch", "When I start a game, tell me its latest update")
        lay.addStretch()
        return w

    def _page_safety(self) -> QWidget:
        w, lay = self._page("Safety", "What Jarvis must ask you about before doing it. Sending email and "
                            "deleting calendar events always ask.")
        c = self._card(lay)
        self._choice(c, "safety.confirm_commands", "Running PowerShell commands",
                     [("risky", "Ask unless it only reads information (recommended)"), ("always", "Always ask"),
                      ("never", "Never ask")])
        self._toggle(c, "safety.confirm_overwrite", "Ask before overwriting a file")
        self._toggle(c, "safety.confirm_power", "Ask before sleep, restart or shutdown")
        p = self._card(lay, "Heads-up alerts")
        self._toggle(p, "proactive.enabled", "Warn me about low disk space and low battery")
        self._toggle(p, "proactive.strain_alerts", "Tell me when my PC is under strain",
                     "He names what's responsible. In full-screen games he only mentions heat and memory - a "
                     "busy GPU is normal there.")
        self._number(p, "proactive.cpu_high", "Processor busy above", 50, 100, suffix=" %", default=90,
                     help="For over a minute.")
        self._number(p, "proactive.memory_high", "Memory full above", 50, 100, suffix=" %", default=90)
        self._number(p, "proactive.gpu_hot", "Graphics card hot above", 60, 100, suffix=" °C", default=85)
        self._number(p, "proactive.strain_cooldown_minutes", "Don't repeat a warning for", 5, 240, suffix=" min",
                     default=20)
        self._number(p, "proactive.disk_low_gb", "Low disk space below", 1, 500, suffix=" GB", default=15)
        self._number(p, "proactive.battery_low", "Low battery below", 5, 50, suffix=" %", default=20)
        lay.addStretch()
        return w

    def _page_personality(self) -> QWidget:
        w, lay = self._page("Personality",
                            "The instructions that make him Jarvis: manner, rules, habits. Edit freely. Words in "
                            "{{double braces}} are filled in automatically - leave those as they are.")
        path = CONFIG_DIR / "persona.md"
        backup = CONFIG_DIR / "persona.default.md"
        ed = QPlainTextEdit(path.read_text(encoding="utf-8-sig") if path.exists() else "")
        ed.setFont(QFont("Consolas", 10))
        ed.setMinimumHeight(430)
        lay.addWidget(ed, 1)
        row = QHBoxLayout()
        status = _label("", "help")

        def save():
            if not backup.exists() and path.exists():
                backup.write_text(path.read_text(encoding="utf-8-sig"), encoding="utf-8")
            path.write_text(ed.toPlainText(), encoding="utf-8")
            status.setText("Saved.")
            self._needs_restart()

        def restore():
            if backup.exists():
                ed.setPlainText(backup.read_text(encoding="utf-8"))
                save()
                status.setText("Restored the original personality.")
            else:
                status.setText("This is already the original.")
        sv = QPushButton("Save")
        sv.setObjectName("primary")
        sv.clicked.connect(save)
        rs = QPushButton("Restore original")
        rs.clicked.connect(restore)
        row.addWidget(sv)
        row.addWidget(rs)
        row.addWidget(status, 1)
        lay.addLayout(row)
        return w

    def _page_offline(self) -> QWidget:
        w, lay = self._page("Offline mode",
                            "Jarvis normally talks through Gemini (online). Offline mode runs everything on this "
                            "PC instead - slower and less natural, but private and free of limits. It's also his "
                            "backup if Gemini is down or you run out of free use.")
        c = self._card(lay, "Brain")
        self._choice(c, "brain.provider", "Jarvis runs on",
                     [("gemini_live", "Gemini Live - natural voice, fast (recommended)"),
                      ("ollama", "Offline - everything on this PC (needs the downloads below)"),
                      ("anthropic", "Claude API - smart, paid (key on Getting started)")])
        s = self._card(lay, "Offline downloads")
        self._off_status = _label("", "help")
        s.addWidget(self._off_status)
        grid = QGridLayout()
        for i, (text, what, help) in enumerate([
                ("1. Download voice && hearing", "voices", "His offline voice and speech recognition - 850 MB."),
                ("2. Install Ollama", "ollama", "The free app that runs an AI brain on your PC."),
                ("3. Download the brain", "model", "The AI model itself - about 6 GB. Needs a decent graphics card.")]):
            b = QPushButton(text)
            b.setFixedWidth(250)
            b.clicked.connect(lambda _=False, w=what: self._run(w))
            grid.addWidget(b, i, 0)
            grid.addWidget(_label(help, "help"), i, 1)
        grid.setColumnStretch(1, 1)
        s.addLayout(grid)
        self._log = QPlainTextEdit()
        self._log.setReadOnly(True)
        self._log.setFont(QFont("Consolas", 9))
        self._log.setFixedHeight(170)
        self._log.hide()
        s.addWidget(self._log)
        v = self._card(lay, "Offline voice")
        self._choice(v, "tts.kokoro_voice", "Voice", [("bm_george", "George - British male"),
                                                      ("bm_fable", "Fable - British male"),
                                                      ("bm_lewis", "Lewis - British male"),
                                                      ("bm_daniel", "Daniel - British male"),
                                                      ("bf_emma", "Emma - British female")])
        self._text(v, "brain.ollama.model", "Brain model", "Any Ollama model that can use tools.")
        lay.addStretch()
        self._offline_refresh()
        return w

    def _ollama_exe(self) -> str | None:
        import os
        import shutil
        found = shutil.which("ollama")
        if found:
            return found
        guess = os.path.expandvars(r"%LOCALAPPDATA%\Programs\Ollama\ollama.exe")
        return guess if os.path.exists(guess) else None

    def _offline_refresh(self) -> None:
        from ..config import MODELS_DIR
        voices = (MODELS_DIR / "kokoro" / "kokoro-v1.0.onnx").exists()
        ollama = self._ollama_exe()
        lines = [("✓ Offline voice & hearing downloaded" if voices else "○ Offline voice & hearing not downloaded"),
                 ("✓ Ollama installed" if ollama else "○ Ollama not installed")]
        self._off_status.setText("<br>".join(lines))

    def _run(self, what: str) -> None:
        """Run a download/install in the background and show its output here - no console windows."""
        import sys
        if getattr(self, "_proc", None) and self._proc.state() != QProcess.ProcessState.NotRunning:
            return
        model = str(self.settings.get("brain.ollama.model", "qwen3-vl:8b"))
        if what == "voices":
            program, args = sys.executable, ["-m", "jarvis", "--setup"]
        elif what == "ollama":
            program, args = "winget", ["install", "-e", "--id", "Ollama.Ollama", "--accept-package-agreements",
                                       "--accept-source-agreements"]
        else:
            exe = self._ollama_exe()
            if not exe:
                self._log.show()
                self._log.appendPlainText("Install Ollama first.")
                return
            program, args = exe, ["pull", model]
        self._log.show()
        self._log.appendPlainText(f"> {what}…")
        self._proc = QProcess(self)
        env = QProcessEnvironment.systemEnvironment()
        env.insert("PYTHONIOENCODING", "utf-8")
        self._proc.setProcessEnvironment(env)
        self._proc.setWorkingDirectory(str(PROJECT_DIR))
        self._proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)

        def output():
            text = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
            for part in re.split(r"[\r\n]+", text):
                part = re.sub(r"\x1b\[[0-9;]*[A-Za-z]", "", part).strip()
                if part:
                    self._log.appendPlainText(part)

        def finished(code, _status):
            self._log.appendPlainText("Done." if code == 0 else f"Stopped (code {code}).")
            self._offline_refresh()
        self._proc.readyReadStandardOutput.connect(output)
        self._proc.finished.connect(finished)
        self._proc.start(program, args)

    def _page_system(self) -> QWidget:
        from .. import shortcuts
        w, lay = self._page("Start-up & system")
        c = self._card(lay, "Starting Jarvis")
        startup = QCheckBox("Start Jarvis when I sign in to Windows")
        try:
            startup.setChecked(shortcuts.startup_link().exists())
        except Exception:
            pass

        def set_startup(v):
            try:
                shortcuts.set_startup(bool(v))
            except Exception as e:
                startup.setText(f"Start Jarvis when I sign in to Windows  (couldn't change it: {e})")
        startup.toggled.connect(set_startup)
        c.addWidget(startup)
        row = QHBoxLayout()
        desk = QPushButton("Put a Jarvis button on the desktop")
        desk_status = _label("", "help")

        def make_desktop():
            try:
                shortcuts.make_link(shortcuts.desktop_link())
                desk_status.setText("✓ It's on your desktop.")
            except Exception as e:
                desk_status.setText(f"✗ {e}")
        desk.clicked.connect(make_desktop)
        row.addWidget(desk)
        row.addWidget(desk_status, 1)
        c.addLayout(row)

        t = self._card(lay, "Health check", "Tests every part of Jarvis - voice, Google, news, apps - and lists "
                       "anything that needs attention.")
        run = QPushButton("Run the check")
        out = QPlainTextEdit()
        out.setReadOnly(True)
        out.setFont(QFont("Consolas", 9))
        out.setFixedHeight(260)
        out.hide()

        def check():
            import sys
            out.show()
            out.setPlainText("Checking… (about 30 seconds)")
            proc = QProcess(self)
            env = QProcessEnvironment.systemEnvironment()
            env.insert("PYTHONIOENCODING", "utf-8")
            env.insert("COLUMNS", "110")
            proc.setProcessEnvironment(env)
            proc.setWorkingDirectory(str(PROJECT_DIR))
            proc.setProcessChannelMode(QProcess.ProcessChannelMode.MergedChannels)
            proc.finished.connect(lambda *_: out.setPlainText(re.sub(
                r"\x1b\[[0-9;]*[A-Za-z]", "", bytes(proc.readAllStandardOutput()).decode("utf-8", "replace"))))
            proc.start(sys.executable, ["-W", "ignore", "-m", "jarvis", "--selftest"])
            self._check_proc = proc
        run.clicked.connect(check)
        t.addWidget(run, 0, Qt.AlignmentFlag.AlignLeft)
        t.addWidget(out)

        a = self._card(lay, "About")
        a.addWidget(_label("J.A.R.V.I.S. - a personal desktop assistant. Built on MARK LVI by FatihMakes "
                           "(CC BY-NC 4.0): free to share, not to sell.", "help"))
        lay.addStretch()
        return w

    def closeEvent(self, event):
        if getattr(self, "_g_timer", None):
            self._g_timer.stop()
        super().closeEvent(event)
