"""SmartClass teacher window."""

import logging
import queue
import time
import tkinter as tk
from tkinter import messagebox, ttk

import customtkinter as ctk

from ..common import config
from ..common.discovery import local_ip_addresses
from ..common.protocol import DEFAULT_HUB_PORT, LOCK_SCREEN, OK, OPEN_URL
from .backend import Backend

POLL_MS = 100


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
        self.last_result = {}  # agent_id -> text
        self._build()
        self.root.after(POLL_MS, self._poll)

    # ---- layout ----

    def _build(self):
        cfg = self.backend.cfg
        self.root.title("SmartClass Teacher")
        self.root.geometry("860x560")
        self.root.minsize(700, 450)

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
        self.count_label = ctk.CTkLabel(left, text="Connected PCs (0)", font=ctk.CTkFont(size=16, weight="bold"))
        self.count_label.pack(pady=(6, 4))

        columns = ("name", "address", "version", "result")
        self.tree = ttk.Treeview(left, columns=columns, show="headings", selectmode="extended", height=12)
        for col, title, width in (("name", "Computer", 180), ("address", "Address", 120),
                                  ("version", "Version", 70), ("result", "Last result", 220)):
            self.tree.heading(col, text=title)
            self.tree.column(col, width=width, anchor="w")
        self.tree.pack(fill=tk.BOTH, expand=True, padx=6)
        self.tree.bind("<<TreeviewSelect>>", lambda e: self._update_target_label())

        ctk.CTkLabel(left, text="Log", font=ctk.CTkFont(size=14)).pack(pady=(8, 2))
        self.log_box = ctk.CTkTextbox(left, height=110, state="disabled")
        self.log_box.pack(fill=tk.X, padx=6, pady=(0, 6))

        right = ctk.CTkFrame(body, width=230)
        right.pack(side=tk.RIGHT, fill=tk.Y, padx=(10, 0))
        right.pack_propagate(False)
        ctk.CTkLabel(right, text="Commands", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(6, 4))
        self.target_label = ctk.CTkLabel(right, text="", wraplength=200)
        self.target_label.pack(pady=(0, 8))
        ctk.CTkButton(right, text="Clear selection", fg_color="gray",
                      command=lambda: self.tree.selection_set(())).pack(padx=10, fill=tk.X)

        self.url_entry = ctk.CTkEntry(right, placeholder_text="Website, e.g. ielts.org")
        self.url_entry.pack(padx=10, pady=(16, 4), fill=tk.X)
        self.url_entry.bind("<Return>", lambda e: self._open_url())
        ctk.CTkButton(right, text="Open website", command=self._open_url).pack(padx=10, pady=4, fill=tk.X)
        ctk.CTkButton(right, text="Lock screens", command=self._lock).pack(padx=10, pady=(16, 4), fill=tk.X)

        self._update_target_label()

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

    def _lock(self):
        if not messagebox.askyesno("Lock screens", f"Lock {self._target_text()}?\n\n"
                                   "Students will need their Windows password to unlock."):
            return
        self.backend.send_command(LOCK_SCREEN, {}, self._targets())
        self._log(f"Lock {self._target_text()}")

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
                info["name"], info["address"], info["version"], self.last_result.get(aid, ""),
            ))
        self.tree.selection_set([aid for aid in selection if aid in self.agents])
        self.count_label.configure(text=f"Connected PCs ({len(self.agents)})")
        self._update_target_label()

    def _log(self, text):
        self.log_box.configure(state="normal")
        self.log_box.insert(tk.END, f"{time.strftime('%H:%M:%S')}  {text}\n")
        self.log_box.configure(state="disabled")
        self.log_box.see(tk.END)


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    ctk.set_appearance_mode("light")
    ctk.set_default_color_theme("blue")

    backend = Backend(load_config())
    root = ctk.CTk()
    TeacherApp(root, backend)
    backend.start()
    root.mainloop()
