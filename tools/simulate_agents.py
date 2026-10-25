"""Connect many fake student agents to a hub, for load-testing the teacher app.

    python tools/simulate_agents.py --hub ws://127.0.0.1:8765 --code ABCD-2345 -n 30

Fake agents answer every command with "ok" without doing anything.
"""

import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from websockets.asyncio.client import connect  # noqa: E402

from smartclass.common.protocol import AGENT, OK, decode, encode  # noqa: E402


async def fake_agent(url, code, index):
    async with connect(url, proxy=None) as ws:
        await ws.send(encode("hello", role=AGENT, class_code=code, agent_id=f"sim-{index:03d}",
                             name=f"SIM-PC-{index:02d}", os="Windows-11-simulated", version="sim"))
        async for raw in ws:
            msg = decode(raw)
            if msg["type"] == "command":
                await ws.send(encode("result", id=msg["id"], status=OK, detail="simulated"))


async def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--hub", default="ws://127.0.0.1:8765")
    parser.add_argument("--code", required=True)
    parser.add_argument("-n", type=int, default=30)
    args = parser.parse_args()
    print(f"Connecting {args.n} fake agents to {args.hub} (Ctrl+C to stop)")
    await asyncio.gather(*(fake_agent(args.hub, args.code, i) for i in range(1, args.n + 1)))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
