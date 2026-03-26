import socket
import json
import time
import os
import webbrowser
import platform
import threading
import traceback

STUDENT_NAME = platform.node()

def send_heartbeat(sock):
    while True:
        try:
            heartbeat = {"type": "status", "status": "alive"}
            sock.sendall((json.dumps(heartbeat) + "\n").encode())
        except Exception:
            break
        time.sleep(5)

def execute_command(message):
    action = message['payload']['action']
    try:
        if action == "OPEN_URL":
            url = message['payload']['url']
            if not url.startswith("http"):
                url = "https://" + url
            webbrowser.open(url)
        elif action == "LOCK_SCREEN":
            if platform.system() == "Windows":
                os.system("rundll32.exe user32.dll,LockWorkStation")
            elif platform.system() == "Linux":
                os.system("gnome-screensaver-command -l")
        else:
            return "unknown_command"
        return "success"
    except Exception as e:
        return f"error: {str(e)}"

try:
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.connect(("127.0.0.1", 5000))

    register_msg = {"type": "register", "name": STUDENT_NAME}
    sock.sendall((json.dumps(register_msg) + "\n").encode())

    print(f"[INFO] Connected to server as {STUDENT_NAME}")

    threading.Thread(target=send_heartbeat, args=(sock,), daemon=True).start()

    buffer = ""
    while True:
        try:
            data = sock.recv(2048).decode()
            if not data:
                time.sleep(0.1)
                continue

            buffer += data
            while "\n" in buffer:
                line, buffer = buffer.split("\n", 1)
                message = json.loads(line)

                if message['type'] == "command":
                    status = execute_command(message)
                    result_msg = {
                        "type": "result",
                        "id": message['id'],
                        "payload": {"status": status}
                    }
                    sock.sendall((json.dumps(result_msg) + "\n").encode())

        except Exception:
            traceback.print_exc()
            time.sleep(1)

except Exception as e:
    print("[ERROR] Critical client error:", e)

finally:
    sock.close()
    print("[INFO] Connection closed.")