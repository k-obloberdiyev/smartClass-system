import asyncio
import ssl

import pytest
from websockets.asyncio.client import connect

from smartclass.agent.client import Agent
from smartclass.common import tls
from smartclass.common.protocol import CLOSE_DENIED, TEACHER, decode, encode
from smartclass.hub import Hub
from test_hub import CODE, KEY, expect_close, fake_agent, recv_until, run, teacher
from test_phase2 import FakeLock
from test_phase3 import FakeIndicator


async def start_approval_hub(approved=None):
    saved = []
    hub = Hub(CODE, KEY, host="127.0.0.1", port=0, require_approval=True,
              approved=approved or [], on_approved_change=saved.append)
    await hub.start()
    return hub, f"ws://127.0.0.1:{hub.port}", saved


async def recv_type(ws, type_):
    while True:
        msg = decode(await ws.recv())
        if msg["type"] == type_:
            return msg


# ---- pairing approval ----

def test_new_agent_waits_for_approval_then_joins():
    async def scenario():
        hub, url, saved = await start_approval_hub()
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"
        await recv_until(t, "pending", lambda m: m["pending"] == [])

        agent = await fake_agent(url, "pc-1")
        assert decode(await agent.recv()).get("status") == "pending"
        await recv_until(t, "pending", lambda m: [p["id"] for p in m["pending"]] == ["pc-1"])
        assert "pc-1" in hub.pending and "pc-1" not in hub.agents

        await t.send(encode("pair", action="approve", agents=["pc-1"]))
        assert (await recv_type(agent, "paired"))["type"] == "paired"
        # The hub clears it from pending, then announces it as an active agent.
        await recv_until(t, "pending", lambda m: m["pending"] == [])
        await recv_until(t, "agents", lambda m: [a["id"] for a in m["agents"]] == ["pc-1"])
        assert saved and saved[-1] == ["pc-1"]

        await agent.close()
        await t.close()
        await hub.close()
    run(scenario())


def test_agent_can_be_messaged_after_approval():
    """Regression: the hub must read from an agent once it's promoted to active."""
    async def scenario():
        hub, url, _ = await start_approval_hub()
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"
        agent = await fake_agent(url, "pc-1")
        assert decode(await agent.recv()).get("status") == "pending"
        await recv_until(t, "pending", lambda m: m["pending"])
        await t.send(encode("pair", action="approve", agents=["pc-1"]))
        await recv_type(agent, "paired")

        # The agent now sends up a result; the hub's active loop must receive and
        # forward it (this is the path the pending-drain once collided with).
        await agent.send(encode("result", id="x1", status="ok", detail="done"))
        result = await recv_until(t, "result")
        assert result["agent_id"] == "pc-1" and result["status"] == "ok"

        await agent.close()
        await t.close()
        await hub.close()
    run(scenario())


def test_denied_agent_is_disconnected():
    async def scenario():
        hub, url, _ = await start_approval_hub()
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"

        agent = await fake_agent(url, "pc-1")
        assert decode(await agent.recv()).get("status") == "pending"
        await recv_until(t, "pending", lambda m: m["pending"])

        await t.send(encode("pair", action="deny", agents=["pc-1"]))
        await expect_close(agent, CLOSE_DENIED)
        await recv_until(t, "pending", lambda m: m["pending"] == [])
        assert "pc-1" not in hub.pending and "pc-1" not in hub.agents

        await t.close()
        await hub.close()
    run(scenario())


def test_already_approved_agent_joins_without_waiting():
    async def scenario():
        hub, url, _ = await start_approval_hub(approved=["pc-1"])
        agent = await fake_agent(url, "pc-1")
        welcome = decode(await agent.recv())
        assert welcome["type"] == "welcome" and welcome.get("status") != "pending"
        await asyncio.sleep(0.05)
        assert "pc-1" in hub.agents and "pc-1" not in hub.pending

        await agent.close()
        await hub.close()
    run(scenario())


def test_reconnect_after_approval_is_direct():
    async def scenario():
        hub, url, _ = await start_approval_hub()
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"

        agent = await fake_agent(url, "pc-1")
        assert decode(await agent.recv()).get("status") == "pending"
        await recv_until(t, "pending", lambda m: m["pending"])
        await t.send(encode("pair", action="approve", agents=["pc-1"]))
        await recv_type(agent, "paired")
        await agent.close()

        again = await fake_agent(url, "pc-1")
        assert decode(await again.recv()).get("status") != "pending"

        await again.close()
        await t.close()
        await hub.close()
    run(scenario())


def test_remove_revokes_approval_and_disconnects():
    async def scenario():
        hub, url, saved = await start_approval_hub(approved=["pc-1"])
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"
        agent = await fake_agent(url, "pc-1")
        assert decode(await agent.recv()).get("status") != "pending"
        await recv_until(t, "agents", lambda m: m["agents"])

        await t.send(encode("pair", action="remove", agents=["pc-1"]))
        await expect_close(agent, CLOSE_DENIED)
        assert "pc-1" not in hub.approved
        await recv_until(t, "agents", lambda m: m["agents"] == [])
        assert saved[-1] == []

        await t.close()
        await hub.close()
    run(scenario())


# ---- TLS: cert + fingerprint pinning ----

def test_cert_is_generated_once_and_reused(tmp_path):
    cert, key = tls.ensure_hub_cert(tmp_path / "c.pem", tmp_path / "k.pem")
    assert cert.exists() and key.exists()
    fp = tls.certfile_fingerprint(cert)
    cert2, _ = tls.ensure_hub_cert(tmp_path / "c.pem", tmp_path / "k.pem")
    assert tls.certfile_fingerprint(cert2) == fp  # not regenerated
    tls.server_context(cert, key)  # loads the key pair without error
    assert tls.client_context().verify_mode == ssl.CERT_NONE


def test_check_pin_detects_change():
    assert tls.check_pin(None, "aa") == "new"
    assert tls.check_pin("aa", "aa") == "ok"
    assert tls.check_pin("aa", "bb") == "mismatch"


def test_agent_over_tls_pins_the_hub_certificate(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTCLASS_HOME", str(tmp_path))

    async def scenario():
        cert, key = tls.ensure_hub_cert(tmp_path / "c.pem", tmp_path / "k.pem")
        fingerprint = tls.certfile_fingerprint(cert)
        hub = Hub(CODE, KEY, host="127.0.0.1", port=0, ssl_context=tls.server_context(cert, key))
        await hub.start()
        url = f"wss://127.0.0.1:{hub.port}"

        cfg = {"hub_url": url, "class_code": CODE, "agent_id": "pc-1", "name": "PC 1"}
        task = asyncio.create_task(Agent(cfg, lock=FakeLock(), indicator=FakeIndicator()).run())

        ws = await connect(url, proxy=None, ssl=tls.client_context())
        await ws.send(encode("hello", role=TEACHER, teacher_key=KEY))
        assert decode(await ws.recv())["type"] == "welcome"
        await recv_until(ws, "agents", lambda m: any(a["id"] == "pc-1" for a in m["agents"]))

        assert cfg.get("hub_fingerprint") == fingerprint

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        await ws.close()
        await hub.close()
    run(scenario())
