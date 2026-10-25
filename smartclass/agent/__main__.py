"""Student agent.

    python -m smartclass.agent --code ABCD-2345 --hub ws://192.168.1.20:8765

--code and --hub are saved to agent.json, so later runs need no arguments.
Without a hub URL the agent looks for one on the local network.
"""

import argparse
import asyncio
import logging

from ..common import config
from ..common.protocol import new_id
from .client import run


def load_config(args):
    cfg = config.load("agent")
    changed = False
    if not cfg.get("agent_id"):
        cfg["agent_id"] = new_id()
        changed = True
    if args.code:
        cfg["class_code"] = config.normalize_class_code(args.code)
        changed = True
    if args.hub is not None:
        cfg["hub_url"] = _normalize_hub_url(args.hub)
        changed = True
    if args.name:
        cfg["name"] = args.name
        changed = True
    if changed:
        config.save("agent", cfg)
    return cfg


def _normalize_hub_url(value):
    """Accept "host", "host:port" or a full ws:// URL; "" clears it (use discovery)."""
    value = value.strip()
    if not value or "://" in value:
        return value
    if ":" not in value:
        value += ":8765"
    return "ws://" + value


def main():
    parser = argparse.ArgumentParser(description="SmartClass student agent")
    parser.add_argument("--code", help="class code shown in the teacher app")
    parser.add_argument("--hub", help='hub address, e.g. 192.168.1.20 or ws://host:8765 ("" = auto-discover)')
    parser.add_argument("--name", help="display name (default: computer name)")
    # Internal: the agent re-runs itself with these to show the lock screen.
    parser.add_argument("--overlay", help=argparse.SUPPRESS)
    parser.add_argument("--parent", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.overlay is not None:
        from .overlay import run as run_overlay

        run_overlay(args.overlay, args.parent)
        return

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    cfg = load_config(args)
    if not cfg.get("class_code"):
        parser.error("no class code yet: run once with --code XXXX-XXXX")

    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
