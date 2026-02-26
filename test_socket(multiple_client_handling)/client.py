import socket
import os

client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_sock.connect(('127.0.0.1', 5000))

while True:
    data = client_sock.recv(2048)
    if not data:
        break
    
    command = data.decode()
    if command == 'LOCK': 
        os.system("rundll32.exe user32.dll,LockWorkStation")
    elif command == 'SHUTDOWN':
        os.system("shutdown /s /t 0")
    elif command.startswith('OPEN_IELTS'):
        os.system("start iexplore.exe PATH_TO_WEBSITE")
    