import socket
import os
import json

client_sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
client_sock.connect(('127.0.0.1', 5000))

try:
    while True:
        data = client_sock.recv(2048)
        if not data:
            continue
        
        message = json.loads(data.decode())
        
        print(f"Received command: {message['do']}\n")

        if(message['type'] == 'command'):
            do = message['do']
            if(do == 'lock'): 
                os.system("rundll32.exe user32.dll,LockWorkStation")
            elif(do == 'shutdown'):
                os.system("shutdown /s /t 0")
            elif(do == 'OPEN_IELTS'):
                url = message['url']
                os.system("start iexplore.exe {url}")
except KeyboardInterrupt:
    client_sock.close()
    