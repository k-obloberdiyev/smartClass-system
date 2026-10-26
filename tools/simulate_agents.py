"""Connect many fake student agents to a hub, for load-testing the teacher app.

    python tools/simulate_agents.py --hub ws://127.0.0.1:8765 --code ABCD-2345 -n 30

Fake agents answer every command with "ok" without doing anything. When the
teacher watches them, they stream a solid-colour placeholder thumbnail so the
monitoring grid can be exercised too (needs Pillow).

The URL may be ws:// or wss://. With the default (approval-required) hub the
sims appear as pending until you click "Approve all"; start the standalone hub
with --no-approval to skip that.
"""

import argparse
import asyncio
import base64
import io
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from websockets.asyncio.client import connect  # noqa: E402

from smartclass.common.protocol import AGENT, OK, decode, encode  # noqa: E402

_COLOURS = ["#2e4a7f", "#7f2e4a", "#2e7f4a", "#7f6b2e", "#4a2e7f", "#2e7f7f"]


def _placeholder(index, max_px):
    from PIL import Image, ImageDraw

    size = (max_px, max_px * 9 // 16)
    img = Image.new("RGB", size, _COLOURS[index % len(_COLOURS)])
    ImageDraw.Draw(img).text((10, 10), f"SIM-PC-{index:02d}", fill="#ffffff")
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=50)
    return base64.b64encode(buf.getvalue()).decode("ascii"), size


async def _stream(ws, index, cfg):
    image, (w, h) = _placeholder(index, cfg.get("max_px") or 320)
    while True:
        await ws.send(encode("frame", image=image, format="jpeg", w=w, h=h,
                             window=f"Simulated window {index}", ts=time.time()))
        await asyncio.sleep(cfg.get("interval") or 2.0)


def _ssl_context(url):
    if not url.startswith("wss://"):
        return None
    import ssl

    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)  # TOFU happens in the real agent; sims don't verify
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


async def fake_agent(url, code, index):
    async with connect(url, proxy=None, ssl=_ssl_context(url)) as ws:
        await ws.send(encode("hello", role=AGENT, class_code=code, agent_id=f"sim-{index:03d}",
                             name=f"SIM-PC-{index:02d}", os="Windows-11-simulated", version="sim"))
        streamer = None
        async for raw in ws:
            msg = decode(raw)
            if msg["type"] == "command":
                await ws.send(encode("result", id=msg["id"], status=OK, detail="simulated"))
            elif msg["type"] == "monitor":
                if streamer:
                    streamer.cancel()
                    streamer = None
                if msg.get("active"):
                    streamer = asyncio.create_task(_stream(ws, index, msg))


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
