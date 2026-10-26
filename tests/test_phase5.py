import asyncio
import base64

import pytest

from smartclass.agent import actions
from smartclass.agent.client import Agent
from smartclass.common.protocol import ERROR, MESSAGE, OK, SEND_FILE, encode
from test_hub import CODE, recv_until, run, start_hub, teacher
from test_phase2 import FakeLock
from test_phase3 import FakeIndicator


@pytest.fixture(autouse=True)
def no_real_popups(monkeypatch):
    """Don't spawn Tk popup processes during tests."""
    calls = []
    monkeypatch.setattr(actions.popup, "show", lambda *a, **k: calls.append((a, k)))
    return calls


# ---- messages ----

def test_message_shows_a_popup(no_real_popups):
    assert actions.execute(MESSAGE, {"text": "Eyes up, please"}) == (OK, "shown")
    (title, body), _ = no_real_popups[0]
    assert body == "Eyes up, please" and "teacher" in title.lower()


def test_message_with_custom_title(no_real_popups):
    actions.execute(MESSAGE, {"text": "Break time", "title": "Notice"})
    (title, body), _ = no_real_popups[0]
    assert (title, body) == ("Notice", "Break time")


def test_message_requires_text():
    assert actions.execute(MESSAGE, {"text": "   "})[0] == ERROR


# ---- files ----

def test_send_file_saves_and_notifies(tmp_path, monkeypatch, no_real_popups):
    monkeypatch.setattr(actions, "received_files_dir", lambda: tmp_path / "recv")
    data = base64.b64encode(b"worksheet contents").decode()
    status, detail = actions.execute(SEND_FILE, {"name": "lesson.txt", "data": data})
    assert status == OK
    saved = tmp_path / "recv" / "lesson.txt"
    assert saved.read_bytes() == b"worksheet contents" and detail == str(saved)
    assert no_real_popups  # the student is told a file arrived


def test_send_file_avoids_overwriting(tmp_path, monkeypatch, no_real_popups):
    monkeypatch.setattr(actions, "received_files_dir", lambda: tmp_path)
    data = base64.b64encode(b"x").decode()
    actions.execute(SEND_FILE, {"name": "a.txt", "data": data})
    _, detail = actions.execute(SEND_FILE, {"name": "a.txt", "data": data})
    assert detail.endswith("a (1).txt")


def test_send_file_rejects_bad_base64(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "received_files_dir", lambda: tmp_path)
    assert actions.execute(SEND_FILE, {"name": "a.txt", "data": "not base64!!"})[0] == ERROR


def test_send_file_rejects_oversize(tmp_path, monkeypatch):
    monkeypatch.setattr(actions, "received_files_dir", lambda: tmp_path)
    monkeypatch.setattr(actions, "MAX_FILE_BYTES", 4)
    data = base64.b64encode(b"too many bytes").decode()
    assert actions.execute(SEND_FILE, {"name": "a.txt", "data": data})[0] == ERROR


@pytest.mark.parametrize("given,expected", [
    ("../../etc/passwd", "passwd"),
    ("a/b/c.txt", "c.txt"),
    ("C:\\Users\\x\\note.txt", "note.txt"),
    ("..hidden", "hidden"),
    ("", "file"),
])
def test_safe_name_strips_paths(given, expected):
    assert actions._safe_name(given) == expected


# ---- round trip through the hub ----

def test_file_reaches_a_real_agent(tmp_path, monkeypatch, no_real_popups):
    monkeypatch.setattr(actions, "received_files_dir", lambda: tmp_path / "recv")

    async def scenario():
        hub, url = await start_hub()
        cfg = {"hub_url": url, "class_code": CODE, "agent_id": "pc-1", "name": "PC 1"}
        task = asyncio.create_task(Agent(cfg, lock=FakeLock(), indicator=FakeIndicator()).run())
        t = await teacher(url)
        await recv_until(t, "agents", lambda m: m["agents"])

        payload = base64.b64encode(b"PDF-ish bytes " * 5000).decode()  # ~70 KB
        await t.send(encode("command", id="f1", targets="all", action=SEND_FILE,
                            args={"name": "handout.txt", "data": payload}))
        result = await recv_until(t, "result")
        assert result["status"] == OK
        assert (tmp_path / "recv" / "handout.txt").read_bytes() == b"PDF-ish bytes " * 5000

        task.cancel()
        await t.close()
        await hub.close()
    run(scenario())
