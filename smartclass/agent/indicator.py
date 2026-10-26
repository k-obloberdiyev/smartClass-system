"""The "your screen is being viewed" banner shown while a teacher is watching.

Like the lock overlay, it runs as its own process (agent --indicator --parent
PID) so Tk is only loaded while it's needed, it closes itself if the agent
dies, and the agent restarts it if a student closes it — so a student can't
silence it while the screen is still being streamed. Unlike the lock, it is a
small banner that never takes focus, so it doesn't interrupt the student's work.

This visibility is deliberate: it's what separates a classroom tool from
spyware. Monitoring and this banner start and stop together.
"""

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("smartclass.agent")

TEXT = "\U0001f441  Your teacher can see this screen"
REASSERT_MS = 1000
WIDTH, HEIGHT = 460, 40
BACKGROUND = "#b23b2e"
FOREGROUND = "#ffffff"


def indicator_command():
    args = ["--indicator", "--parent", str(os.getpid())]
    if getattr(sys, "frozen", False):  # PyInstaller build: the exe is the agent
        return [sys.executable, *args]
    return [sys.executable, "-m", "smartclass.agent", *args]


class MonitorIndicator:
    """Parent-side control of the banner process: show / hide / keep alive."""

    def __init__(self):
        self._want = False
        self._proc = None

    @property
    def showing(self):
        return self._want

    def show(self):
        self._want = True
        if not self._running():
            self._spawn()

    def hide(self):
        self._want = False
        self._kill()

    def ensure(self):
        """Restart the banner if the student managed to close it."""
        if self._want and not self._running():
            log.warning("Monitoring banner was closed; restarting it")
            self._spawn()

    def _running(self):
        return self._proc is not None and self._proc.poll() is None

    def _spawn(self):
        self._proc = subprocess.Popen(
            indicator_command(),
            cwd=Path(__file__).resolve().parents[2],  # so "-m smartclass.agent" resolves
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )

    def _kill(self):
        if self._running():
            self._proc.terminate()
            try:
                self._proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                self._proc.kill()
        self._proc = None


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


def run(parent_pid=None):
    import tkinter as tk

    root = tk.Tk()
    root.title("SmartClass")
    screen_w = root.winfo_screenwidth()
    x = (screen_w - WIDTH) // 2
    root.overrideredirect(True)
    root.geometry(f"{WIDTH}x{HEIGHT}+{x}+0")
    root.configure(bg=BACKGROUND)
    root.attributes("-topmost", True)
    root.resizable(False, False)
    root.protocol("WM_DELETE_WINDOW", lambda: None)

    tk.Label(root, text=TEXT, bg=BACKGROUND, fg=FOREGROUND,
             font=("Segoe UI", 15, "bold")).pack(expand=True, fill=tk.BOTH)

    def reassert():
        if parent_pid and not _parent_alive(parent_pid):
            root.destroy()
            return
        # Stay on top, but never grab focus: the student keeps working normally.
        root.lift()
        root.attributes("-topmost", True)
        root.after(REASSERT_MS, reassert)

    reassert()
    root.mainloop()
