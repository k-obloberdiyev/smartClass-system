"""Networking for the teacher app, running on its own asyncio thread.

The GUI talks to it with send_command() and reads events from `events`
(a thread-safe queue of (kind, data) tuples).
"""

import asyncio
import logging
import queue
import threading

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from ..common.protocol import (
    CLOSE_AUTH_FAILED,
    DISCOVERY_PORT,
    TEACHER,
    ProtocolError,
    decode,
    encode,
    new_id,
)
from ..hub import Hub

log = logging.getLogger("smartclass.teacher")

MAX_BACKOFF = 10


class Backend:
    def __init__(self, cfg):
        """cfg: class_code, teacher_key, port, and optional hub_url for a remote hub."""
        self.cfg = cfg
        self.events = queue.Queue()
        self.hub = None
        self._loop = asyncio.new_event_loop()
        self._ws = None
        self._thread = threading.Thread(target=self._loop.run_forever, name="smartclass-net", daemon=True)

    @property
    def embedded(self):
        return not self.cfg.get("hub_url")

    def start(self):
        self._thread.start()
        asyncio.run_coroutine_threadsafe(self._main(), self._loop)

    def send_command(self, action, args=None, targets="all"):
        """Queue a command; returns its id. Thread-safe."""
        cmd_id = new_id()
        message = encode("command", id=cmd_id, targets=targets, action=action, args=args or {})
        asyncio.run_coroutine_threadsafe(self._send(message), self._loop)
        return cmd_id

    async def _send(self, message):
        if self._ws is None:
            self._emit("log", "Not connected to the hub; command not sent.")
            return
        try:
            await self._ws.send(message)
        except ConnectionClosed:
            self._emit("log", "Connection to the hub was lost; command not sent.")

    def _emit(self, kind, data=None):
        self.events.put((kind, data))

    async def _main(self):
        if self.embedded:
            self.hub = Hub(self.cfg["class_code"], self.cfg["teacher_key"],
                           port=self.cfg["port"], discovery_port=DISCOVERY_PORT)
            try:
                await self.hub.start()
            except OSError as e:
                self._emit("fatal", f"Could not start on port {self.cfg['port']}: {e}\n"
                                    "Is SmartClass Teacher already running?")
                return
            url = f"ws://127.0.0.1:{self.hub.port}"
        else:
            url = self.cfg["hub_url"]
        await self._connect_forever(url)

    async def _connect_forever(self, url):
        backoff = 1
        while True:
            try:
                async with connect(url, open_timeout=10, ping_interval=10, ping_timeout=20, proxy=None) as ws:
                    await ws.send(encode("hello", role=TEACHER, teacher_key=self.cfg["teacher_key"]))
                    welcome = decode(await asyncio.wait_for(ws.recv(), 10))
                    if welcome["type"] != "welcome":
                        raise ProtocolError(f"expected welcome, got {welcome['type']!r}")
                    self._ws = ws
                    backoff = 1
                    self._emit("connected", welcome.get("class_code"))
                    async for raw in ws:
                        try:
                            msg = decode(raw)
                        except ProtocolError as e:
                            log.warning("Ignoring bad message: %s", e)
                            continue
                        self._emit(msg["type"], msg)
            except ConnectionClosed as e:
                if e.rcvd and e.rcvd.code == CLOSE_AUTH_FAILED:
                    self._emit("fatal", "The hub rejected the teacher key.")
                    return
            except (OSError, asyncio.TimeoutError, InvalidHandshake, InvalidURI, ProtocolError) as e:
                log.info("Could not connect to %s: %s", url, e)
            finally:
                if self._ws is not None:
                    self._ws = None
                    self._emit("disconnected")
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)
