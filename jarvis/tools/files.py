"""Finding, reading and writing the user's files."""
from __future__ import annotations

import fnmatch
import os
import time
from datetime import datetime
from pathlib import Path

from .. import undo as undo_stack
from .. import winutil
from ..config import get_settings
from . import outcome
from .registry import P, registry

SKIP_DIRS = {".git", "node_modules", "__pycache__", ".venv", "venv", "appdata", "$recycle.bin", ".cache",
             "site-packages", ".idea", ".vs"}


def resolve_path(path: str) -> Path:
    """Accepts absolute paths, '~', %VARS%, 'Desktop\\notes.txt' style, or bare names (-> Documents)."""
    p = os.path.expandvars(os.path.expanduser(path.strip().strip('"')))
    if os.path.isabs(p):
        return Path(p)
    first, _, rest = p.replace("/", "\\").partition("\\")
    base = winutil.known_folder(first)
    if base:
        return Path(base) / rest
    return Path(winutil.known_folder("documents") or Path.home()) / p


def _size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


@registry.tool(
    "find_files",
    "Find files or folders by name (wildcards allowed, e.g. '*.pdf', 'budget') in the user's Desktop, Documents, "
    "Downloads, Pictures, Music and Videos - or in a given folder. With no name, lists the folder's contents.",
    {"name": P("string", "Name, part of a name, or wildcard pattern", required=False),
     "folder": P("string", "Folder to look in, e.g. 'Downloads' or 'C:\\Projects'", required=False),
     "limit": P("integer", "Max results (default 20)", required=False)},
    slow=True,
)
def find_files(name: str = "", folder: str = "", limit: int = 20) -> str:
    limit = max(1, min(int(limit or 20), 100))
    if folder:
        roots = [resolve_path(folder)]
    else:
        roots = [Path(p) for p in (winutil.known_folder(n) for n in
                                   ("desktop", "documents", "downloads", "pictures", "music", "videos")) if p]
    if not name:
        root = roots[0]
        if not root.is_dir():
            return f"{root} isn't a folder."
        entries = sorted(root.iterdir(), key=lambda e: (not e.is_dir(), e.name.lower()))
        lines = [f"{'[folder] ' if e.is_dir() else ''}{e.name}" + ("" if e.is_dir() else f" ({_size(e.stat().st_size)})")
                 for e in entries[:limit * 3]]
        return f"Contents of {root} ({len(entries)} items):\n" + "\n".join(lines)
    pattern = name.lower()
    wildcard = any(c in pattern for c in "*?[")
    hits: list[tuple[float, Path]] = []
    deadline = time.time() + 8
    for root in roots:
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS and not d.startswith(".")]
            for entry in dirnames + filenames:
                low = entry.lower()
                if (fnmatch.fnmatch(low, pattern) if wildcard else pattern in low):
                    full = Path(dirpath) / entry
                    try:
                        hits.append((full.stat().st_mtime, full))
                    except OSError:
                        pass
            if time.time() > deadline:
                break
    if not hits:
        return f"No files matching '{name}' found."
    hits.sort(reverse=True)
    lines = [f"- {p}  (modified {datetime.fromtimestamp(m):%Y-%m-%d %H:%M})" for m, p in hits[:limit]]
    more = f"\n...and {len(hits) - limit} more." if len(hits) > limit else ""
    return f"Found {len(hits)} match(es), newest first:\n" + "\n".join(lines) + more


@registry.tool(
    "read_file",
    "Read a text file (notes, code, logs, CSV, Markdown...).",
    {"path": P("string", "File path, e.g. 'Desktop\\notes.txt' or a full path")},
)
def read_file(path: str) -> str:
    p = resolve_path(path)
    if not p.is_file():
        return f"No file at {p}."
    if p.stat().st_size > 5 * 2**20:
        return f"{p.name} is {_size(p.stat().st_size)} - too large to read aloud."
    raw = p.read_bytes()
    if b"\x00" in raw[:4096]:
        return f"{p.name} is a binary file (not text)."
    for enc in ("utf-8-sig", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeDecodeError:
            continue
    body = text[:7000] + ("\n...(truncated)" if len(text) > 7000 else "")
    return f"Contents of {p} ({len(text)} chars):\n" + outcome.untrusted(f"file {p.name}", body)


def _write_question(args: dict) -> str | None:
    p = resolve_path(args.get("path", ""))
    if args.get("append") or not p.exists():
        return None
    if not get_settings().get("safety.confirm_overwrite", True):
        return None
    return f"Overwrite the existing file {p.name}?"


@registry.tool(
    "write_file",
    "Create or overwrite a text file, or append to one. Relative paths go in Documents.",
    {"path": P("string", "File path, e.g. 'Desktop\\shopping.txt'"),
     "content": P("string", "Text to write"),
     "append": P("boolean", "Append instead of overwrite (default false)", required=False)},
    confirm=_write_question,
)
def write_file(path: str, content: str, append: bool = False) -> str:
    p = resolve_path(path)
    existed = p.exists()
    previous = p.read_bytes() if existed and p.stat().st_size <= undo_stack.MAX_FILE_BYTES else None
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "a" if append else "w", encoding="utf-8") as f:
        f.write(content if not append or content.startswith("\n") else "\n" + content)
    written = p.read_bytes()

    if not existed:
        def reverse() -> str:
            if p.exists() and p.read_bytes() == written:
                p.unlink()
                return f"Deleted {p.name}, which I had created."
            return f"Left {p.name} alone - it has changed since I wrote it."
        undo_stack.register(f"create {p.name}", reverse)
    elif previous is not None:
        def reverse() -> str:
            p.write_bytes(previous)
            return f"Restored the previous contents of {p.name}."
        undo_stack.register(f"{'append to' if append else 'overwrite'} {p.name}", reverse)

    ok = content.strip()[:200] in written.decode("utf-8", "replace")
    msg = f"{'Appended to' if append else 'Saved'} {p}."
    return outcome.done(msg) if ok else outcome.unverified(msg + " (couldn't read the text back)")
