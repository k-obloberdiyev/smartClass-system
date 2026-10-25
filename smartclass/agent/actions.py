"""Commands the agent can carry out on the student PC.

Each action takes the command's args dict and returns a detail string, or
raises. Student PCs are Windows; other platforms raise NotImplementedError
so the agent can still be run on a Mac for development.
"""

import re
import sys
import webbrowser
from urllib.parse import urlsplit

from ..common.protocol import ERROR, LOCK_SCREEN, OK, OPEN_URL, UNSUPPORTED


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


def lock_screen(args):
    if sys.platform != "win32":
        raise NotImplementedError("locking is only supported on Windows")
    import ctypes

    if not ctypes.windll.user32.LockWorkStation():
        raise ctypes.WinError()
    return "locked"


ACTIONS = {
    OPEN_URL: open_url,
    LOCK_SCREEN: lock_screen,
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
