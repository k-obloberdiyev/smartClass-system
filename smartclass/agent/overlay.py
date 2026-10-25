"""Full-screen lock overlay window.

Runs as its own process (agent --overlay MESSAGE --parent PID) so Tk is only
loaded while a PC is locked. The agent unlocks by killing this process; the
window also closes itself if the agent dies, so a PC can never stay locked
with nothing to unlock it.

This is a classroom attention tool, not a security boundary: Ctrl+Alt+Del
and Task Manager still work, and the agent restarts the overlay if killed.
"""

import os
import sys
import tkinter as tk

REASSERT_MS = 300
BACKGROUND = "#1f2a44"
FOREGROUND = "#ffffff"
SUBTLE = "#aab4cc"


def _parent_alive(pid):
    if sys.platform == "win32":
        import ctypes

        SYNCHRONIZE, WAIT_TIMEOUT = 0x00100000, 0x102
        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(SYNCHRONIZE, False, pid)
        if not handle:
            return False
        try:
            return kernel32.WaitForSingleObject(handle, 0) == WAIT_TIMEOUT
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        pass
    return True


def _screen_geometry(root):
    """(x, y, width, height) of the whole desktop, and the primary screen's (width, height)."""
    if sys.platform == "win32":
        import ctypes

        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)  # real pixels on scaled displays
        except (AttributeError, OSError):
            ctypes.windll.user32.SetProcessDPIAware()
        metric = ctypes.windll.user32.GetSystemMetrics
        # SM_XVIRTUALSCREEN.. SM_CYVIRTUALSCREEN span all monitors; SM_CXSCREEN/SM_CYSCREEN is the primary.
        return (metric(76), metric(77), metric(78), metric(79)), (metric(0), metric(1))
    w, h = root.winfo_screenwidth(), root.winfo_screenheight()
    return (0, 0, w, h), (w, h)


def run(message, parent_pid=None):
    root = tk.Tk()
    root.title("SmartClass")
    (x, y, width, height), (primary_w, primary_h) = _screen_geometry(root)
    root.overrideredirect(True)
    root.geometry(f"{width}x{height}{x:+d}{y:+d}")
    root.configure(bg=BACKGROUND, cursor="none")
    root.attributes("-topmost", True)
    root.protocol("WM_DELETE_WINDOW", lambda: None)
    for sequence in ("<Alt-F4>", "<Escape>", "<Control-w>"):
        root.bind_all(sequence, lambda e: "break")

    # Centre the text on the primary monitor (at 0,0 in desktop coordinates).
    box = tk.Frame(root, bg=BACKGROUND)
    box.place(x=-x + primary_w // 2, y=-y + primary_h // 2, anchor="center")
    tk.Label(box, text=message, bg=BACKGROUND, fg=FOREGROUND, wraplength=int(primary_w * 0.8),
             font=("Segoe UI", 44, "bold")).pack()
    tk.Label(box, text="Your screen has been locked by your teacher.", bg=BACKGROUND, fg=SUBTLE,
             font=("Segoe UI", 18)).pack(pady=(24, 0))

    def reassert():
        if parent_pid and not _parent_alive(parent_pid):
            root.destroy()
            return
        root.lift()
        root.attributes("-topmost", True)
        root.focus_force()
        root.after(REASSERT_MS, reassert)

    reassert()
    root.mainloop()
