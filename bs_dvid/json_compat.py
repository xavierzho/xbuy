"""JSON serialization matching Node/V8 JSON.stringify key ordering."""

from __future__ import annotations

import json
from typing import Any


def _is_array_index_key(key: str) -> bool:
    if not isinstance(key, str) or not key.isdigit():
        return False
    if len(key) > 1 and key[0] == "0":
        return False
    n = int(key)
    return 0 <= n <= 0xFFFF_FFFE


def _object_keys_like_v8(obj: dict) -> list[str]:
    index_keys: list[tuple[int, str]] = []
    other_keys: list[str] = []
    for key in obj.keys():
        if _is_array_index_key(key):
            index_keys.append((int(key), key))
        else:
            other_keys.append(key)
    index_keys.sort(key=lambda item: item[0])
    return [key for _, key in index_keys] + other_keys


def dumps_like_node(value: Any) -> str:
    """Serialize like JSON.stringify in V8 (array-index keys sorted numerically)."""

    def encode(v: Any) -> str:
        if v is None:
            return "null"
        if v is True:
            return "true"
        if v is False:
            return "false"
        if isinstance(v, int):
            return str(v)
        if isinstance(v, float):
            if v != v or v in (float("inf"), float("-inf")):
                return "null"
            return json.dumps(v)
        if isinstance(v, str):
            return json.dumps(v, ensure_ascii=False)
        if isinstance(v, list):
            return "[" + ",".join(encode(item) for item in v) + "]"
        if isinstance(v, dict):
            parts: list[str] = []
            for key in _object_keys_like_v8(v):
                parts.append(json.dumps(str(key), ensure_ascii=False) + ":" + encode(v[key]))
            return "{" + ",".join(parts) + "}"
        raise TypeError(f"unsupported type for node json: {type(v)!r}")

    return encode(value)
