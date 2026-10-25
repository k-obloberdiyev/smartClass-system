"""Wire protocol shared by the hub, the teacher app and the student agent.

Every message is a single JSON object sent as one WebSocket text frame:

    {"v": 1, "type": "<type>", ...fields}

Connection flow:
    client -> hub   hello    (role = "agent" or "teacher", plus credentials)
    hub -> client   welcome  (or the connection is closed with a CLOSE_* code)

Agent messages:
    hub -> agent    command  {id, action, args}
    agent -> hub    result   {id, status, detail}

Teacher messages:
    teacher -> hub  command  {id, targets: "all" | [agent_id, ...], action, args}
    hub -> teacher  ack      {id, sent: [agent_id], missing: [agent_id]}
    hub -> teacher  result   {agent_id, id, status, detail}
    hub -> teacher  agents   {agents: [{id, name, os, version, address}]}
"""

import json
import uuid

PROTOCOL_VERSION = 1

DEFAULT_HUB_PORT = 8765
DISCOVERY_PORT = 8766

# Roles
AGENT = "agent"
TEACHER = "teacher"

# Actions
OPEN_URL = "OPEN_URL"
LOCK_SCREEN = "LOCK_SCREEN"

# Result statuses
OK = "ok"
ERROR = "error"
UNSUPPORTED = "unsupported"

# WebSocket close codes (4000-4999 are reserved for applications)
CLOSE_BAD_HELLO = 4000
CLOSE_AUTH_FAILED = 4001
CLOSE_REPLACED = 4002


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
