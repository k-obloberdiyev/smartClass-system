"""A small notice window for teacher messages and received files.

Runs as its own process (agent --popup ...) so Tk stays out of the agent's
event loop, like the lock overlay and the monitoring banner. Unlike those, a
notice is something the student reads and dismisses, so it's an ordinary
closable window and the agent doesn't track or restart it.
"""

import subprocess
import sys
from pathlib import Path

BACKGROUND = "#1f2a44"
FOREGROUND = "#ffffff"
ACCENT = "#2f6fed"


def popup_command(title, body, open_path=None):
    args = ["--popup", "--title", title, "--body", body]
    if open_path:
        args += ["--open", open_path]
    if getattr(sys, "frozen", False):  # PyInstaller build: the exe is the agent
        return [sys.executable, *args]
    return [sys.executable, "-m", "smartclass.agent", *args]


def show(title, body, open_path=None):
    """Spawn a notice window (fire and forget)."""
    subprocess.Popen(
        popup_command(title, body, open_path),
        cwd=Path(__file__).resolve().parents[2],
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def open_in_file_manager(path):
    path = str(path)
    if sys.platform == "win32":
        import os

        os.startfile(path)  # noqa: S606 - opening a folder for the student
    elif sys.platform == "darwin":
        subprocess.Popen(["open", path])
    else:
        subprocess.Popen(["xdg-open", path])


def run(title, body, open_path=None):
    import tkinter as tk

    root = tk.Tk()
    root.title(title or "SmartClass")
    root.configure(bg=BACKGROUND)
    root.attributes("-topmost", True)
    root.resizable(False, False)

    tk.Label(root, text=title or "Message from your teacher", bg=BACKGROUND, fg=FOREGROUND,
             font=("Segoe UI", 18, "bold"), wraplength=440, justify=tk.LEFT).pack(
        padx=28, pady=(24, 8), anchor="w")
    tk.Label(root, text=body, bg=BACKGROUND, fg=FOREGROUND,
             font=("Segoe UI", 14), wraplength=440, justify=tk.LEFT).pack(
        padx=28, pady=(0, 20), anchor="w")

    buttons = tk.Frame(root, bg=BACKGROUND)
    buttons.pack(padx=28, pady=(0, 24), anchor="e")
    if open_path:
        tk.Button(buttons, text="Open folder", font=("Segoe UI", 12),
                  command=lambda: open_in_file_manager(open_path)).pack(side=tk.LEFT, padx=(0, 8))
    tk.Button(buttons, text="Got it", font=("Segoe UI", 12, "bold"),
              command=root.destroy).pack(side=tk.LEFT)

    root.update_idletasks()
    w, h = root.winfo_width(), root.winfo_height()
    x = (root.winfo_screenwidth() - w) // 2
    y = (root.winfo_screenheight() - h) // 3
    root.geometry(f"+{x}+{y}")
    root.lift()
    root.after(100, lambda: root.attributes("-topmost", False))
    root.mainloop()
