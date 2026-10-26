"""Agent connection loop: find the hub, stay connected, run commands."""

import asyncio
import base64
import logging
import platform
import time

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .. import __version__
from ..common import config, discovery, tls
from ..common.protocol import (
    AGENT, CANCEL_SHUTDOWN, CLOSE_AUTH_FAILED, CLOSE_DENIED, ERROR, MAX_WS_SIZE,
    MONITOR_INTERVAL, OK, OVERLAY_LOCK, OVERLAY_UNLOCK, RESTART, SHUTDOWN, THUMBNAIL_MAX,
    THUMBNAIL_QUALITY, ProtocolError, decode, encode,
)
from . import actions, capture
from .indicator import MonitorIndicator
from .lock import DEFAULT_MESSAGE, OverlayLock

log = logging.getLogger("smartclass.agent")

MAX_BACKOFF = 30
WATCHDOG_INTERVAL = 1
# Unlock by itself if the teacher is gone this long, so a PC is never stuck locked.
ORPHAN_UNLOCK_AFTER = 120


class PinMismatch(Exception):
    """The hub's TLS certificate changed since we first pinned it."""


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
    def __init__(self, cfg, lock=None, indicator=None):
        self.cfg = cfg
        self.lock = lock or OverlayLock()
        self.indicator = indicator or MonitorIndicator()
        self.power = None  # "shutdown" / "restart" while a countdown is running
        self.disconnected_since = time.monotonic()
        self.monitoring = False
        self._monitor_cfg = {}
        self._monitor_task = None

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
            await asyncio.to_thread(self.indicator.hide)

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
            if self.monitoring:
                await asyncio.to_thread(self.indicator.ensure)

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
                    code = e.rcvd.code if e.rcvd else None
                    if code == CLOSE_AUTH_FAILED:
                        log.error("Hub rejected us: wrong class code")
                        backoff = MAX_BACKOFF
                    elif code == CLOSE_DENIED:
                        log.warning("Teacher has not approved this PC yet; will keep trying")
                        backoff = MAX_BACKOFF
                    else:
                        log.info("Disconnected from hub")
                        backoff = 1
                except PinMismatch:
                    log.error("Hub certificate changed since first use (possible impersonation); "
                              "refusing to connect. Run the agent with --reset-pin if the hub was reinstalled.")
                    backoff = MAX_BACKOFF
                except (OSError, asyncio.TimeoutError, InvalidHandshake, InvalidURI, ProtocolError) as e:
                    log.info("Could not connect to %s: %s", url, e)
                finally:
                    if self.disconnected_since is None:
                        self.disconnected_since = time.monotonic()
            await asyncio.sleep(backoff)
            backoff = min(backoff * 2, MAX_BACKOFF)

    async def _session(self, url):
        ssl_context = tls.client_context() if url.startswith("wss://") else None
        # proxy=None: school proxy settings must not intercept LAN connections.
        async with connect(url, open_timeout=10, ping_interval=10, ping_timeout=20,
                           proxy=None, ssl=ssl_context, max_size=MAX_WS_SIZE) as ws:
            if ssl_context is not None:
                self._verify_pin(ws)
            await ws.send(hello_message(self.cfg))
            welcome = decode(await asyncio.wait_for(ws.recv(), 10))
            if welcome["type"] != "welcome":
                raise ProtocolError(f"expected welcome, got {welcome['type']!r}")
            if welcome.get("status") == "pending":
                log.info("Connected; waiting for the teacher to approve this PC")
                await self._await_pairing(ws)
            self.disconnected_since = None
            log.info("Connected to hub %s", url)
            await ws.send(encode("state", state=self.state()))

            try:
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
                    elif msg["type"] == "monitor":
                        await self._set_monitoring(ws, msg)
            finally:
                # No teacher can see us once the link drops, so the banner must go too.
                await self._stop_monitoring()

    def _verify_pin(self, ws):
        seen = tls.peer_fingerprint(ws)
        if seen is None:
            return
        verdict = tls.check_pin(self.cfg.get("hub_fingerprint"), seen)
        if verdict == "mismatch":
            raise PinMismatch()
        if verdict == "new":
            self.cfg["hub_fingerprint"] = seen
            if self.cfg.get("agent_id"):  # not a throwaway test config
                config.save("agent", self.cfg)
            log.info("Pinned the hub's certificate (first connection)")

    async def _await_pairing(self, ws):
        """Wait for the teacher's approval before becoming controllable."""
        while True:
            msg = decode(await ws.recv())
            if msg["type"] == "paired":
                log.info("Approved by the teacher")
                return

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

    # ---- screen monitoring ----

    async def _set_monitoring(self, ws, msg):
        if msg.get("active"):
            self._monitor_cfg = {
                "interval": float(msg.get("interval") or MONITOR_INTERVAL),
                "max_px": int(msg.get("max_px") or THUMBNAIL_MAX),
                "quality": int(msg.get("quality") or THUMBNAIL_QUALITY),
            }
            if not self.monitoring:
                self.monitoring = True
                await asyncio.to_thread(self.indicator.show)
                self._monitor_task = asyncio.create_task(self._capture_loop(ws))
        else:
            await self._stop_monitoring()

    async def _stop_monitoring(self):
        if self._monitor_task is not None:
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            self._monitor_task = None
        if self.monitoring:
            self.monitoring = False
            await asyncio.to_thread(self.indicator.hide)

    async def _capture_loop(self, ws):
        """While watched, send a screen thumbnail every interval seconds."""
        while True:
            cfg = self._monitor_cfg
            try:
                data, (w, h), window = await asyncio.to_thread(
                    capture.snapshot, cfg["max_px"], cfg["quality"])
            except Exception as e:  # capture can fail (no display, permissions)
                log.warning("Screen capture failed: %s", e)
            else:
                image = base64.b64encode(data).decode("ascii")
                try:
                    await ws.send(encode("frame", image=image, format="jpeg",
                                         w=w, h=h, window=window, ts=time.time()))
                except ConnectionClosed:
                    break
            await asyncio.sleep(cfg["interval"])


async def run(cfg):
    await Agent(cfg).run()
