"""Run the hub on its own, e.g. on a server both teacher and students can reach.

    python -m smartclass.hub [--port 8765]

The class code and teacher key are created on first run and stored in hub.json.
"""

import argparse
import asyncio
import logging

from ..common import config
from ..common.protocol import DEFAULT_HUB_PORT, DISCOVERY_PORT
from .server import Hub


def main():
    parser = argparse.ArgumentParser(description="SmartClass hub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_HUB_PORT)
    parser.add_argument("--no-discovery", action="store_true", help="don't answer LAN discovery probes")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = config.load("hub")
    if not cfg.get("class_code") or not cfg.get("teacher_key"):
        cfg.setdefault("class_code", config.generate_class_code())
        cfg.setdefault("teacher_key", config.generate_secret())
        config.save("hub", cfg)

    print(f"Class code:  {cfg['class_code']}")
    print(f"Teacher key: {cfg['teacher_key']}")
    print(f"Settings:    {config.app_dir() / 'hub.json'}")

    hub = Hub(cfg["class_code"], cfg["teacher_key"], host=args.host, port=args.port,
              discovery_port=None if args.no_discovery else DISCOVERY_PORT)
    try:
        asyncio.run(hub.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
