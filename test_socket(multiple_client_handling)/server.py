import socket
import threading

def handle_client(client_sock, client_addr):
    print("New Connection from", client_addr)
    while True:
        data = client_sock.recv(2048)
        if not data:
            break
        print(f"Received from {client_addr}: {data.decode()}")
        # Echo
        client_sock.sendall(data)
    client_sock.close()
    print(f"Connection closed {client_addr}")

serv_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM, proto=0)
serv_sock.bind(('127.0.0.1', 5000))
serv_sock.listen(10)

print("Server is listening...")

while True:
    client_sock, client_addr = serv_sock.accept()
    thread = threading.Thread(target=handle_client, args=(client_sock, client_addr))
    thread.start()
