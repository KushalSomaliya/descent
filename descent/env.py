"""Minimal .env loader (stdlib only).

Reads KEY=value lines from a .env file and sets them in os.environ, without
overriding variables that are already set in the shell.
"""

from __future__ import annotations

import os
from pathlib import Path


def _parse_line(line: str) -> tuple[str, str] | None:
    line = line.strip()
    if not line or line.startswith("#"):
        return None
    if line.startswith("export "):
        line = line[len("export "):].lstrip()
    key, sep, value = line.partition("=")
    key = key.strip()
    if not sep or not key or not key.replace("_", "").isalnum():
        return None
    value = value.strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in ("'", '"'):
        value = value[1:-1]
    elif " #" in value:
        value = value.split(" #", 1)[0].rstrip()
    return key, value


def load_dotenv(path: str | Path | None = None) -> list[str]:
    """Load the first .env found (given path, else ./.env, else the repo root). Returns keys set."""
    candidates = [Path(path)] if path else [Path.cwd() / ".env", Path(__file__).resolve().parent.parent / ".env"]
    for candidate in candidates:
        if candidate.is_file():
            loaded = []
            for raw in candidate.read_text(encoding="utf-8").splitlines():
                parsed = _parse_line(raw)
                if parsed and parsed[0] not in os.environ:
                    os.environ[parsed[0]] = parsed[1]
                    loaded.append(parsed[0])
            return loaded
    return []
