"""Google sign-in for Jarvis.

One OAuth client (yours, made once in Google Cloud - see google_setup.py) and one saved sign-in per Google
account:
  main       your own account: Calendar, Gmail, Contacts, Drive
  classroom  your son's school account: Google Classroom (read-only)

Sign-ins live in %USERPROFILE%\\.jarvis\\google, outside OneDrive - they are as good as passwords.
Delete a token_*.json file (or remove "Jarvis" at myaccount.google.com/permissions) to disconnect.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import threading
from pathlib import Path

from .config import HOME_DIR

log = logging.getLogger(__name__)

GOOGLE_DIR = HOME_DIR / "google"
CLIENT_FILE = GOOGLE_DIR / "client_secret.json"
ACCOUNTS_FILE = GOOGLE_DIR / "accounts.json"

_G = "https://www.googleapis.com/auth/"
IDENTITY = ["openid", _G + "userinfo.email"]
SCOPES = {
    "main": IDENTITY + [
        _G + "calendar",                  # read your calendars, add/move/delete events
        _G + "gmail.modify",              # read, draft, send, archive (never permanent delete)
        _G + "contacts.readonly",         # "email Mum" -> her address
        _G + "contacts.other.readonly",   # people you've emailed but never saved
        _G + "drive.readonly",            # find and open your Docs/files
    ],
    "classroom": IDENTITY + [
        _G + "classroom.courses.readonly",
        _G + "classroom.coursework.me.readonly",      # assignments, due dates, turned-in / missing
        _G + "classroom.announcements.readonly",
    ],
}
LABELS = {"main": "your Google account", "classroom": "the Classroom account"}

os.environ.setdefault("OAUTHLIB_RELAX_TOKEN_SCOPE", "1")  # fine if someone unticks a box; we check what we got

_lock = threading.Lock()


class NotConnected(Exception):
    """This Google account hasn't been signed in (or the sign-in was revoked)."""


def token_path(account: str) -> Path:
    return GOOGLE_DIR / f"token_{account}.json"


def has_client() -> bool:
    return CLIENT_FILE.exists()


def is_client_file(path: Path) -> bool:
    """The JSON Google gives you for a *Desktop app* OAuth client."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        return "installed" in data and "client_id" in data["installed"]
    except (OSError, ValueError):
        return False


def download_dirs() -> list[Path]:
    home = Path.home()
    dirs = [home / "Downloads", home / "OneDrive" / "Downloads"]
    try:  # wherever Windows really keeps Downloads
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                            r"Software\Microsoft\Windows\CurrentVersion\Explorer\User Shell Folders") as k:
            val, _ = winreg.QueryValueEx(k, "{374DE290-123F-4565-9164-39C4925E467B}")
            dirs.insert(0, Path(os.path.expandvars(val)))
    except OSError:
        pass
    return [d for d in dict.fromkeys(dirs) if d.exists()]


def find_downloaded_client(since: float = 0) -> Path | None:
    """A client_secret_....json in Downloads (newer than `since`), if Google's download has landed."""
    for d in download_dirs():
        for f in sorted(d.glob("client_secret*.json"), key=lambda p: p.stat().st_mtime, reverse=True):
            if f.stat().st_mtime >= since and is_client_file(f):
                return f
    return None


def import_client(path: Path, remove_original: bool = True) -> None:
    import shutil
    if not is_client_file(path):
        raise ValueError("That isn't the right file - it should be the JSON from 'Download JSON' for a Desktop app.")
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(path, CLIENT_FILE)
    if remove_original:
        try:
            Path(path).unlink()  # no stray copy of the key left in Downloads
        except OSError:
            pass


def connected(account: str = "main") -> bool:
    return token_path(account).exists()


def accounts() -> dict:
    try:
        return json.loads(ACCOUNTS_FILE.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def email_of(account: str) -> str:
    return accounts().get(account, {}).get("email", "")


def granted(account: str) -> set[str]:
    return set(accounts().get(account, {}).get("scopes", []))


def has_scope(account: str, scope: str) -> bool:
    got = granted(account)
    return not got or (_G + scope) in got  # unknown (older token) -> assume yes and let the API say


def credentials(account: str = "main"):
    """Valid credentials for an account, refreshed and re-saved when needed."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    path = token_path(account)
    if not path.exists():
        raise NotConnected(account)
    with _lock:
        creds = Credentials.from_authorized_user_file(str(path))
        if not creds.valid:
            if not creds.refresh_token:
                raise NotConnected(account)
            try:
                creds.refresh(Request())
            except RefreshError as e:
                # Usually Google's 7-day limit for apps left in "Testing" - sign in again, same account.
                log.warning("Google sign-in for %s has expired: %s", account, e)
                raise NotConnected(account, "expired") from e
            path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def service(api: str, version: str, account: str = "main"):
    """A Google API client. Built per call: the underlying HTTP client isn't thread-safe, and Jarvis runs
    tools on several threads (the briefing fetches its sections in parallel)."""
    from googleapiclient.discovery import build
    return build(api, version, credentials=credentials(account), cache_discovery=False)


def _email_from_id_token(id_token: str | None) -> str:
    try:
        payload = id_token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return json.loads(base64.urlsafe_b64decode(payload)).get("email", "")
    except Exception:
        return ""


def _use_chrome() -> None:
    """Open the sign-in page in Chrome, where the user's Google accounts are, rather than Edge."""
    import webbrowser

    from .tools.apps import _chrome_path
    chrome = _chrome_path()
    if chrome:
        webbrowser.register("chrome-jarvis", None, webbrowser.BackgroundBrowser(chrome), preferred=True)


def _signin_browser(on_url) -> str:
    """A browser entry that hands the sign-in link to `on_url` (so a window can show it as a fallback)
    and opens it in Chrome, or the default browser."""
    import webbrowser

    from .tools.apps import _chrome_path
    chrome = _chrome_path()

    class _Browser(webbrowser.BaseBrowser):
        def open(self, url, new=0, autoraise=True):
            if on_url:
                try:
                    on_url(url)
                except Exception:
                    pass
            if chrome:
                return webbrowser.BackgroundBrowser(chrome).open(url)
            return webbrowser.open(url)

    webbrowser.register("jarvis-signin", None, _Browser("jarvis-signin"))
    return "jarvis-signin"


def sign_in(account: str, timeout: int | None = None, on_url=None) -> tuple[str, list[str]]:
    """Opens Google's sign-in page and waits for the user. Returns (email, scopes that weren't granted)."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    if not has_client():
        raise FileNotFoundError(CLIENT_FILE)
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_FILE), SCOPES[account])
    creds = flow.run_local_server(
        port=0, open_browser=True, browser=_signin_browser(on_url),
        prompt="select_account consent", access_type="offline", timeout_seconds=timeout,
        authorization_prompt_message="\n  If no Chrome tab opened, copy this whole link into Chrome's address bar:\n\n  {url}\n",
        success_message="Jarvis is connected. You can close this tab.")
    GOOGLE_DIR.mkdir(parents=True, exist_ok=True)
    token_path(account).write_text(creds.to_json(), encoding="utf-8")
    got = set(flow.oauth2session.token.get("scope") or creds.scopes or [])
    if isinstance(flow.oauth2session.token.get("scope"), str):
        got = set(flow.oauth2session.token["scope"].split())
    email = _email_from_id_token(getattr(creds, "id_token", None))
    data = accounts()
    data[account] = {"email": email, "scopes": sorted(got)}
    ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
    missing = [s.replace(_G, "") for s in SCOPES[account] if s not in got and s not in IDENTITY]
    return email, missing


_reconnecting: dict[str, float] = {}


def reconnect_in_background(account: str) -> bool:
    """Open Google's sign-in in Chrome for an expired account and wait (up to 5 min) for the user to click
    through it. Returns False if a sign-in for this account is already open or was opened very recently."""
    import time
    now = time.time()
    if now - _reconnecting.get(account, 0) < 600:
        return False
    _reconnecting[account] = now

    def work():
        try:
            email, _ = sign_in(account, timeout=300)
            log.info("Google %s signed in again: %s", account, email)
            from .events import bus
            bus.emit("notice", text=f"Google reconnected ({email or account}).")
        except Exception as e:
            log.warning("Google re-sign-in for %s didn't complete: %s", account, e)
        finally:
            _reconnecting[account] = time.time() - 540  # allow another try a minute later

    threading.Thread(target=work, name=f"google-reconnect-{account}", daemon=True).start()
    return True


def disconnect(account: str) -> None:
    token_path(account).unlink(missing_ok=True)
    data = accounts()
    data.pop(account, None)
    if GOOGLE_DIR.exists():
        ACCOUNTS_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")
