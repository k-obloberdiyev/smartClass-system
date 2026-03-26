import customtkinter as ctk
import tkinter as tk
from tkinter import ttk
import threading
import socket
import json
import uuid

clients = {}
clients_status = {}
clients_lock = threading.Lock()

# unique id for each command
def generate_command_id():
    return str(uuid.uuid4())

def update_status(text):
    status_box.configure(state="normal")
    status_box.insert(tk.END, text + "\n")
    status_box.configure(state="disabled")
    status_box.see(tk.END)

# updating student computer status
def refresh_student_list():
    for row in student_list.get_children():
        student_list.delete(row)

    with clients_lock:
        for sock, name in clients.items():
            status = clients_status.get(sock, "connected")
            student_list.insert("", tk.END, values=(name, status))

# sending commands to all students
def send_command(action, url=None):
    message = {
        "type": "command",
        "id": generate_command_id(),
        "payload": {"action": action}
    }
    if url:
        message["payload"]["url"] = url

    with clients_lock:
        dead_clients = []
        for client in clients:
            try:
                client.sendall((json.dumps(message) + "\n").encode())
                clients_status[client] = f"Sent {action}"
            except:
                dead_clients.append(client)

        for dc in dead_clients:
            name = clients.get(dc, "Unknown")
            update_status(f"[WARN] {name} disconnected unexpectedly")
            clients.pop(dc, None)
            clients_status.pop(dc, None)

    refresh_student_list()

def open_url_callback():
    url = url_entry.get().strip()
    if url:
        send_command("OPEN_URL", url)
        url_entry.delete(0, tk.END)

def lock_callback():
    send_command("LOCK_SCREEN")

def handle_client(client_sock, client_addr):
    try:
        data = client_sock.recv(2048).decode()
        reg = json.loads(data)
        if reg['type'] == 'register':
            name = reg['name']
            with clients_lock:
                clients[client_sock] = name
                clients_status[client_sock] = "connected"
            update_status(f"[INFO] {name} connected from {client_addr}")
            refresh_student_list()

        buffer = ""
        while True:
            data = client_sock.recv(2048)
            if not data:
                continue
            buffer += data.decode()
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                message = json.loads(line)
                if message['type'] == "result":
                    with clients_lock:
                        clients_status[client_sock] = message['payload']['status']
                    update_status(f"{clients[client_sock]} → {message['payload']['status']} (command {message['id']})")
                    refresh_student_list()
    except:
        pass
    finally:
        with clients_lock:
            if client_sock in clients:
                name = clients[client_sock]
                update_status(f"[INFO] {name} disconnected")
                clients.pop(client_sock, None)
            clients_status.pop(client_sock, None)
        refresh_student_list()
        client_sock.close()


def server_loop():
    server_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    server_sock.bind(("127.0.0.1", 5000))  
    server_sock.listen(5)
    update_status("[INFO] Server listening on 127.0.0.1:5000")

    while True:
        client_sock, client_addr = server_sock.accept()
        threading.Thread(target=handle_client, args=(client_sock, client_addr), daemon=True).start()


# Preparing GUI with tkinter

ctk.set_appearance_mode("light")
ctk.set_default_color_theme("blue")

root = ctk.CTk()
root.title("SmartClass Teacher")
root.geometry("750x500")

frame_left = ctk.CTkFrame(root)
frame_left.pack(side=ctk.LEFT, fill=tk.BOTH, expand=True, padx=10, pady=10)

frame_right = ctk.CTkFrame(root, width=200)
frame_right.pack(side=ctk.RIGHT, fill=tk.Y, padx=10, pady=10)

ctk.CTkLabel(frame_left, text="Connected Students", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(0,5))
student_list = ttk.Treeview(frame_left, columns=("Name", "Status"), show="headings", height=15)
student_list.heading("Name", text="Name")
student_list.heading("Status", text="Status")
student_list.column("Name", width=200)
student_list.column("Status", width=100)
student_list.pack(fill=tk.BOTH, expand=True)

ctk.CTkLabel(frame_left, text="Status Log", font=ctk.CTkFont(size=14)).pack(pady=(10,5))
status_box = ctk.CTkTextbox(frame_left, height=10, state="disabled")
status_box.pack(fill=tk.BOTH, expand=False)

ctk.CTkLabel(frame_right, text="Commands", font=ctk.CTkFont(size=16, weight="bold")).pack(pady=(0,10))
url_entry = ctk.CTkEntry(frame_right, placeholder_text="Enter URL")
url_entry.pack(pady=5, fill=tk.X)
ctk.CTkButton(frame_right, text="Open URL", command=open_url_callback).pack(pady=5, fill=tk.X)
ctk.CTkButton(frame_right, text="Lock All PCs", command=lock_callback).pack(pady=5, fill=tk.X)

threading.Thread(target=server_loop, daemon=True).start()

root.mainloop()