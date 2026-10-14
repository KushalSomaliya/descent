"""Pull a JSON object out of whatever a model returns."""

from __future__ import annotations

import json
import re
from typing import Any

THINK_RE = re.compile(r"<think>.*?</think>", re.DOTALL | re.IGNORECASE)
FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def extract_json(text: str) -> dict[str, Any] | None:
    if not text:
        return None
    text = THINK_RE.sub("", text).strip()
    candidates = [m.group(1) for m in FENCE_RE.finditer(text)] + [text]
    for cand in candidates:
        cand = cand.strip()
        try:
            obj = json.loads(cand)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        obj = _first_object(cand)
        if obj is not None:
            return obj
    return None


def _first_object(s: str) -> dict[str, Any] | None:
    """Scan for the first balanced {...} that parses as JSON."""
    start = s.find("{")
    while start != -1:
        depth, in_str, esc = 0, False, False
        for i in range(start, len(s)):
            ch = s[i]
            if in_str:
                if esc:
                    esc = False
                elif ch == "\\":
                    esc = True
                elif ch == '"':
                    in_str = False
                continue
            if ch == '"':
                in_str = True
            elif ch == "{":
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0:
                    try:
                        obj = json.loads(s[start : i + 1])
                        if isinstance(obj, dict):
                            return obj
                    except json.JSONDecodeError:
                        break
                    break
        start = s.find("{", start + 1)
    return None
