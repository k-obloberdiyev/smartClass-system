import socket
import threading
import json

clients = {}
clients_lock = threading.Lock()

def server_input():
    while True:
        message = {}
        inp = input("Enter command for students: ")
        if(inp == 'lock'):
            message = {
                'type': 'command',
                'do': 'lock'
            }
        elif(inp == 'shutdown'):
            message = {
                'type': 'command',
                'do': 'shutdown'
            }
        elif(inp.startswith('ielts')):
            url = inp.split()[1]
            message = {
                'type': 'command',
                'do': 'OPEN_IELTS',
                'url': url
            }
        else: 
            print('Error\n')
            continue

        with clients_lock:
            for client in clients:
                client.sendall((json.dumps(message).encode()))

def handle_client(client_sock, client_addr):

    reg = client_sock.recv(2048)

    d = json.loads(reg.decode())
    if d['type'] == 'reg':
        name = d['name']

        with clients_lock:
            clients[client_sock] = name

        print(f"{name} connected from {client_addr}")

        try:
            while True:
                data = client_sock.recv(2048)
                if not data:
                    break
        finally:
            with clients_lock:
                if client_sock in clients:
                    del clients[client_sock]

            client_sock.close()
            print(f"{name} disconnected")

serv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, proto=0)
serv_sock.bind(('127.0.0.1', 5000))
serv_sock.listen(10)

print("Server is listening...")

threading.Thread(target=server_input, daemon=True).start()

while True:
    client_sock, client_addr = serv_sock.accept()
    thread = threading.Thread(target=handle_client, args=(client_sock, client_addr))
    thread.start()
