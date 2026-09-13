from __future__ import annotations

import re


_SAFE_RE = re.compile(r"[^a-z0-9_]+")


def to_snake_identifier(value: str) -> str:
    s = (value or "").strip().lower()
    s = s.replace("-", "_").replace(".", "_").replace("/", "_")
    s = _SAFE_RE.sub("_", s)
    s = re.sub(r"_+", "_", s).strip("_")
    return s


def make_exposed_tool_name(server_id: str, tool_name: str, *, prefix: str = "mcp", max_len: int = 64) -> str:
    sid = to_snake_identifier(server_id)
    tid = to_snake_identifier(tool_name)
    base = "_".join([p for p in (prefix, sid, tid) if p])
    if not base:
        base = f"{prefix}_tool"
    if len(base) <= max_len:
        return base
    # Keep prefix + tail to preserve uniqueness signal
    tail = base[-(max_len - len(prefix) - 1) :]
    return f"{prefix}_{tail}"


def is_provider_safe_tool_name(name: str) -> bool:
    if not name:
        return False
    return bool(re.fullmatch(r"[a-zA-Z0-9_]+", name))

