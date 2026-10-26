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
    if args.reset_pin and cfg.pop("hub_fingerprint", None) is not None:
        changed = True
    if changed:
        config.save("agent", cfg)
    return cfg


def _normalize_hub_url(value):
    """Accept "host", "host:port" or a full ws(s):// URL; "" clears it (use discovery).

    A bare host becomes wss:// — the hub serves encrypted by default. Give an
    explicit ws:// URL to reach a hub started with --no-tls.
    """
    value = value.strip()
    if not value or "://" in value:
        return value
    if ":" not in value:
        value += ":8765"
    return "wss://" + value


def main():
    parser = argparse.ArgumentParser(description="SmartClass student agent")
    parser.add_argument("--code", help="class code shown in the teacher app")
    parser.add_argument("--hub", help='hub address, e.g. 192.168.1.20 or ws://host:8765 ("" = auto-discover)')
    parser.add_argument("--name", help="display name (default: computer name)")
    parser.add_argument("--reset-pin", action="store_true",
                        help="forget the remembered hub certificate (use after the hub is reinstalled)")
    # Internal: the agent re-runs itself with these to show the lock screen,
    # the "screen is being viewed" banner, or a notice popup.
    parser.add_argument("--overlay", help=argparse.SUPPRESS)
    parser.add_argument("--indicator", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--popup", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--title", help=argparse.SUPPRESS)
    parser.add_argument("--body", help=argparse.SUPPRESS)
    parser.add_argument("--open", dest="open_path", help=argparse.SUPPRESS)
    parser.add_argument("--parent", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args()

    if args.overlay is not None:
        from .overlay import run as run_overlay

        run_overlay(args.overlay, args.parent)
        return

    if args.indicator:
        from .indicator import run as run_indicator

        run_indicator(args.parent)
        return

    if args.popup:
        from .popup import run as run_popup

        run_popup(args.title, args.body or "", args.open_path)
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
