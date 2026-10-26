# smartClass-system

This project is designed to help English classes aimed at preparing students for an IELTS exam.

The teacher's laptop (Windows or macOS) controls the classroom's Windows PCs,
all of them or only the selected ones:

- open a website
- send a message, or send a file that lands in the student's SmartClass folder
- lock screens with an "Eyes on the teacher" overlay, and unlock them again
  (or use the real Windows lock screen)
- restart or shut down with a 60-second warning, which can be cancelled
- watch screens live as a grid of thumbnails, with each PC's active window

One-click installers are coming next (see the roadmap below).

A locked PC unlocks itself if it loses contact with the teacher for 2 minutes,
so closing the teacher app can never leave a classroom stuck locked.

Screen monitoring is opt-in and visible: a PC only captures and streams its
screen while the teacher is actually watching it, and while it does the student
sees a banner saying the screen is being viewed. The banner can't be closed
while monitoring is on, and it disappears the moment the teacher stops.

New PCs must be **approved** by the teacher before they can be controlled: a PC
with the class code shows up in a "want to join" list, and the teacher clicks
Approve (or Deny). Approved PCs are remembered, so they rejoin automatically.
Traffic is encrypted with **TLS** (`wss://`); the hub makes its own
certificate, and each agent pins it on first connection and refuses to connect
if it ever changes (so a machine impersonating the hub is rejected).

## How it works

```
 Teacher app ──┐                    ┌── Student agent (PC 1)
 (GUI + hub)   ├── WebSocket hub ───┼── Student agent (PC 2)
               │                    └── ...
```

- **Hub** routes commands and results. It runs inside the teacher app by default,
  or on its own (`python -m smartclass.hub`) on a machine both sides can reach.
- **Student agent** runs on each PC, connects out to the hub, and carries out commands.
- PCs join with the **class code** shown in the teacher app. Agents find the hub
  automatically on the same network; otherwise give them the hub's address.

## Running from source

Python 3.10+.

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-dev.txt     # Windows: .venv\Scripts\pip ...

# Teacher laptop
python -m smartclass.teacher

# Student PC (first run; settings are remembered afterwards)
python -m smartclass.agent --code ABCD-2345 --hub 192.168.1.20
#   --hub can be omitted when both are on the same network (auto-discovery)
```

Click **Watch screens** to open a live thumbnail grid of the selected PCs (or
all of them). Each watched PC shows its student a banner for as long as the
grid is open.

When a PC joins for the first time it appears in a "want to join" list at the
top of the teacher window; click **Approve** to let it in. The first time an
agent reaches the hub it also remembers the hub's TLS certificate. If the hub
is ever reinstalled (new certificate), re-run the agent once with `--reset-pin`.

The teacher app shows the class code and the address students should use.
Settings live in `%APPDATA%\SmartClass` (Windows) or
`~/Library/Application Support/SmartClass` (macOS).

### Firewall

The teacher laptop must accept incoming connections on **TCP 8765** (and UDP 8766
for auto-discovery). Windows and macOS ask the first time the app starts; choose *Allow*.

## Development

```bash
python -m pytest tests
python tools/simulate_agents.py --code ABCD-2345 -n 30   # 30 fake student PCs
```

## Standalone builds

To build double-click executables (no Python needed on the classroom PCs), see
[`packaging/README.md`](packaging/README.md). In short:

```bash
pip install -r requirements-build.txt
python tools/build.py agent      # build on Windows for student PCs
python tools/build.py teacher    # build on the teacher's OS
```

## Roadmap

- [x] Phase 0: cleanup and project structure
- [x] Phase 1: WebSocket hub, auto-discovery, reconnecting agents, class code
- [x] Phase 2: lock overlay with unlock, Windows lock, restart/shutdown with countdown
- [x] Phase 3: live screen thumbnails and "what's open" monitoring, with a
      visible banner on each watched PC
- [x] Phase 4: pairing approval, and TLS with trust-on-first-use pinning
- [x] Phase 5: send files and messages
- [ ] Phase 6: one-click installers (Windows agent, Windows/macOS teacher)
