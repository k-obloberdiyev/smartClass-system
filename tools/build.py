"""Build standalone executables with PyInstaller.

    pip install -r requirements-build.txt
    python tools/build.py agent      # student agent (build this on Windows)
    python tools/build.py teacher    # teacher app  (build on Windows or macOS)
    python tools/build.py all

Each target is produced for the operating system you run this on: a Windows
.exe when run on Windows, a macOS binary when run on macOS. There's no
cross-compiling, so the Windows student agent must be built on a Windows
machine. Output lands in dist/.
"""

import argparse
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# PIL and customtkinter ship data files and submodules that PyInstaller misses
# unless we collect them explicitly; cryptography is imported lazily.
COMMON = ["--onefile", "--noconfirm", "--clean",
          "--collect-all", "PIL", "--hidden-import", "cryptography"]

TARGETS = {
    "agent": {
        "entry": ROOT / "packaging" / "run_agent.py",
        "name": "smartclass-agent",
        # Windowed: no console window pops up on the student's PC.
        "extra": ["--windowed"],
    },
    "teacher": {
        "entry": ROOT / "packaging" / "run_teacher.py",
        "name": "smartclass-teacher",
        "extra": ["--windowed", "--collect-all", "customtkinter"],
    },
}


def build(target):
    import PyInstaller.__main__

    spec = TARGETS[target]
    args = [
        str(spec["entry"]),
        "--name", spec["name"],
        "--paths", str(ROOT),
        "--distpath", str(ROOT / "dist"),
        "--workpath", str(ROOT / "build"),
        "--specpath", str(ROOT / "build"),
        *COMMON,
        *spec["extra"],
    ]
    print(f"Building {target}:\n  pyinstaller {' '.join(args)}\n")
    PyInstaller.__main__.run(args)


def main():
    parser = argparse.ArgumentParser(description="Build SmartClass executables")
    parser.add_argument("target", choices=[*TARGETS, "all"])
    args = parser.parse_args()
    targets = list(TARGETS) if args.target == "all" else [args.target]
    for target in targets:
        build(target)
    print(f"\nDone. See {ROOT / 'dist'} (built for {sys.platform}).")


if __name__ == "__main__":
    main()
