"""Per-user JSON settings stored in the OS application-data folder.

Set SMARTCLASS_HOME to use a different folder (tests, running several
agents on one machine).
"""

import json
import os
import secrets
import sys
from pathlib import Path


def app_dir():
    override = os.environ.get("SMARTCLASS_HOME")
    if override:
        return Path(override)
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or Path.home() / "AppData" / "Roaming"
    elif sys.platform == "darwin":
        base = Path.home() / "Library" / "Application Support"
    else:
        base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "SmartClass"


def load(name):
    path = app_dir() / f"{name}.json"
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (FileNotFoundError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def save(name, data):
    folder = app_dir()
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f"{name}.json.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, folder / f"{name}.json")


# No 0/O or 1/I, so the code is easy to read aloud and type.
_CODE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"


def generate_class_code():
    chars = "".join(secrets.choice(_CODE_ALPHABET) for _ in range(8))
    return f"{chars[:4]}-{chars[4:]}"


def normalize_class_code(code):
    return (code or "").strip().upper()


def generate_secret():
    return secrets.token_urlsafe(24)
