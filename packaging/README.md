# Building SmartClass executables

These are the standalone, double-click builds of SmartClass. `tools/build.py`
drives [PyInstaller](https://pyinstaller.org/); `run_agent.py` and
`run_teacher.py` are the entry points it bundles (thin wrappers so the
package's relative imports resolve when frozen).

## Prerequisites

```bash
python -m venv .venv
.venv/bin/pip install -r requirements-build.txt     # Windows: .venv\Scripts\pip ...
```

## Build

```bash
python tools/build.py agent      # student agent
python tools/build.py teacher    # teacher app
python tools/build.py all
```

The result lands in `dist/`:

- Windows: `smartclass-agent.exe`, `smartclass-teacher.exe`
- macOS: `smartclass-agent`, `smartclass-teacher` (plus a `.app` bundle)

## Important: build each OS on that OS

PyInstaller does **not** cross-compile. The student PCs are Windows, so the
**student agent must be built on a Windows machine** — you can't produce
`smartclass-agent.exe` from macOS. The teacher app can be built on whichever OS
the teacher uses (Windows or macOS).

Both targets have been validated by building and launching them on macOS; the
same spec produces the Windows binaries when run on Windows.

## Don't have a Windows machine? Use CI

`.github/workflows/build.yml` builds both apps on GitHub's `windows-latest` and
`macos-latest` runners and uploads them as artifacts. Trigger it from the
repository's **Actions → Build executables → Run workflow**, or by pushing a
`v*` tag, then download the `smartclass-windows` / `smartclass-macos` artifact
from the run. This is how to get `smartclass-agent.exe` without a Windows PC.

## Notes

- Both builds are **windowed** (no console). To see agent logs while
  troubleshooting, run it from source instead (`python -m smartclass.agent`).
- The agent re-launches its own executable to show the lock overlay, the
  monitoring banner, and message/file popups. This is handled for frozen builds
  (`sys.frozen`), so no extra files are needed beside the exe.
- First launch may be flagged by Windows SmartScreen or macOS Gatekeeper
  because the binaries are unsigned. Code signing is out of scope here; on a
  managed classroom, deploy the agent through the school's software tooling.
- macOS onefile + windowed produces a `.app`; PyInstaller warns that onedir is
  preferred there. The agent's real target is Windows, so onefile is kept for a
  single-file `.exe`.
