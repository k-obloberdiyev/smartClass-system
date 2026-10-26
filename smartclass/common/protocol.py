"""Wire protocol shared by the hub, the teacher app and the student agent.

Every message is a single JSON object sent as one WebSocket text frame:

    {"v": 1, "type": "<type>", ...fields}

Connection flow:
    client -> hub   hello    (role = "agent" or "teacher", plus credentials)
    hub -> client   welcome  (or the connection is closed with a CLOSE_* code)
                             an agent's welcome carries status "active" or
                             "pending"; a pending agent waits for a "paired"
                             message before the teacher can control it.

Agent messages:
    hub -> agent    command  {id, action, args}
    agent -> hub    result   {id, status, detail}
    agent -> hub    state    {state: {locked: bool, power: "shutdown" | "restart" | null}}
    hub -> agent    monitor  {active: bool, interval, max_px, quality}
    agent -> hub    frame    {image (base64 JPEG), format, w, h, window, ts}

Teacher messages:
    teacher -> hub  command  {id, targets: "all" | [agent_id, ...], action, args}
    hub -> teacher  ack      {id, sent: [agent_id], missing: [agent_id]}
    hub -> teacher  result   {agent_id, id, status, detail}
    hub -> teacher  agents   {agents: [{id, name, os, version, address, state}]}
    teacher -> hub  watch    {agents: "all" | [agent_id, ...]}  (the full set to watch now)
    hub -> teacher  frame    {agent_id, image, format, w, h, window, ts}
    hub -> teacher  pending  {pending: [{id, name, os, version, address}]}  (awaiting approval)
    teacher -> hub  pair     {action: "approve" | "deny" | "remove", agents: [agent_id, ...]}
    hub -> agent    paired   {}  (the teacher approved this PC)

Screen monitoring is opt-in and visible: an agent only captures and streams its
screen while a teacher is watching it, and while it does it shows the student a
banner saying the screen is being viewed (see agent/indicator.py).
"""

import json
import uuid

PROTOCOL_VERSION = 1

DEFAULT_HUB_PORT = 8765
DISCOVERY_PORT = 8766

# Largest WebSocket frame we accept, so sent files (as base64) fit in one message.
MAX_WS_SIZE = 16 * 1024 * 1024
MAX_FILE_BYTES = 10 * 1024 * 1024  # biggest file the teacher may send to a PC

# Roles
AGENT = "agent"
TEACHER = "teacher"

# Actions
OPEN_URL = "OPEN_URL"
MESSAGE = "MESSAGE"  # args: {text, title}
SEND_FILE = "SEND_FILE"  # args: {name, data (base64)}
LOCK_SCREEN = "LOCK_SCREEN"  # the real Windows lock screen; needs the student's password
OVERLAY_LOCK = "OVERLAY_LOCK"  # args: {message}
OVERLAY_UNLOCK = "OVERLAY_UNLOCK"
SHUTDOWN = "SHUTDOWN"  # args: {delay}
RESTART = "RESTART"  # args: {delay}
CANCEL_SHUTDOWN = "CANCEL_SHUTDOWN"

# Screen monitoring defaults (the teacher/hub may override on the wire).
MONITOR_INTERVAL = 2.0  # seconds between thumbnails
THUMBNAIL_MAX = 320  # longest thumbnail edge, in pixels
THUMBNAIL_QUALITY = 50  # JPEG quality, 1-95

# Result statuses
OK = "ok"
ERROR = "error"
UNSUPPORTED = "unsupported"

# WebSocket close codes (4000-4999 are reserved for applications)
CLOSE_BAD_HELLO = 4000
CLOSE_AUTH_FAILED = 4001
CLOSE_REPLACED = 4002
CLOSE_DENIED = 4003  # the teacher denied or revoked this PC's pairing


class ProtocolError(Exception):
    pass


def new_id():
    return uuid.uuid4().hex


def encode(type_, **fields):
    return json.dumps({"v": PROTOCOL_VERSION, "type": type_, **fields})


def decode(raw):
    if isinstance(raw, bytes):
        raise ProtocolError("binary frames are not supported")
    try:
        msg = json.loads(raw)
    except ValueError as e:
        raise ProtocolError(f"invalid JSON: {e}") from None
    if not isinstance(msg, dict) or not isinstance(msg.get("type"), str):
        raise ProtocolError("message has no type")
    if msg.get("v") != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {msg.get('v')!r}")
    return msg
