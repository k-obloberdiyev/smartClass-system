"""Agent connection loop: find the hub, stay connected, run commands."""

import asyncio
import logging
import platform

from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed, InvalidHandshake, InvalidURI

from .. import __version__
from ..common import discovery
from ..common.protocol import AGENT, CLOSE_AUTH_FAILED, ProtocolError, decode, encode
from . import actions

log = logging.getLogger("smartclass.agent")

MAX_BACKOFF = 30


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


async def run(cfg):
    """Keep a connection to the hub open forever, reconnecting with backoff."""
    backoff = 1
    while True:
        url = await resolve_hub(cfg)
        if url is None:
            log.info("No hub found on the local network; retrying in %ss", backoff)
        else:
            try:
                await _session(url, cfg)
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
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, MAX_BACKOFF)


async def _session(url, cfg):
    # proxy=None: school proxy settings must not intercept LAN connections.
    async with connect(url, open_timeout=10, ping_interval=10, ping_timeout=20, proxy=None) as ws:
        await ws.send(hello_message(cfg))
        welcome = decode(await asyncio.wait_for(ws.recv(), 10))
        if welcome["type"] != "welcome":
            raise ProtocolError(f"expected welcome, got {welcome['type']!r}")
        log.info("Connected to hub %s", url)

        async for raw in ws:
            try:
                msg = decode(raw)
            except ProtocolError as e:
                log.warning("Ignoring bad message: %s", e)
                continue
            if msg["type"] == "command":
                # Actions may block (opening a browser), so keep them off the event loop.
                status, detail = await asyncio.to_thread(actions.execute, msg.get("action"), msg.get("args"))
                log.info("%s -> %s %s", msg.get("action"), status, detail)
                await ws.send(encode("result", id=msg.get("id"), status=status, detail=detail))
