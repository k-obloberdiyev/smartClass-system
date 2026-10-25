"""Find a hub on the local network with a UDP broadcast.

This only works when the agent and the hub share a subnet. Agents on other
networks must be given the hub URL explicitly (see agent --hub).
"""

import asyncio
import json
import socket

from .protocol import DISCOVERY_PORT

_PROBE = json.dumps({"t": "smartclass-discover", "v": 1}).encode()


class _Responder(asyncio.DatagramProtocol):
    def __init__(self, hub_port, name):
        self.hub_port = hub_port
        self.name = name
        self.transport = None

    def connection_made(self, transport):
        self.transport = transport

    def datagram_received(self, data, addr):
        try:
            msg = json.loads(data)
        except ValueError:
            return
        if isinstance(msg, dict) and msg.get("t") == "smartclass-discover":
            reply = {"t": "smartclass-hub", "v": 1, "port": self.hub_port, "name": self.name}
            self.transport.sendto(json.dumps(reply).encode(), addr)


async def start_responder(hub_port, name, port=DISCOVERY_PORT):
    """Answer discovery probes until the returned transport is closed."""
    loop = asyncio.get_running_loop()
    transport, _ = await loop.create_datagram_endpoint(
        lambda: _Responder(hub_port, name),
        local_addr=("0.0.0.0", port),
        allow_broadcast=True,
    )
    return transport


def discover(timeout=2.0, port=DISCOVERY_PORT, target="255.255.255.255"):
    """Broadcast a probe and return the first hub's ws:// URL, or None. Blocking."""
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        sock.settimeout(timeout)
        try:
            sock.sendto(_PROBE, (target, port))
            while True:
                data, (host, _) = sock.recvfrom(1024)
                try:
                    msg = json.loads(data)
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("t") == "smartclass-hub" and isinstance(msg.get("port"), int):
                    return f"ws://{host}:{msg['port']}"
        except OSError:  # includes socket.timeout
            return None


def local_ip_addresses():
    """Best-effort list of this machine's LAN IPv4 addresses, primary first."""
    addresses = []
    try:
        # No packet is sent; this just asks the OS which interface it would route through.
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.connect(("10.255.255.255", 1))
            addresses.append(sock.getsockname()[0])
    except OSError:
        pass
    try:
        for info in socket.getaddrinfo(socket.gethostname(), None, socket.AF_INET):
            ip = info[4][0]
            if ip not in addresses and not ip.startswith("127."):
                addresses.append(ip)
    except OSError:
        pass
    return addresses
