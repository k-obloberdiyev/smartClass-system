"""Stateless commands the agent can carry out on the student PC.

The overlay lock keeps state, so it lives in lock.py and the agent client.

Each action takes the command's args dict and returns a detail string, or
raises. Student PCs are Windows; other platforms raise NotImplementedError
so the agent can still be run on a Mac for development.
"""

import base64
import binascii
import os
import re
import subprocess
import sys
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from ..common.protocol import (
    CANCEL_SHUTDOWN, ERROR, LOCK_SCREEN, MAX_FILE_BYTES, MESSAGE, OK, OPEN_URL, RESTART,
    SEND_FILE, SHUTDOWN, UNSUPPORTED,
)
from . import popup


def open_url(args):
    url = str(args.get("url") or "").strip()
    if not url:
        raise ValueError("no URL given")
    # "mailto:x" has a scheme; "localhost:8000" does not (a port follows the colon).
    if not re.match(r"[a-z][a-z0-9+.-]*:(?!\d)", url, re.IGNORECASE):
        url = "https://" + url
    # Only web pages: never let a command launch file:// or other handlers.
    parts = urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.netloc:
        raise ValueError(f"not a web address: {url}")
    if not webbrowser.open(url):
        raise RuntimeError("no web browser available")
    return url


def message(args):
    text = str(args.get("text") or "").strip()
    if not text:
        raise ValueError("no message text")
    title = str(args.get("title") or "").strip() or "Message from your teacher"
    popup.show(title, text)
    return "shown"


def received_files_dir():
    downloads = Path.home() / "Downloads"
    base = downloads if downloads.is_dir() else Path.home()
    return base / "SmartClass"


def _safe_name(name):
    # Keep only the final component and drop anything that could escape the folder.
    name = os.path.basename(str(name or "").replace("\\", "/").strip())
    name = name.lstrip(".") or "file"
    return re.sub(r'[<>:"/\\|?*\x00-\x1f]', "_", name)


def _unique_path(folder, name):
    path = folder / name
    if not path.exists():
        return path
    stem, suffix = path.stem, path.suffix
    for i in range(1, 1000):
        candidate = folder / f"{stem} ({i}){suffix}"
        if not candidate.exists():
            return candidate
    raise FileExistsError(f"too many copies of {name}")


def send_file(args):
    name = _safe_name(args.get("name"))
    try:
        data = base64.b64decode(str(args.get("data") or ""), validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("file data is not valid base64") from None
    if not data:
        raise ValueError("empty file")
    if len(data) > MAX_FILE_BYTES:
        raise ValueError(f"file is too large ({len(data)} bytes)")
    folder = received_files_dir()
    folder.mkdir(parents=True, exist_ok=True)
    path = _unique_path(folder, name)
    path.write_bytes(data)
    popup.show("File from your teacher", f"Saved to your SmartClass folder:\n\n{path.name}",
               open_path=str(folder))
    return str(path)


def lock_screen(args):
    _require_windows("locking")
    import ctypes

    if not ctypes.windll.user32.LockWorkStation():
        raise ctypes.WinError()
    return "locked"


DEFAULT_SHUTDOWN_DELAY = 60
MAX_SHUTDOWN_DELAY = 600
# Windows error when "shutdown /a" finds nothing to cancel.
NO_SHUTDOWN_IN_PROGRESS = 1116
_SHUTDOWN_MESSAGES = {
    "/s": "Your teacher is shutting down this computer. Save your work now.",
    "/r": "Your teacher is restarting this computer. Save your work now.",
}


def _require_windows(what):
    if sys.platform != "win32":
        raise NotImplementedError(f"{what} is only supported on Windows")


def _run_shutdown_exe(*args):
    result = subprocess.run(
        ["shutdown", *args], capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if result.returncode != 0:
        raise OSError(result.returncode, (result.stderr or result.stdout).strip() or "shutdown failed")


def _power(flag, args):
    try:
        delay = int(args.get("delay", DEFAULT_SHUTDOWN_DELAY))
    except (TypeError, ValueError):
        raise ValueError("delay must be a number of seconds") from None
    delay = max(0, min(delay, MAX_SHUTDOWN_DELAY))
    # Windows shows the message with a countdown; a delay > 0 also forces apps to close.
    _run_shutdown_exe(flag, "/t", str(delay), "/c", _SHUTDOWN_MESSAGES[flag])
    return f"in {delay}s"


def shutdown(args):
    _require_windows("shutting down")
    return _power("/s", args)


def restart(args):
    _require_windows("restarting")
    return _power("/r", args)


def cancel_shutdown(args):
    _require_windows("cancelling a shutdown")
    try:
        _run_shutdown_exe("/a")
    except OSError as e:
        if e.errno == NO_SHUTDOWN_IN_PROGRESS:
            return "nothing to cancel"
        raise
    return "cancelled"


ACTIONS = {
    OPEN_URL: open_url,
    MESSAGE: message,
    SEND_FILE: send_file,
    LOCK_SCREEN: lock_screen,
    SHUTDOWN: shutdown,
    RESTART: restart,
    CANCEL_SHUTDOWN: cancel_shutdown,
}


def execute(action, args):
    """Run an action and return (status, detail). Never raises."""
    handler = ACTIONS.get(action)
    if handler is None:
        return UNSUPPORTED, f"unknown action {action!r}"
    try:
        return OK, handler(args or {})
    except NotImplementedError as e:
        return UNSUPPORTED, str(e)
    except Exception as e:
        return ERROR, str(e) or type(e).__name__
