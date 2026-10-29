"""Keep Jarvis's Python packages in step with requirements.txt.

After an update (a new ZIP unzipped over the old folder, or a git pull) requirements.txt may list new
packages; run_jarvis.bat calls this first, and it installs them only when the file has changed.
"""
from __future__ import annotations

import hashlib
import subprocess
import sys

from .config import HOME_DIR, PROJECT_DIR

REQ = PROJECT_DIR / "requirements.txt"
STAMP = HOME_DIR / "requirements.sha256"


def _digest() -> str:
    return hashlib.sha256(REQ.read_bytes()).hexdigest() if REQ.exists() else ""


def main() -> int:
    if "--mark" in sys.argv:
        STAMP.parent.mkdir(parents=True, exist_ok=True)
        STAMP.write_text(_digest(), encoding="utf-8")
        return 0
    current = _digest()
    try:
        if STAMP.read_text(encoding="utf-8").strip() == current:
            return 0
    except OSError:
        pass
    print("Jarvis was updated - installing new components (one time)...", flush=True)
    code = subprocess.call([sys.executable, "-m", "pip", "install", "-r", str(REQ), "--quiet",
                            "--disable-pip-version-check"])
    if code == 0:
        STAMP.write_text(current, encoding="utf-8")
    return 0  # start Jarvis either way; a missing extra only disables that feature


if __name__ == "__main__":
    sys.exit(main())
