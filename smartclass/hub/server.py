"""The hub routes commands from teachers to agents and results back.

It runs inside the teacher app by default, or standalone (python -m
smartclass.hub) on a machine both sides can reach.
"""

import asyncio
import hmac
import logging
import time

from websockets.asyncio.server import broadcast, serve
from websockets.exceptions import ConnectionClosed

from ..common import discovery
from ..common.config import normalize_class_code
from ..common.protocol import (
    AGENT,
    CLOSE_AUTH_FAILED,
    CLOSE_BAD_HELLO,
    CLOSE_REPLACED,
    DEFAULT_HUB_PORT,
    TEACHER,
    ProtocolError,
    decode,
    encode,
    new_id,
)

log = logging.getLogger("smartclass.hub")

HELLO_TIMEOUT = 10


def _matches(given, expected):
    return hmac.compare_digest(str(given or "").encode(), expected.encode())


class _Agent:
    def __init__(self, ws, info):
        self.ws = ws
        self.info = info


class Hub:
    def __init__(self, class_code, teacher_key, host="0.0.0.0", port=DEFAULT_HUB_PORT,
                 discovery_port=None, name="SmartClass"):
        """discovery_port=None disables answering LAN discovery probes."""
        self.class_code = normalize_class_code(class_code)
        self.teacher_key = teacher_key
        self.host = host
        self.requested_port = port
        self.discovery_port = discovery_port
        self.name = name
        self.agents = {}  # agent_id -> _Agent
        self.teachers = set()
        self._server = None
        self._discovery = None

    @property
    def port(self):
        """The bound port (useful when started with port=0)."""
        return self._server.sockets[0].getsockname()[1]

    async def start(self):
        self._server = await serve(
            self._handle, self.host, self.requested_port,
            ping_interval=10, ping_timeout=20,
        )
        log.info("Hub listening on %s:%s", self.host, self.port)
        if self.discovery_port is not None:
            try:
                self._discovery = await discovery.start_responder(self.port, self.name, self.discovery_port)
            except OSError as e:
                log.warning("LAN discovery disabled: %s", e)

    async def close(self):
        if self._discovery:
            self._discovery.close()
        if self._server:
            self._server.close()
            await self._server.wait_closed()

    async def serve_forever(self):
        await self.start()
        await self._server.serve_forever()

    async def _handle(self, ws):
        try:
            hello = decode(await asyncio.wait_for(ws.recv(), HELLO_TIMEOUT))
        except (asyncio.TimeoutError, ProtocolError, ConnectionClosed):
            await ws.close(CLOSE_BAD_HELLO, "expected hello")
            return
        role = hello.get("role") if hello["type"] == "hello" else None
        if role == AGENT:
            await self._serve_agent(ws, hello)
        elif role == TEACHER:
            await self._serve_teacher(ws, hello)
        else:
            await ws.close(CLOSE_BAD_HELLO, "expected hello")

    # ---- agents ----

    async def _serve_agent(self, ws, hello):
        if not _matches(normalize_class_code(hello.get("class_code")), self.class_code):
            log.info("Rejected agent from %s: wrong class code", ws.remote_address)
            await ws.close(CLOSE_AUTH_FAILED, "wrong class code")
            return
        agent_id = str(hello.get("agent_id") or "")
        if not agent_id:
            await ws.close(CLOSE_BAD_HELLO, "missing agent_id")
            return

        entry = _Agent(ws, {
            "id": agent_id,
            "name": str(hello.get("name") or "Unknown PC"),
            "os": str(hello.get("os") or ""),
            "version": str(hello.get("version") or ""),
            "address": ws.remote_address[0] if ws.remote_address else "",
            "connected_at": time.time(),
            "state": {},
        })
        old = self.agents.get(agent_id)
        self.agents[agent_id] = entry
        if old:
            await old.ws.close(CLOSE_REPLACED, "replaced by a newer connection")
        log.info("Agent %s (%s) connected", entry.info["name"], entry.info["address"])
        await ws.send(encode("welcome"))
        self._notify_agents_changed()

        try:
            async for raw in ws:
                try:
                    msg = decode(raw)
                except ProtocolError as e:
                    log.warning("Bad message from agent %s: %s", agent_id, e)
                    continue
                if msg["type"] == "result":
                    self._to_teachers(encode(
                        "result", agent_id=agent_id, id=msg.get("id"),
                        status=msg.get("status"), detail=msg.get("detail"),
                    ))
                elif msg["type"] == "state" and isinstance(msg.get("state"), dict):
                    if msg["state"] != entry.info["state"]:
                        entry.info["state"] = msg["state"]
                        self._notify_agents_changed()
        except ConnectionClosed:
            pass
        finally:
            # A reconnecting agent may already have replaced this entry.
            if self.agents.get(agent_id) is entry:
                del self.agents[agent_id]
                log.info("Agent %s disconnected", entry.info["name"])
                self._notify_agents_changed()

    # ---- teachers ----

    async def _serve_teacher(self, ws, hello):
        if not _matches(hello.get("teacher_key"), self.teacher_key):
            log.info("Rejected teacher from %s: wrong key", ws.remote_address)
            await ws.close(CLOSE_AUTH_FAILED, "wrong teacher key")
            return
        self.teachers.add(ws)
        try:
            await ws.send(encode("welcome", class_code=self.class_code))
            await ws.send(self._agents_message())
            async for raw in ws:
                try:
                    msg = decode(raw)
                except ProtocolError as e:
                    log.warning("Bad message from teacher: %s", e)
                    continue
                if msg["type"] == "command":
                    await self._dispatch(ws, msg)
        except ConnectionClosed:
            pass
        finally:
            self.teachers.discard(ws)

    async def _dispatch(self, teacher_ws, msg):
        cmd_id = str(msg.get("id") or new_id())
        targets = msg.get("targets", "all")
        ids = list(self.agents) if targets == "all" else [str(t) for t in targets or []]
        present = [aid for aid in ids if aid in self.agents]
        missing = [aid for aid in ids if aid not in self.agents]

        command = encode("command", id=cmd_id, action=msg.get("action"), args=msg.get("args") or {})
        broadcast([self.agents[aid].ws for aid in present], command)
        await teacher_ws.send(encode("ack", id=cmd_id, sent=present, missing=missing))

    def _agents_message(self):
        return encode("agents", agents=[a.info for a in self.agents.values()])

    def _notify_agents_changed(self):
        self._to_teachers(self._agents_message())

    def _to_teachers(self, message):
        broadcast(self.teachers, message)
