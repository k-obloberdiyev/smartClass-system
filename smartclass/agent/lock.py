"""Starts, stops and restarts the overlay process (see overlay.py)."""

import logging
import os
import subprocess
import sys
from pathlib import Path

log = logging.getLogger("smartclass.agent")

DEFAULT_MESSAGE = "Eyes on the teacher, please"


def overlay_command(message):
    args = ["--overlay", message, "--parent", str(os.getpid())]
    if getattr(sys, "frozen", False):  # PyInstaller build: the exe is the agent
        return [sys.executable, *args]
    return [sys.executable, "-m", "smartclass.agent", *args]


class OverlayLock:
    def __init__(self):
        self.message = None
        self._proc = None

    @property
    def locked(self):
        return self.message is not None

    def lock(self, message=DEFAULT_MESSAGE):
        if self.locked and message == self.message and self._running():
            return
        self._kill()
        self.message = message
        self._spawn()

    def unlock(self):
        self.message = None
        self._kill()

    def ensure(self):
        """Restart the overlay if a student managed to close it."""
        if self.locked and not self._running():
            log.warning("Lock overlay was closed; restarting it")
            self._spawn()

    def _running(self):
        return self._proc is not None and self._proc.poll() is None

    def _spawn(self):
        self._proc = subprocess.Popen(
            overlay_command(self.message),
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
