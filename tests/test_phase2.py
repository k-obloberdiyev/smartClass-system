import asyncio
import subprocess
import sys

import pytest

from smartclass.agent import actions, client, lock
from smartclass.agent.client import Agent
from smartclass.common.protocol import (
    CANCEL_SHUTDOWN, OK, OVERLAY_LOCK, OVERLAY_UNLOCK, SHUTDOWN, UNSUPPORTED, encode,
)
from test_hub import CODE, recv_until, run, start_hub, teacher


class FakeLock:
    def __init__(self):
        self.message = None
        self.ensured = 0

    @property
    def locked(self):
        return self.message is not None

    def lock(self, message):
        self.message = message

    def unlock(self):
        self.message = None

    def ensure(self):
        self.ensured += 1


def agent_cfg(url, agent_id="pc-1"):
    return {"hub_url": url, "class_code": CODE, "agent_id": agent_id, "name": agent_id}


def test_lock_state_reaches_teacher():
    async def scenario():
        hub, url = await start_hub()
        fake = FakeLock()
        task = asyncio.create_task(Agent(agent_cfg(url), lock=fake).run())
        t = await teacher(url)
        await recv_until(t, "agents", lambda m: m["agents"] and m["agents"][0]["state"] == {"locked": False, "power": None})

        await t.send(encode("command", id="c1", targets="all", action=OVERLAY_LOCK, args={"message": "Look up"}))
        result = await recv_until(t, "result")
        assert result["status"] == OK and fake.message == "Look up"
        await recv_until(t, "agents", lambda m: m["agents"][0]["state"]["locked"] is True)

        await t.send(encode("command", id="c2", targets="all", action=OVERLAY_UNLOCK))
        await recv_until(t, "agents", lambda m: m["agents"][0]["state"]["locked"] is False)
        assert not fake.locked

        task.cancel()
        await t.close()
        await hub.close()
    run(scenario())


def test_power_commands_are_unsupported_off_windows():
    if sys.platform == "win32":
        pytest.skip("would really shut down")
    assert actions.execute(SHUTDOWN, {})[0] == UNSUPPORTED


@pytest.fixture
def fake_windows(monkeypatch):
    calls = []
    returncodes = []

    def fake_run(cmd, **kwargs):
        calls.append(cmd)
        return subprocess.CompletedProcess(cmd, returncodes.pop(0) if returncodes else 0, "", "")

    monkeypatch.setattr(actions.sys, "platform", "win32")
    monkeypatch.setattr(actions.subprocess, "run", fake_run)
    return calls, returncodes


def test_shutdown_runs_windows_shutdown_with_warning(fake_windows):
    calls, _ = fake_windows
    assert actions.execute(SHUTDOWN, {"delay": 60}) == (OK, "in 60s")
    cmd = calls[0]
    assert cmd[:4] == ["shutdown", "/s", "/t", "60"] and cmd[4] == "/c"


def test_shutdown_delay_is_clamped(fake_windows):
    calls, _ = fake_windows
    actions.execute(SHUTDOWN, {"delay": 99999})
    actions.execute(SHUTDOWN, {"delay": -5})
    assert [c[3] for c in calls] == ["600", "0"]


def test_cancel_with_nothing_pending_is_ok(fake_windows):
    calls, returncodes = fake_windows
    returncodes.append(actions.NO_SHUTDOWN_IN_PROGRESS)
    assert actions.execute(CANCEL_SHUTDOWN, {}) == (OK, "nothing to cancel")
    assert calls == [["shutdown", "/a"]]


def test_agent_tracks_power_state(fake_windows):
    async def scenario():
        agent = Agent({"agent_id": "x"}, lock=FakeLock())
        assert (await agent._execute(SHUTDOWN, {"delay": 60}))[0] == OK
        assert agent.state()["power"] == "shutdown"
        await agent._execute(CANCEL_SHUTDOWN, {})
        assert agent.state()["power"] is None
    run(scenario())


def test_overlay_lock_restarts_killed_overlay_and_unlocks(monkeypatch):
    monkeypatch.setattr(lock, "overlay_command", lambda message: [sys.executable, "-c", "import time; time.sleep(30)"])
    overlay = lock.OverlayLock()
    overlay.lock("hi")
    first = overlay._proc
    assert overlay._running()

    first.kill()
    first.wait()
    overlay.ensure()
    assert overlay._running() and overlay._proc is not first

    second = overlay._proc
    overlay.unlock()
    assert second.poll() is not None and not overlay.locked


def test_locked_pc_unlocks_itself_when_teacher_is_gone(monkeypatch):
    monkeypatch.setattr(client, "WATCHDOG_INTERVAL", 0.05)
    monkeypatch.setattr(client, "ORPHAN_UNLOCK_AFTER", 0.2)

    async def scenario():
        fake = FakeLock()
        fake.lock("hi")
        agent = Agent({"hub_url": "ws://127.0.0.1:9", "class_code": CODE, "agent_id": "x"}, lock=fake)
        task = asyncio.create_task(agent.run())
        await asyncio.sleep(0.5)
        assert not fake.locked
        task.cancel()
    run(scenario())


def test_overlay_exits_when_agent_dies():
    """Run the real Tk overlay with a parent that dies; it must close by itself."""
    try:
        import tkinter

        tkinter.Tk().destroy()
    except Exception:
        pytest.skip("no display")
    parent = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    overlay = subprocess.Popen([sys.executable, "-m", "smartclass.agent", "--overlay", "test", "--parent", str(parent.pid)])
    try:
        parent.wait()
        assert overlay.wait(timeout=5) == 0
    finally:
        overlay.kill()
