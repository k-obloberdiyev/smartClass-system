"""Grab a screen thumbnail and the foreground window title.

Everything here blocks, so the agent calls it with asyncio.to_thread. Capture
only runs while a teacher is watching, and while it does the student sees a
banner (see indicator.py) — nothing about the screen leaves the PC silently.
"""

import io
import sys

from ..common.protocol import THUMBNAIL_MAX, THUMBNAIL_QUALITY


def _encode(img, max_px, quality):
    """Downscale a PIL image to fit max_px and return (jpeg_bytes, (w, h))."""
    from PIL import Image

    if img.mode != "RGB":
        img = img.convert("RGB")
    w, h = img.size
    longest = max(w, h) or 1
    if longest > max_px:
        scale = max_px / longest
        img = img.resize((max(1, round(w * scale)), max(1, round(h * scale))), Image.BILINEAR)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=quality)
    return buf.getvalue(), img.size


def grab_thumbnail(max_px=THUMBNAIL_MAX, quality=THUMBNAIL_QUALITY):
    """A JPEG thumbnail of the primary screen as (jpeg_bytes, (w, h))."""
    from PIL import ImageGrab

    return _encode(ImageGrab.grab(), max_px, quality)


def foreground_window():
    """Title of the focused window, best effort; "" when it can't be read."""
    if sys.platform == "win32":
        import ctypes

        user32 = ctypes.windll.user32
        hwnd = user32.GetForegroundWindow()
        if not hwnd:
            return ""
        length = user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, buf, length + 1)
        return buf.value
    return ""


def snapshot(max_px=THUMBNAIL_MAX, quality=THUMBNAIL_QUALITY):
    """One capture: (jpeg_bytes, (w, h), foreground_window_title)."""
    data, size = grab_thumbnail(max_px, quality)
    return data, size, foreground_window()
