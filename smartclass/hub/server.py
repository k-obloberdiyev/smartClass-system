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
    CLOSE_DENIED,
    CLOSE_REPLACED,
    DEFAULT_HUB_PORT,
    MAX_WS_SIZE,
    MONITOR_INTERVAL,
    TEACHER,
    THUMBNAIL_MAX,
    THUMBNAIL_QUALITY,
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
        self.monitoring = False  # whether we've told this agent to stream its screen
        self.approval = None  # a Future, set by the teacher, while awaiting approval


class Hub:
    def __init__(self, class_code, teacher_key, host="0.0.0.0", port=DEFAULT_HUB_PORT,
                 discovery_port=None, name="SmartClass", monitor_interval=MONITOR_INTERVAL,
                 thumb_max=THUMBNAIL_MAX, thumb_quality=THUMBNAIL_QUALITY,
                 ssl_context=None, require_approval=False, approved=None,
                 on_approved_change=None):
        """discovery_port=None disables answering LAN discovery probes.

        require_approval: new PCs wait for the teacher before they can be
        controlled. approved: agent ids already paired. on_approved_change:
        called with the sorted id list whenever that set changes, to persist it.
        """
        self.class_code = normalize_class_code(class_code)
        self.teacher_key = teacher_key
        self.host = host
        self.requested_port = port
        self.discovery_port = discovery_port
        self.name = name
        self.monitor_interval = monitor_interval
        self.thumb_max = thumb_max
        self.thumb_quality = thumb_quality
        self.ssl_context = ssl_context
        self.require_approval = require_approval
        self.approved = set(approved or [])
        self._on_approved_change = on_approved_change
        self.agents = {}  # agent_id -> _Agent (paired and connected; controllable)
        self.pending = {}  # agent_id -> _Agent (connected, awaiting approval)
        self.teachers = set()
        self.watch_intent = {}  # teacher ws -> "all" | set(agent_id); which screens it watches
        self._server = None
        self._discovery = None

    @property
    def port(self):
        """The bound port (useful when started with port=0)."""
        return self._server.sockets[0].getsockname()[1]

    async def start(self):
        self._server = await serve(
            self._handle, self.host, self.requested_port,
            ping_interval=10, ping_timeout=20, ssl=self.ssl_context, max_size=MAX_WS_SIZE,
        )
        log.info("Hub listening on %s:%s (%s)", self.host, self.port,
                 "TLS" if self.ssl_context else "no TLS")
        if self.discovery_port is not None:
            try:
                self._discovery = await discovery.start_responder(
                    self.port, self.name, self.discovery_port, tls=self.ssl_context is not None)
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

        if self.require_approval and agent_id not in self.approved:
            if not await self._await_approval(agent_id, entry, ws):
                return  # denied or disconnected before approval
        else:
            await self._activate(agent_id, entry, paired=False)
        await self._run_active(agent_id, entry, ws)

    async def _await_approval(self, agent_id, entry, ws):
        """Hold a new agent as pending until the teacher approves it. True if approved."""
        old = self.pending.get(agent_id)
        self.pending[agent_id] = entry
        if old and old.approval and not old.approval.done():
            old.approval.set_result(False)  # a newer connection takes its place
        entry.approval = asyncio.get_running_loop().create_future()
        await ws.send(encode("welcome", status="pending"))
        log.info("Agent %s (%s) is waiting for approval", entry.info["name"], entry.info["address"])
        self._notify_pending_changed()

        drain = asyncio.create_task(self._drain(ws))
        try:
            await asyncio.wait({drain, entry.approval}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            # Fully stop the drain's recv() before _run_active starts its own —
            # websockets forbids two coroutines calling recv() at once.
            drain.cancel()
            try:
                await drain
            except asyncio.CancelledError:
                pass
            if self.pending.get(agent_id) is entry:
                del self.pending[agent_id]
                self._notify_pending_changed()

        if entry.approval.done() and entry.approval.result():
            await self._activate(agent_id, entry, paired=True)
            return True
        if entry.approval.done() and entry.approval.result() is False:
            await ws.close(CLOSE_DENIED, "not approved")
        return False

    async def _drain(self, ws):
        """Consume (and ignore) frames from a pending agent until it disconnects."""
        try:
            while True:
                await ws.recv()
        except ConnectionClosed:
            pass

    async def _activate(self, agent_id, entry, paired):
        old = self.agents.get(agent_id)
        self.agents[agent_id] = entry
        if old:
            await old.ws.close(CLOSE_REPLACED, "replaced by a newer connection")
        log.info("Agent %s (%s) connected", entry.info["name"], entry.info["address"])
        await entry.ws.send(encode("paired") if paired else encode("welcome"))
        self._notify_agents_changed()
        # A teacher may already be watching this PC (e.g. after it reconnects).
        await self._sync_monitoring()

    async def _run_active(self, agent_id, entry, ws):
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
                elif msg["type"] == "frame":
                    self._to_watchers(agent_id, encode(
                        "frame", agent_id=agent_id, image=msg.get("image"),
                        format=msg.get("format"), w=msg.get("w"), h=msg.get("h"),
                        window=msg.get("window"), ts=msg.get("ts"),
                    ))
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
            await ws.send(self._pending_message())
            async for raw in ws:
                try:
                    msg = decode(raw)
                except ProtocolError as e:
                    log.warning("Bad message from teacher: %s", e)
                    continue
                if msg["type"] == "command":
                    await self._dispatch(ws, msg)
                elif msg["type"] == "watch":
                    self._set_watch(ws, msg.get("agents"))
                    await self._sync_monitoring()
                elif msg["type"] == "pair":
                    await self._pair(msg.get("action"), msg.get("agents") or [])
        except ConnectionClosed:
            pass
        finally:
            self.teachers.discard(ws)
            if self.watch_intent.pop(ws, None) is not None:
                await self._sync_monitoring()

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

    # ---- pairing approval ----

    def _pending_message(self):
        return encode("pending", pending=[a.info for a in self.pending.values()])

    def _notify_pending_changed(self):
        self._to_teachers(self._pending_message())

    def _save_approved(self):
        if self._on_approved_change:
            self._on_approved_change(sorted(self.approved))

    async def _pair(self, action, agent_ids):
        for agent_id in (str(a) for a in agent_ids):
            if action == "approve":
                self.approved.add(agent_id)
                entry = self.pending.get(agent_id)
                if entry and entry.approval and not entry.approval.done():
                    entry.approval.set_result(True)  # its handler promotes it
            elif action == "deny":
                entry = self.pending.get(agent_id)
                if entry and entry.approval and not entry.approval.done():
                    entry.approval.set_result(False)
            elif action == "remove":
                self.approved.discard(agent_id)
                active = self.agents.get(agent_id)
                if active:
                    await active.ws.close(CLOSE_DENIED, "approval revoked")
        if action in ("approve", "remove"):
            self._save_approved()

    # ---- screen monitoring ----

    def _set_watch(self, ws, agents):
        if agents == "all":
            self.watch_intent[ws] = "all"
        elif isinstance(agents, list):
            self.watch_intent[ws] = {str(a) for a in agents}
        else:
            self.watch_intent.pop(ws, None)

    def _watchers_of(self, agent_id):
        return [ws for ws, intent in self.watch_intent.items()
                if intent == "all" or agent_id in intent]

    def _to_watchers(self, agent_id, message):
        broadcast(self._watchers_of(agent_id), message)

    async def _sync_monitoring(self):
        """Tell each agent to start or stop streaming as its watcher set changes."""
        for agent_id, agent in self.agents.items():
            wanted = bool(self._watchers_of(agent_id))
            if wanted == agent.monitoring:
                continue
            agent.monitoring = wanted
            if wanted:
                message = encode("monitor", active=True, interval=self.monitor_interval,
                                 max_px=self.thumb_max, quality=self.thumb_quality)
            else:
                message = encode("monitor", active=False)
            try:
                await agent.ws.send(message)
            except ConnectionClosed:
                pass
