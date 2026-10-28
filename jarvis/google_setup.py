"""Connect Jarvis to Google - run by connect_google.bat (or: python -m jarvis --google).

Google only lets an app read your calendar or email through an "OAuth client" that belongs to someone.
Jarvis isn't a published app, so the client is yours: a free Google Cloud project only you use. This walks
through making it (about five minutes, once), then signs in your account and, optionally, your son's
Classroom account.
"""
from __future__ import annotations

import json
import shutil
import sys
import time
import webbrowser
from pathlib import Path

from . import google_auth as ga

APIS = ",".join(["calendar-json.googleapis.com", "gmail.googleapis.com", "people.googleapis.com",
                 "drive.googleapis.com", "classroom.googleapis.com"])
STEPS = [
    ("Create a project",
     "https://console.cloud.google.com/projectcreate",
     ["Sign in with YOUR Google account (the one whose calendar and Gmail Jarvis should use).",
      "Project name: Jarvis   ->  click CREATE.  (No billing is needed - this is all free.)",
      "Wait a few seconds until it says the project was created."]),
    ("Switch on the Google services Jarvis uses",
     f"https://console.cloud.google.com/flows/enableapi?apiid={APIS}",
     ["At the top, make sure the project says Jarvis.",
      "Click NEXT, then ENABLE.  (Calendar, Gmail, People/contacts, Drive and Classroom.)"]),
    ("Describe the app to Google",
     "https://console.cloud.google.com/auth/overview",
     ["Click GET STARTED.",
      "App name: Jarvis.  User support email: your email.  NEXT.",
      "Audience: External.  NEXT.",
      "Contact email: your email.  NEXT.  Tick 'I agree'.  CONTINUE, then CREATE."]),
    ("Allow your accounts to sign in",
     "https://console.cloud.google.com/auth/audience",
     ["Leave 'Publishing status' on Testing (publishing needs a website and privacy policy - not worth it).",
      "Scroll to 'Test users', click '+ ADD USERS', and add your Gmail address",
      "  (and your son's school account too, if you want Classroom). Click SAVE.",
      "Google signs Jarvis out every 7 days in Testing - Jarvis reopens the sign-in for you when it happens."]),
    ("Make the key Jarvis signs in with",
     "https://console.cloud.google.com/auth/clients",
     ["Check the project at the top says Jarvis, then click '+ CREATE CLIENT'.",
      "Application type: open the dropdown and pick 'Desktop app' (NOT 'Web application').",
      "Name: Jarvis (or leave it).  Click CREATE.",
      "A box pops up ('OAuth client created'). Click DOWNLOAD JSON in that box.",
      "  (Closed it by accident? Click 'Jarvis' in the list, then the download icon by the secret.)",
      "Nothing to copy or paste - this window finds the file in Downloads by itself."]),
]


def _say(text: str = "") -> None:
    print(text, flush=True)


def _wait_enter(prompt: str = "Press Enter when that's done...") -> None:
    try:
        input(f"\n  {prompt}")
    except EOFError:
        pass


def _open(url: str) -> None:
    ga._use_chrome()
    webbrowser.open(url)


def _download_dirs() -> list[Path]:
    home = Path.home()
    dirs = [home / "Downloads", home / "OneDrive" / "Downloads"]
    try:  # the real Downloads folder, wherever Windows keeps it
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            val, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            import os
            dirs.insert(0, Path(os.path.expandvars(val)))
    except OSError:
        pass
    return [d for d in dict.fromkeys(dirs) if d.exists()]


def _looks_like_client(path: Path) -> bool:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return "installed" in data and "client_id" in data["installed"]
    except (OSError, ValueError):
        return False


def _catch_client_file(since: float, timeout: float = 1200) -> Path | None:
    """Watch the Downloads folder for the client_secret_....json Google hands out. Enter = type a path."""
    import msvcrt
    _say("\n  Waiting for the downloaded file (client_secret_....json)...  "
         "[or press Enter to type where it is]")
    end = time.time() + timeout
    while time.time() < end:
        for d in _download_dirs():
            for f in sorted(d.glob("client_secret*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
                if f.stat().st_mtime >= since - 5 and _looks_like_client(f):
                    return f
        if msvcrt.kbhit():
            msvcrt.getwch()
            typed = input("  Path to the JSON file: ").strip().strip('"')
            if typed and _looks_like_client(Path(typed)):
                return Path(typed)
            _say("  That isn't the right file - it should be the one from 'Download JSON'. Still watching...")
        time.sleep(1)
    return None


def _make_client() -> bool:
    _say("=" * 72)
    _say("  Google needs a key before it lets an app read your calendar or Gmail.")
    _say("  Jarvis isn't a published app, so you'll make your own key - a free, private Google")
    _say("  Cloud project that only you use. Five steps, about five minutes, only ever once.")
    _say("  Each step opens the right page in Chrome.")
    _say("=" * 72)
    started = time.time()
    for i, (title, url, lines) in enumerate(STEPS, 1):
        _say(f"\n  STEP {i} of {len(STEPS)}: {title}")
        for line in lines:
            _say(f"    - {line}")
        _open(url)
        if i < len(STEPS):
            _wait_enter()
    found = _catch_client_file(started)
    if not found:
        _say("\n  No key file arrived. Run connect_google.bat again whenever you're ready.")
        return False
    ga.GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(found, ga.CLIENT_FILE)
    try:
        found.unlink()  # don't leave a copy lying around in Downloads
    except OSError:
        pass
    _say(f"\n  Got it - key saved to {ga.CLIENT_FILE}")
    return True


def _connect(account: str) -> bool:
    _say("\n  Opening Google's sign-in page in Chrome...")
    if account == "main":
        _say("    1. Pick YOUR account.")
    else:
        _say("    1. Pick your SON'S school account (use 'Use another account' if it isn't listed).")
    _say("    2. Google will say it hasn't verified this app. That's expected - it's your own app.")
    _say("       Click 'Advanced', then 'Go to Jarvis (unsafe)'.")
    _say("    3. Tick 'Select all' so every box is checked, then Continue.")
    try:
        email, missing = ga.sign_in(account)
    except Exception as e:
        text = str(e)
        _say(f"\n  Sign-in didn't complete: {text[:300]}")
        if account == "classroom" and any(w in text.lower() for w in ("admin", "access_denied", "blocked", "policy")):
            _say("  School accounts often block apps their admin hasn't approved - especially for students")
            _say("  under 18. The block page usually has a 'Request access' button; the school's IT admin can")
            _say("  approve 'Jarvis'. Until then, Jarvis reads the Classroom guardian summaries that arrive in")
            _say("  your Gmail instead (ask the teacher to add you as a guardian if you don't get them).")
        return False
    _say(f"\n  Connected: {email or 'signed in'}")
    if missing:
        _say(f"  Not allowed (some boxes were unticked): {', '.join(missing)}")
        _say("  Jarvis will skip those. Run this again and tick 'Select all' to add them.")
    return True


def _check() -> None:
    from .tools import gservices
    _say("\n  Quick check:")
    for label, fn in gservices.HEALTH_CHECKS:
        try:
            _say(f"    {label}: {fn()}")
        except Exception as e:
            _say(f"    {label}: problem - {type(e).__name__}: {str(e)[:160]}")


def _yes(prompt: str) -> bool:
    try:
        return input(f"\n  {prompt} [y/N] ").strip().lower().startswith("y")
    except EOFError:
        return False


def main() -> int:
    try:
        sys.stdout.reconfigure(encoding="utf-8")
    except Exception:
        pass
    _say("\n  J.A.R.V.I.S. - connect Google\n")
    if not ga.has_client() and not _make_client():
        return 1
    if ga.connected("main"):
        _say(f"  Your account is already connected ({ga.email_of('main') or 'signed in'}).")
        if _yes("Sign it in again (e.g. to switch accounts or tick more boxes)?"):
            _connect("main")
    else:
        if not _connect("main"):
            return 1
    if ga.connected("classroom"):
        _say(f"\n  Classroom account already connected ({ga.email_of('classroom') or 'signed in'}).")
        again = _yes("Sign it in again?")
    else:
        _say("\n  Google Classroom: Jarvis can tell you what's due and what's missing for your son.")
        _say("  That needs your son's school account signed in once (on this PC, in Chrome).")
        again = _yes("Connect your son's Classroom account now?")
    if again:
        _connect("classroom")
    _check()
    _say("\n  All done. Restart Jarvis and try: \"Jarvis, what's on my calendar today?\",")
    _say("  \"any important emails?\", or \"what homework is due this week?\"")
    _wait_enter("Press Enter to close.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
