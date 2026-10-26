import asyncio
import base64
import io
import sys

import pytest
from PIL import Image

from smartclass.agent import capture, client, indicator
from smartclass.agent.client import Agent
from smartclass.common.protocol import OK, decode, encode
from smartclass.hub import Hub
from test_hub import CODE, KEY, recv_until, run, teacher
from test_phase2 import FakeLock


async def recv_until_type(ws, type_):
    while True:
        raw = await ws.recv()
        if decode(raw)["type"] == type_:
            return raw


class FakeIndicator:
    def __init__(self):
        self.shown = False
        self.ensured = 0

    def show(self):
        self.shown = True

    def hide(self):
        self.shown = False

    def ensure(self):
        self.ensured += 1


async def start_hub(interval=0.05):
    hub = Hub(CODE, KEY, host="127.0.0.1", port=0, monitor_interval=interval,
              thumb_max=200, thumb_quality=40)
    await hub.start()
    return hub, f"ws://127.0.0.1:{hub.port}"


async def raw_agent(url, agent_id="pc-1"):
    """A bare agent connection the test drives by hand."""
    from smartclass.common.protocol import AGENT
    from websockets.asyncio.client import connect

    ws = await connect(url, proxy=None)
    await ws.send(encode("hello", role=AGENT, class_code=CODE, agent_id=agent_id, name=agent_id))
    assert decode(await ws.recv())["type"] == "welcome"
    return ws


def agent_cfg(url, agent_id="pc-1"):
    return {"hub_url": url, "class_code": CODE, "agent_id": agent_id, "name": agent_id}


# ---- hub routing ----

def test_watch_all_starts_streaming_and_forwards_frames():
    async def scenario():
        hub, url = await start_hub()
        agent = await raw_agent(url, "pc-1")
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"

        await t.send(encode("watch", agents="all"))
        monitor = decode(await recv_until_type(agent, "monitor"))
        assert monitor["active"] is True and monitor["interval"] == 0.05
        assert monitor["max_px"] == 200 and monitor["quality"] == 40

        await agent.send(encode("frame", image="QUJD", format="jpeg", w=4, h=3, window="Notepad"))
        frame = await recv_until(t, "frame")
        assert frame["agent_id"] == "pc-1" and frame["image"] == "QUJD" and frame["window"] == "Notepad"

        await t.send(encode("watch", agents=[]))
        assert decode(await recv_until_type(agent, "monitor"))["active"] is False

        await agent.close()
        await t.close()
        await hub.close()
    run(scenario())


def test_watch_specific_pc_leaves_others_alone():
    async def scenario():
        hub, url = await start_hub()
        a1 = await raw_agent(url, "pc-1")
        a2 = await raw_agent(url, "pc-2")
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"

        await t.send(encode("watch", agents=["pc-1"]))
        assert decode(await recv_until_type(a1, "monitor"))["active"] is True
        # pc-2 is not watched, so it must never be told to stream.
        assert hub.agents["pc-2"].monitoring is False

        await a1.close()
        await a2.close()
        await t.close()
        await hub.close()
    run(scenario())


def test_teacher_disconnect_stops_streaming():
    async def scenario():
        hub, url = await start_hub()
        agent = await raw_agent(url, "pc-1")
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"

        await t.send(encode("watch", agents="all"))
        assert decode(await recv_until_type(agent, "monitor"))["active"] is True

        await t.close()
        assert decode(await recv_until_type(agent, "monitor"))["active"] is False
        assert hub.agents["pc-1"].monitoring is False

        await agent.close()
        await hub.close()
    run(scenario())


def test_streaming_resumes_when_a_watched_agent_reconnects():
    async def scenario():
        hub, url = await start_hub()
        t = await teacher(url)
        assert decode(await t.recv())["type"] == "welcome"
        await t.send(encode("watch", agents="all"))

        first = await raw_agent(url, "pc-1")
        assert decode(await recv_until_type(first, "monitor"))["active"] is True
        await first.close()
        await recv_until(t, "agents", lambda m: m["agents"] == [])

        # The teacher is still watching "all", so the reconnecting PC is told to stream.
        second = await raw_agent(url, "pc-1")
        assert decode(await recv_until_type(second, "monitor"))["active"] is True

        await second.close()
        await t.close()
        await hub.close()
    run(scenario())


def test_watchers_of_and_set_watch_are_derived_from_intent():
    hub = Hub(CODE, KEY, host="127.0.0.1", port=1)
    ws_all, ws_one, ws_none = object(), object(), object()
    hub._set_watch(ws_all, "all")
    hub._set_watch(ws_one, ["pc-2"])
    hub._set_watch(ws_none, [])
    assert set(hub._watchers_of("pc-1")) == {ws_all}
    assert set(hub._watchers_of("pc-2")) == {ws_all, ws_one}
    hub._set_watch(ws_all, None)  # None clears the intent
    assert hub._watchers_of("pc-1") == []


# ---- agent streaming + visible banner ----

def test_agent_streams_thumbnails_and_shows_banner(monkeypatch):
    monkeypatch.setattr(capture, "snapshot", lambda max_px, quality: (b"ABC", (4, 3), "Notepad"))

    async def scenario():
        hub, url = await start_hub()
        indicator = FakeIndicator()
        task = asyncio.create_task(Agent(agent_cfg(url), lock=FakeLock(), indicator=indicator).run())
        t = await teacher(url)
        await recv_until(t, "agents", lambda m: any(a["id"] == "pc-1" for a in m["agents"]))

        await t.send(encode("watch", agents="all"))
        frame = await recv_until(t, "frame")
        assert base64.b64decode(frame["image"]) == b"ABC"
        assert frame["window"] == "Notepad" and (frame["w"], frame["h"]) == (4, 3)
        assert indicator.shown is True

        await t.send(encode("watch", agents=[]))
        for _ in range(50):
            await asyncio.sleep(0.02)
            if not indicator.shown:
                break
        assert indicator.shown is False

        task.cancel()
        await t.close()
        await hub.close()
    run(scenario())


def test_agent_hides_banner_when_it_shuts_down(monkeypatch):
    monkeypatch.setattr(capture, "snapshot", lambda max_px, quality: (b"ABC", (4, 3), ""))

    async def scenario():
        hub, url = await start_hub()
        indicator = FakeIndicator()
        agent = Agent(agent_cfg(url), lock=FakeLock(), indicator=indicator)
        task = asyncio.create_task(agent.run())
        t = await teacher(url)
        await recv_until(t, "agents", lambda m: m["agents"])
        await t.send(encode("watch", agents="all"))
        await recv_until(t, "frame")
        assert indicator.shown is True

        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        assert indicator.shown is False

        await t.close()
        await hub.close()
    run(scenario())


# ---- capture helpers ----

def test_encode_downscales_and_produces_readable_jpeg():
    data, size = capture._encode(Image.new("RGB", (1000, 400), "navy"), max_px=320, quality=50)
    assert size == (320, 128)
    assert Image.open(io.BytesIO(data)).size == (320, 128)


def test_encode_leaves_small_images_alone():
    _, size = capture._encode(Image.new("RGB", (100, 80)), max_px=320, quality=50)
    assert size == (100, 80)


def test_foreground_window_is_empty_off_windows(monkeypatch):
    if sys.platform == "win32":
        pytest.skip("reads a real window title on Windows")
    assert capture.foreground_window() == ""


# ---- visible-banner process manager ----

def test_indicator_restarts_if_closed_and_hides_on_demand(monkeypatch):
    monkeypatch.setattr(indicator, "indicator_command",
                        lambda: [sys.executable, "-c", "import time; time.sleep(30)"])
    badge = indicator.MonitorIndicator()
    badge.show()
    first = badge._proc
    assert badge._running() and badge.showing

    first.kill()
    first.wait()
    badge.ensure()
    assert badge._running() and badge._proc is not first

    second = badge._proc
    badge.hide()
    assert second.poll() is not None and not badge.showing
    # ensure() does nothing once hidden
    badge.ensure()
    assert badge._proc is None
