"""SmartClass teacher window."""

import base64
import io
import logging
import queue
import time
import os
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import customtkinter as ctk
from PIL import Image

from ..common import config
from ..common.discovery import local_ip_addresses
from ..common.protocol import (
    CANCEL_SHUTDOWN, DEFAULT_HUB_PORT, LOCK_SCREEN, MAX_FILE_BYTES, MESSAGE, OK, OPEN_URL,
    OVERLAY_LOCK, OVERLAY_UNLOCK, RESTART, SEND_FILE, SHUTDOWN,
)
from .backend import Backend

POLL_MS = 100
SHUTDOWN_DELAY = 60
MONITOR_COLUMNS = 3


def load_config():
    cfg = config.load("teacher")
    if not cfg.get("class_code") or not cfg.get("teacher_key"):
        cfg.setdefault("class_code", config.generate_class_code())
        cfg.setdefault("teacher_key", config.generate_secret())
        config.save("teacher", cfg)
    cfg.setdefault("port", DEFAULT_HUB_PORT)
    return cfg


class TeacherApp:
    def __init__(self, root, backend):
        self.root = root
        self.backend = backend
        self.agents = {}  # agent_id -> info
        self.pending = {}  # agent_id -> info, awaiting approval
        self.last_result = {}  # agent_id -> text
        self.monitor_window = None
        self._build()
        self.root.after(POLL_MS, self._poll)

    # ---- layout ----

    def _build(self):
        cfg = self.backend.cfg
        self.root.title("SmartClass Teacher")
        self.root.geometry("960x640")
        self.root.minsize(760, 560)

        header = ctk.CTkFrame(self.root)
        header.pack(fill=tk.X, padx=10, pady=(10, 0))
        ctk.CTkLabel(header, text=f"Class code:  {cfg['class_code']}",
                     font=ctk.CTkFont(size=20, weight="bold")).pack(side=tk.LEFT, padx=10, pady=8)
        self.conn_label = ctk.CTkLabel(header, text="Starting…")
        self.conn_label.pack(side=tk.RIGHT, padx=10)

        if self.backend.embedded:
            ips = local_ip_addresses() or ["this computer's IP"]
            where = "   or   ".join(f"{ip}:{cfg['port']}" for ip in ips)
            hint = f"Student PCs connect to:  {where}"
        else:
            hint = f"Using hub:  {cfg['hub_url']}"
        ctk.CTkLabel(self.root, text=hint, anchor="w").pack(fill=tk.X, padx=20, pady=(4, 0))

        body = ctk.CTkFrame(self.root, fg_color="transparent")
        body.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        left = ctk.CTkFrame(body)
        left.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        # Shown only when PCs are waiting for approval (see _refresh_pending).
        self.pending_frame = ctk.CTkFrame(left, fg_color="#7a4a00")
        self.count_label = ctk.CTkLabel(left, text="Connected PCs (0)", font=ctk.CTkFont(size=16, weight="bold"))
        self.count_label.pack(pady=(6, 4))

        columns = ("name", "state", "address", "version", "result")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended", height=12)
        for col, title, width in (("name", "Computer", 170), ("state", "State", 130),
                                  ("address", "Address", 110), ("version", "Version", 60),
                                  ("result", "Last result", 200)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_target_label())

        ctk.CTkLabel(left, text="Log", font=ctk.CTkFont(size=14)).pack(pady=(8, 2))
        self.log_box = ctk.CTkTextbox(left, height=110, state="disabled")
        self.log_box.pack(fill=tk.X, padx=6, pady=(0, 6))

        right = ctk.CTkScrollableFrame(body, width=250, label_text="Commands")
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        self.target_label = ctk.CTkLabel(right, text="", wraplength=200)
        self.target_label.pack(pady=(0, 8))
        ctk.CTkButton(right, text="Clear selection", fg_color="gray",
                      command=lambda: self.tree.selection_set(())).pack(padx=10, fill=tk.X)

        ctk.CTkButton(right, text="👁  Watch screens", command=self._watch).pack(padx=10, pady=(14, 4), fill=tk.X)

        self.url_entry = ctk.CTkEntry(right, placeholder_text="Website, e.g. ielts.org")
        self.url_entry.pack(padx=10, pady=(16, 4), fill=tk.X)
        self.url_entry.bind("<Return>", lambda e: self._open_url())
        ctk.CTkButton(right, text="Open website", command=self._open_url).pack(padx=10, pady=4, fill=tk.X)

        self._section(right, "Send")
        self.message_entry = ctk.CTkEntry(right, placeholder_text="Message to students")
        self.message_entry.pack(padx=10, pady=4, fill=tk.X)
        self.message_entry.bind("<Return>", lambda e: self._send_message())
        ctk.CTkButton(right, text="Send message", command=self._send_message).pack(padx=10, pady=4, fill=tk.X)
        ctk.CTkButton(right, text="Send file…", command=self._send_file).pack(padx=10, pady=4, fill=tk.X)

        self._section(right, "Lock")
        self.lock_message = ctk.CTkEntry(right, placeholder_text="Message (optional)")
        self.lock_message.pack(padx=10, pady=4, fill=tk.X)
        ctk.CTkButton(right, text="Lock screens", command=self._lock).pack(padx=10, pady=4, fill=tk.X)
        ctk.CTkButton(right, text="Unlock", command=self._unlock).pack(padx=10, pady=4, fill=tk.X)
        ctk.CTkButton(right, text="Windows lock", fg_color="gray", command=self._windows_lock).pack(padx=10, pady=4, fill=tk.X)

        self._section(right, "Power")
        ctk.CTkButton(right, text="Restart…", fg_color="#c0392b", hover_color="#962d22",
                      command=lambda: self._power(RESTART, "Restart")).pack(padx=10, pady=4, fill=tk.X)
        ctk.CTkButton(right, text="Shut down…", fg_color="#c0392b", hover_color="#962d22",
                      command=lambda: self._power(SHUTDOWN, "Shut down")).pack(padx=10, pady=4, fill=tk.X)
        ctk.CTkButton(right, text="Cancel shutdown", fg_color="gray", command=self._cancel_shutdown).pack(padx=10, pady=4, fill=tk.X)

        self._section(right, "Pairing")
        ctk.CTkButton(right, text="Remove selected PC(s)", fg_color="gray",
                      command=self._remove_pcs).pack(padx=10, pady=4, fill=tk.X)

        self._update_target_label()

    def _section(self, parent, title):
        ctk.CTkLabel(parent, text=title, font=ctk.CTkFont(size=13, weight="bold"),
                     anchor="w").pack(padx=10, pady=(14, 0), fill=tk.X)

    # ---- commands ----

    def _targets(self):
        """Selected agent ids, or "all" when nothing is selected."""
        selected = list(self.tree.selection())
        return selected or "all"

    def _target_text(self):
        targets = self._targets()
        if targets == "all":
            return f"all {len(self.agents)} PCs"
        return f"{len(targets)} selected PC" + ("s" if len(targets) != 1 else "")

    def _update_target_label(self):
        self.target_label.configure(text=f"Commands go to: {self._target_text()}")

    def _open_url(self):
        url = self.url_entry.get().strip()
        if not url:
            return
        self.backend.send_command(OPEN_URL, {"url": url}, self._targets())
        self._log(f"Open {url} on {self._target_text()}")
        self.url_entry.delete(0, tk.END)

    def _send_message(self):
        text = self.message_entry.get().strip()
        if not text:
            return
        self.backend.send_command(MESSAGE, {"text": text}, self._targets())
        self._log(f"Message to {self._target_text()}: {text}")
        self.message_entry.delete(0, tk.END)

    def _send_file(self):
        path = filedialog.askopenfilename(title="Send a file to students")
        if not path:
            return
        try:
            size = os.path.getsize(path)
        except OSError as e:
            messagebox.showerror("Send file", f"Could not read the file:\n{e}")
            return
        if size > MAX_FILE_BYTES:
            messagebox.showerror("Send file", f"That file is {size // (1024 * 1024)} MB. "
                                 f"The limit is {MAX_FILE_BYTES // (1024 * 1024)} MB.")
            return
        try:
            with open(path, "rb") as f:
                data = base64.b64encode(f.read()).decode("ascii")
        except OSError as e:
            messagebox.showerror("Send file", f"Could not read the file:\n{e}")
            return
        name = os.path.basename(path)
        self.backend.send_command(SEND_FILE, {"name": name, "data": data}, self._targets())
        self._log(f"Sending {name} to {self._target_text()}")

    def _lock(self):
        args = {}
        message = self.lock_message.get().strip()
        if message:
            args["message"] = message
        self.backend.send_command(OVERLAY_LOCK, args, self._targets())
        self._log(f"Lock {self._target_text()}")

    def _unlock(self):
        self.backend.send_command(OVERLAY_UNLOCK, {}, self._targets())
        self._log(f"Unlock {self._target_text()}")

    def _windows_lock(self):
        if not messagebox.askyesno("Windows lock", f"Lock {self._target_text()} with the Windows lock screen?\n\n"
                                   "You can't unlock these from here: students need their Windows password."):
            return
        self.backend.send_command(LOCK_SCREEN, {}, self._targets())
        self._log(f"Windows lock {self._target_text()}")

    def _power(self, action, verb):
        if not messagebox.askyesno(verb, f"{verb} {self._target_text()}?\n\n"
                                   f"Students get a {SHUTDOWN_DELAY}-second warning to save their work.\n"
                                   "You can still cancel during the countdown.", icon="warning"):
            return
        self.backend.send_command(action, {"delay": SHUTDOWN_DELAY}, self._targets())
        self._log(f"{verb} {self._target_text()} in {SHUTDOWN_DELAY}s")

    def _cancel_shutdown(self):
        self.backend.send_command(CANCEL_SHUTDOWN, {}, self._targets())
        self._log(f"Cancel shutdown on {self._target_text()}")

    def _watch(self):
        if self.monitor_window is not None:
            self.monitor_window.focus()
            return
        if not self.agents:
            self._log("No PCs are connected to watch")
            return
        targets = self._targets()
        self.monitor_window = MonitorWindow(self, targets)
        self._log(f"Watching {self._target_text()}")

    def _remove_pcs(self):
        selected = list(self.tree.selection())
        if not selected:
            self._log("Select the PC(s) to remove first")
            return
        if not messagebox.askyesno("Remove PCs", f"Remove {len(selected)} PC(s)?\n\n"
                                   "They'll be disconnected and will have to be approved again to rejoin."):
            return
        self.backend.pair("remove", selected)
        self._log(f"Removing {len(selected)} PC(s)")

    def _approve(self, agent_ids):
        self.backend.pair("approve", agent_ids)
        self._log(f"Approved {len(agent_ids)} PC(s)")

    def _deny(self, agent_ids):
        self.backend.pair("deny", agent_ids)
        self._log(f"Denied {len(agent_ids)} PC(s)")

    # ---- events from the network thread ----

    def _poll(self):
        try:
            while True:
                kind, data = self.backend.events.get_nowait()
                handler = getattr(self, f"_on_{kind}", None)
                if handler:
                    handler(data)
        except queue.Empty:
            pass
        self.root.after(POLL_MS, self._poll)

    def _on_connected(self, class_code):
        self.conn_label.configure(text="● Online", text_color="green")
        self._log("Connected to hub")
        # The hub forgets watch state across a reconnect, so ask again.
        if self.monitor_window is not None:
            self.backend.watch(self.monitor_window.targets)

    def _on_disconnected(self, _):
        self.conn_label.configure(text="● Reconnecting…", text_color="orange")
        self._log("Lost connection to hub, reconnecting…")

    def _on_fatal(self, text):
        self.conn_label.configure(text="● Error", text_color="red")
        self._log(text)
        messagebox.showerror("SmartClass", text)

    def _on_log(self, text):
        self._log(text)

    def _on_agents(self, msg):
        new = {a["id"]: a for a in msg.get("agents", [])}
        for aid in set(self.agents) - set(new):
            self._log(f"{self.agents[aid]['name']} disconnected")
        for aid in set(new) - set(self.agents):
            self._log(f"{new[aid]['name']} connected from {new[aid]['address']}")
        self.agents = new
        self._refresh_tree()
        if self.monitor_window is not None:
            self.monitor_window.sync_agents(new)

    def _on_frame(self, msg):
        if self.monitor_window is not None:
            self.monitor_window.on_frame(msg)

    def _on_pending(self, msg):
        new = {p["id"]: p for p in msg.get("pending", [])}
        for aid in set(new) - set(self.pending):
            self._log(f"{new[aid]['name']} ({new[aid]['address']}) wants to join")
        self.pending = new
        self._refresh_pending()

    def _on_ack(self, msg):
        missing = msg.get("missing") or []
        if missing:
            self._log(f"{len(missing)} PC(s) were offline and did not get the command")
        if not msg.get("sent") and not missing:
            self._log("No PCs are connected")

    def _on_result(self, msg):
        aid = msg.get("agent_id")
        status, detail = msg.get("status"), msg.get("detail") or ""
        text = "✓ done" if status == OK else f"✗ {status}: {detail}"
        self.last_result[aid] = f"{time.strftime('%H:%M')}  {text}"
        if status != OK:
            name = self.agents.get(aid, {}).get("name", aid)
            self._log(f"{name}: {status}: {detail}")
        self._refresh_tree()

    # ---- helpers ----

    def _refresh_tree(self):
        selection = set(self.tree.selection())
        self.tree.delete(*self.tree.get_children())
        for aid, info in sorted(self.agents.items(), key=lambda kv: kv[1]["name"].lower()):
            self.tree.insert("", tk.END, iid=aid, values=(
                info["name"], _state_text(info.get("state") or {}), info["address"], info["version"],
                self.last_result.get(aid, ""),
            ))
        self.tree.selection_set([aid for aid in selection if aid in self.agents])
        self.count_label.configure(text=f"Connected PCs ({len(self.agents)})")
        self._update_target_label()

    def _refresh_pending(self):
        for child in self.pending_frame.winfo_children():
            child.destroy()
        if not self.pending:
            self.pending_frame.pack_forget()
            return
        self.pending_frame.pack(fill=tk.X, padx=6, pady=(6, 0), before=self.count_label)
        ids = list(self.pending)
        head = ctk.CTkFrame(self.pending_frame, fg_color="transparent")
        head.pack(fill=tk.X, padx=8, pady=(8, 4))
        ctk.CTkLabel(head, text=f"⏳ {len(ids)} PC(s) want to join",
                     font=ctk.CTkFont(size=14, weight="bold")).pack(side=tk.LEFT)
        ctk.CTkButton(head, text="Deny all", width=80, fg_color="gray",
                      command=lambda: self._deny(ids)).pack(side=tk.RIGHT, padx=(6, 0))
        ctk.CTkButton(head, text="Approve all", width=100,
                      command=lambda: self._approve(ids)).pack(side=tk.RIGHT)
        for aid, info in self.pending.items():
            row = ctk.CTkFrame(self.pending_frame, fg_color="transparent")
            row.pack(fill=tk.X, padx=8, pady=2)
            ctk.CTkLabel(row, text=f"{info['name']}  ·  {info['address']}", anchor="w").pack(side=tk.LEFT)
            ctk.CTkButton(row, text="Deny", width=70, fg_color="gray",
                          command=lambda a=aid: self._deny([a])).pack(side=tk.RIGHT, padx=(6, 0))
            ctk.CTkButton(row, text="Approve", width=90,
                          command=lambda a=aid: self._approve([a])).pack(side=tk.RIGHT)

    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log_box.configure(state="disabled")
        self.log_box.see(tk.END)


class MonitorWindow:
    """A live grid of screen thumbnails for the watched PCs.

    Opening it asks the hub to start streaming; closing it stops. Each watched
    PC shows the student a banner while this window is open.
    """

    def __init__(self, app, targets):
        self.app = app
        self.targets = targets  # "all" or a list of agent ids
        self.tiles = {}  # agent_id -> {"frame", "image", "caption", "photo"}

        self.win = ctk.CTkToplevel(app.root)
        self.win.title("Screen monitor")
        self.win.geometry("940x640")
        self.win.minsize(420, 320)
        self.win.protocol("WM_DELETE_WINDOW", self.close)

        note = ("Live view of the selected PCs. While this window is open, each PC "
                "shows its student a banner saying the screen is being viewed.")
        ctk.CTkLabel(self.win, text=note, wraplength=900, anchor="w",
                     justify=tk.LEFT).pack(fill=tk.X, padx=16, pady=(12, 0))
        self.grid = ctk.CTkScrollableFrame(self.win)
        self.grid.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        for col in range(MONITOR_COLUMNS):
            self.grid.grid_columnconfigure(col, weight=1)
        self.placeholder = ctk.CTkLabel(self.grid, text="Waiting for screens…")
        self.placeholder.grid(row=0, column=0, padx=10, pady=10)

        app.backend.watch(targets)
        self.win.after(200, self.win.lift)

    def focus(self):
        self.win.lift()
        self.win.focus_force()

    def on_frame(self, msg):
        aid = msg.get("agent_id")
        if aid is None:
            return
        name = self.app.agents.get(aid, {}).get("name", aid)
        try:
            raw = base64.b64decode(msg.get("image") or "")
            image = Image.open(io.BytesIO(raw))
            image.load()
        except Exception:
            return
        size = (msg.get("w") or image.width, msg.get("h") or image.height)
        photo = ctk.CTkImage(light_image=image, dark_image=image, size=size)

        tile = self.tiles.get(aid) or self._add_tile(aid)
        tile["photo"] = photo  # keep a reference so Tk doesn't drop the image
        tile["image"].configure(image=photo, text="")
        window = (msg.get("window") or "").strip()
        tile["caption"].configure(text=f"{name}\n{window}" if window else name)

    def sync_agents(self, agents):
        for aid in list(self.tiles):
            if aid not in agents:
                self._remove_tile(aid)

    def _add_tile(self, aid):
        self.placeholder.grid_forget()
        frame = ctk.CTkFrame(self.grid)
        image = ctk.CTkLabel(frame, text="…", width=self._approx_width(), height=180)
        image.pack(padx=6, pady=(6, 2))
        caption = ctk.CTkLabel(frame, text=aid, justify=tk.CENTER, wraplength=300)
        caption.pack(padx=6, pady=(0, 6))
        tile = {"frame": frame, "image": image, "caption": caption, "photo": None}
        self.tiles[aid] = tile
        self._relayout()
        return tile

    def _remove_tile(self, aid):
        tile = self.tiles.pop(aid, None)
        if tile:
            tile["frame"].destroy()
        self._relayout()

    def _relayout(self):
        for index, aid in enumerate(sorted(self.tiles)):
            self.tiles[aid]["frame"].grid(
                row=index // MONITOR_COLUMNS, column=index % MONITOR_COLUMNS, padx=8, pady=8, sticky="n")
        if not self.tiles:
            self.placeholder.grid(row=0, column=0, padx=10, pady=10)

    def _approx_width(self):
        return 300

    def close(self):
        self.app.backend.watch([])  # stop streaming (and the students' banners)
        self.app.monitor_window = None
        self.app._log("Stopped watching screens")
        self.win.destroy()


def _state_text(state):
    parts = []
    if state.get("locked"):
        parts.append("🔒 Locked")
    if state.get("power") == "shutdown":
        parts.append("⏻ Shutting down")
    elif state.get("power") == "restart":
        parts.append("↻ Restarting")
    return ", ".join(parts) or "Active"


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")

    backend = Backend(load_config())
    root = ctk.CTk()
    TeacherApp(root, backend)
    backend.start()
    root.mainloop()
