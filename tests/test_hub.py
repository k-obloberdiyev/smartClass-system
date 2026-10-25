import asyncio
import json
import socket

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from smartclass.agent import actions
from smartclass.agent.__main__ import _normalize_hub_url
from smartclass.agent.client import run as run_agent
from smartclass.common import discovery
from smartclass.common.protocol import (
    AGENT, CLOSE_AUTH_FAILED, CLOSE_BAD_HELLO, CLOSE_REPLACED, ERROR, OK, OPEN_URL,
    TEACHER, UNSUPPORTED, decode, encode,
)
from smartclass.hub import Hub

CODE = "ABCD-2345"
KEY = "teacher-secret"


def run(coro):
    return asyncio.run(asyncio.wait_for(coro, 10))


async def start_hub():
    hub = Hub(CODE, KEY, host="127.0.0.1", port=0)
    await hub.start()
    return hub, f"ws://127.0.0.1:{hub.port}"


async def recv_until(ws, type_, predicate=lambda m: True):
    while True:
        msg = decode(await ws.recv())
        if msg["type"] == type_ and predicate(msg):
            return msg


async def teacher(url, key=KEY):
    ws = await connect(url, proxy=None)
    await ws.send(encode("hello", role=TEACHER, teacher_key=key))
    return ws


async def fake_agent(url, agent_id="a1", code=CODE):
    ws = await connect(url, proxy=None)
    await ws.send(encode("hello", role=AGENT, class_code=code, agent_id=agent_id, name=agent_id))
    return ws


async def expect_close(ws, code):
    with pytest.raises(ConnectionClosed) as info:
        await ws.recv()
    assert info.value.rcvd.code == code


def test_agent_with_wrong_class_code_is_rejected():
    async def scenario():
        hub, url = await start_hub()
        ws = await fake_agent(url, code="WRONG-CODE")
        await expect_close(ws, CLOSE_AUTH_FAILED)
        assert hub.agents == {}
        await hub.close()
    run(scenario())


def test_class_code_is_case_insensitive():
    async def scenario():
        hub, url = await start_hub()
        ws = await fake_agent(url, code=" abcd-2345 ")
        assert decode(await ws.recv())["type"] == "welcome"
        await ws.close()
        await hub.close()
    run(scenario())


def test_teacher_with_wrong_key_is_rejected():
    async def scenario():
        hub, url = await start_hub()
        ws = await teacher(url, key="nope")
        await expect_close(ws, CLOSE_AUTH_FAILED)
        await hub.close()
    run(scenario())


def test_garbage_hello_is_rejected():
    async def scenario():
        hub, url = await start_hub()
        ws = await connect(url, proxy=None)
        await ws.send("not json")
        await expect_close(ws, CLOSE_BAD_HELLO)
        await hub.close()
    run(scenario())


def test_command_round_trip_with_real_agent(tmp_path, monkeypatch):
    async def scenario():
        hub, url = await start_hub()
        agent_task = asyncio.create_task(run_agent({"hub_url": url, "class_code": CODE, "agent_id": "pc-1", "name": "PC 1"}))

        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"
        await recv_until(t, "agents", lambda m: any(a["id"] == "pc-1" for a in m["agents"]))

        # A non-web URL is refused by the agent, so no browser opens during tests.
        await t.send(encode("command", id="c1", targets=["pc-1", "ghost"], action=OPEN_URL, args={"url": "file:///etc/passwd"}))
        ack = await recv_until(t, "ack")
        assert ack == {**ack, "id": "c1", "sent": ["pc-1"], "missing": ["ghost"]}
        result = await recv_until(t, "result")
        assert (result["agent_id"], result["id"], result["status"]) == ("pc-1", "c1", ERROR)

        await t.send(encode("command", id="c2", targets="all", action="SELF_DESTRUCT"))
        result = await recv_until(t, "result")
        assert (result["id"], result["status"]) == ("c2", UNSUPPORTED)

        agent_task.cancel()
        await recv_until(t, "agents", lambda m: m["agents"] == [])
        await t.close()
        await hub.close()
    run(scenario())


def test_agent_reconnecting_replaces_old_connection():
    async def scenario():
        hub, url = await start_hub()
        first = await fake_agent(url, "pc-1")
        assert decode(await first.recv())["type"] == "welcome"
        second = await fake_agent(url, "pc-1")
        assert decode(await second.recv())["type"] == "welcome"
        await expect_close(first, CLOSE_REPLACED)
        await asyncio.sleep(0.05)
        assert list(hub.agents) == ["pc-1"] and hub.agents["pc-1"].ws is not None
        await second.close()
        await hub.close()
    run(scenario())


def test_lan_discovery_finds_hub():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]

    async def scenario():
        transport = await discovery.start_responder(hub_port=9999, name="test", port=port)
        try:
            url = await asyncio.to_thread(discovery.discover, 2.0, port, "127.0.0.1")
        finally:
            transport.close()
        assert url == "ws://127.0.0.1:9999"
    run(scenario())


@pytest.mark.parametrize("url", ["", "file:///C:/Windows", "javascript:alert(1)", "ftp://x.org"])
def test_open_url_rejects_non_web_addresses(url, monkeypatch):
    monkeypatch.setattr(actions.webbrowser, "open", lambda u: pytest.fail(f"opened {u}"))
    status, _ = actions.execute(OPEN_URL, {"url": url})
    assert status == ERROR


def test_open_url_adds_https(monkeypatch):
    opened = []
    monkeypatch.setattr(actions.webbrowser, "open", lambda u: opened.append(u) or True)
    assert actions.execute(OPEN_URL, {"url": "ielts.org"}) == (OK, "https://ielts.org")
    assert actions.execute(OPEN_URL, {"url": "localhost:8000/test"}) == (OK, "https://localhost:8000/test")
    assert opened == ["https://ielts.org", "https://localhost:8000/test"]


@pytest.mark.parametrize("given,expected", [
    ("192.168.1.5", "ws://192.168.1.5:8765"),
    ("192.168.1.5:9000", "ws://192.168.1.5:9000"),
    ("ws://hub.example.com:8765", "ws://hub.example.com:8765"),
    ("", ""),
])
def test_normalize_hub_url(given, expected):
    assert _normalize_hub_url(given) == expected
