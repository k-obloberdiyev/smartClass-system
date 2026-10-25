"""Agent connection loop: find the hub, stay connected, run commands."""

import asyncio
import logging
import platform
import time

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .. import __version__
from ..common import discovery
from ..common.protocol import (
    AGENT, CANCEL_SHUTDOWN, CLOSE_AUTH_FAILED, ERROR, OK, OVERLAY_LOCK, OVERLAY_UNLOCK,
    RESTART, SHUTDOWN, ProtocolError, decode, encode,
)
from . import actions
from .lock import DEFAULT_MESSAGE, OverlayLock

log = logging.getLogger("smartclass.agent")

MAX_BACKOFF = 30
WATCHDOG_INTERVAL = 1
# Unlock by itself if the teacher is gone this long, so a PC is never stuck locked.
ORPHAN_UNLOCK_AFTER = 120


async def resolve_hub(cfg):
    if cfg.get("hub_url"):
        return cfg["hub_url"]
    return await asyncio.to_thread(discovery.discover)


def hello_message(cfg):
    return encode(
        "hello",
        role=AGENT,
        class_code=cfg.get("class_code", ""),
        agent_id=cfg["agent_id"],
        name=cfg.get("name") or platform.node(),
        os=platform.platform(),
        version=__version__,
    )


class Agent:
    def __init__(self, cfg, lock=None):
        self.cfg = cfg
        self.lock = lock or OverlayLock()
        self.power = None  # "shutdown" / "restart" while a countdown is running
        self.disconnected_since = time.monotonic()

    def state(self):
        return {"locked": self.lock.locked, "power": self.power}

    async def run(self):
        """Keep a connection to the hub open forever, reconnecting with backoff."""
        watchdog = asyncio.create_task(self._watchdog())
        try:
            await self._connect_forever()
        finally:
            watchdog.cancel()
            await asyncio.to_thread(self.lock.unlock)

    async def _watchdog(self):
        while True:
            await asyncio.sleep(WATCHDOG_INTERVAL)
            offline = self.disconnected_since is not None and \
                time.monotonic() - self.disconnected_since > ORPHAN_UNLOCK_AFTER
            if self.lock.locked and offline:
                log.warning("No contact with the teacher for %ss; unlocking", ORPHAN_UNLOCK_AFTER)
                await asyncio.to_thread(self.lock.unlock)
            else:
                await asyncio.to_thread(self.lock.ensure)

    async def _connect_forever(self):
        backoff = 1
        while True:
            url = await resolve_hub(self.cfg)
            if url is None:
                log.info("No hub found on the local network; retrying in %ss", backoff)
            else:
                try:
                    await self._session(url)
                    backoff = 1  # we were connected; reconnect quickly
                except ConnectionClosed as e:
                    if e.rcvd and e.rcvd.code == CLOSE_AUTH_FAILED:
                        log.error("Hub rejected us: wrong class code")
                        backoff = MAX_BACKOFF
                    else:
                        log.info("Disconnected from hub")
                        backoff = 1
                except (OSError, asyncio.TimeoutError, InvalidHandshake, InvalidURI, ProtocolError) as e:
                    log.info("Could not connect to %s: %s", url, e)
                finally:
                    if self.disconnected_since is None:
                        self.disconnected_since = time.monotonic()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def _session(self, url):
        # proxy=None: school proxy settings must not intercept LAN connections.
        async with connect(url, open_timeout=10, ping_interval=10, ping_timeout=20, proxy=None) as ws:
            await ws.send(hello_message(self.cfg))
            welcome = decode(await asyncio.wait_for(ws.recv(), 10))
            if welcome["type"] != "welcome":
                raise ProtocolError(f"expected welcome, got {welcome['type']!r}")
            self.disconnected_since = None
            log.info("Connected to hub %s", url)
            await ws.send(encode("state", state=self.state()))

            async for raw in ws:
                try:
                    msg = decode(raw)
                except ProtocolError as e:
                    log.warning("Ignoring bad message: %s", e)
                    continue
                if msg["type"] == "command":
                    status, detail = await self._execute(msg.get("action"), msg.get("args") or {})
                    log.info("%s -> %s %s", msg.get("action"), status, detail)
                    await ws.send(encode("result", id=msg.get("id"), status=status, detail=detail))
                    await ws.send(encode("state", state=self.state()))

    async def _execute(self, action, args):
        # Everything below may block (starting processes, opening a browser),
        # so it runs off the event loop.
        if action in (OVERLAY_LOCK, OVERLAY_UNLOCK):
            try:
                if action == OVERLAY_LOCK:
                    await asyncio.to_thread(self.lock.lock, str(args.get("message") or DEFAULT_MESSAGE))
                    return OK, "locked"
                await asyncio.to_thread(self.lock.unlock)
                return OK, "unlocked"
            except OSError as e:
                return ERROR, f"could not start lock screen: {e}"

        status, detail = await asyncio.to_thread(actions.execute, action, args)
        if status == OK and action in (SHUTDOWN, RESTART):
            self.power = action.lower()
        elif status == OK and action == CANCEL_SHUTDOWN:
            self.power = None
        return status, detail


async def run(cfg):
    await Agent(cfg).run()
