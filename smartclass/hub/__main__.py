"""Run the hub on its own, e.g. on a server both teacher and students can reach.

    python -m smartclass.hub [--port 8765]

The class code and teacher key are created on first run and stored in hub.json.
"""

import argparse
import asyncio
import logging

from ..common import config, tls
from ..common.protocol import DEFAULT_HUB_PORT, DISCOVERY_PORT
from .server import Hub


def main():
    parser = argparse.ArgumentParser(description="SmartClass hub")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=DEFAULT_HUB_PORT)
    parser.add_argument("--no-discovery", action="store_true", help="don't answer LAN discovery probes")
    parser.add_argument("--no-tls", action="store_true", help="serve ws:// instead of encrypted wss://")
    parser.add_argument("--no-approval", action="store_true", help="let any PC with the class code join without approval")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")

    cfg = config.load("hub")
    if not cfg.get("class_code") or not cfg.get("teacher_key"):
        cfg.setdefault("class_code", config.generate_class_code())
        cfg.setdefault("teacher_key", config.generate_secret())
        config.save("hub", cfg)

    def save_approved(ids):
        cfg["approved"] = ids
        config.save("hub", cfg)

    ssl_context = None
    if not args.no_tls:
        cert_path, key_path = tls.ensure_hub_cert()
        ssl_context = tls.server_context(cert_path, key_path)

    print(f"Class code:  {cfg['class_code']}")
    print(f"Teacher key: {cfg['teacher_key']}")
    print(f"Settings:    {config.app_dir() / 'hub.json'}")
    if ssl_context is not None:
        print(f"TLS cert:    {config.app_dir() / 'hub_cert.pem'}")

    hub = Hub(cfg["class_code"], cfg["teacher_key"], host=args.host, port=args.port,
              discovery_port=None if args.no_discovery else DISCOVERY_PORT,
              ssl_context=ssl_context, require_approval=not args.no_approval,
              approved=cfg.get("approved", []), on_approved_change=save_approved)
    try:
        asyncio.run(hub.serve_forever())
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
