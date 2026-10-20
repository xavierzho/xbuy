from __future__ import annotations

import os
from typing import Any


def is_parity_mode() -> bool:
    v = (os.environ.get("MT_PARITY_MODE") or "").strip().lower()
    return v in ("1", "true", "yes", "on")


class Mulberry32:
    """与 nodejs/shared/parity_rng.cjs 相同的 mulberry32 序列。"""

    def __init__(self, seed: int) -> None:
        self._a = int(seed) & 0xFFFFFFFF

    def random(self) -> float:
        self._a = (self._a + 0x6D2B79F5) & 0xFFFFFFFF
        t = self._a
        t = ((t ^ (t >> 15)) * (t | 1)) & 0xFFFFFFFF
        t = (t ^ (t + (((t ^ (t >> 7)) * (t | 61)) & 0xFFFFFFFF) & 0xFFFFFFFF)) & 0xFFFFFFFF
        return ((t ^ (t >> 14)) & 0xFFFFFFFF) / 4294967296.0

    def randbelow(self, n: int) -> int:
        return int(self.random() * n)

    def randint(self, lo: int, hi: int) -> int:
        return lo + self.randbelow(hi - lo + 1)

    def choice(self, seq: list | tuple) -> Any:
        return seq[self.randbelow(len(seq))]


class ParityContext:
    def __init__(self, seed: int, *, timestamps: list[int] | None = None) -> None:
        self.seed = int(seed)
        self.rng = Mulberry32(self.seed)
        self._timestamps = list(timestamps or [])
        self._ts_index = 0

    def random(self) -> float:
        return self.rng.random()

    def choice(self, seq: list | tuple) -> Any:
        return self.rng.choice(seq)

    def randint(self, lo: int, hi: int) -> int:
        return self.rng.randint(lo, hi)

    def randbelow(self, n: int) -> int:
        return self.rng.randbelow(n)

    def next_timestamp_sec(self, default: int | None = None) -> str:
        if self._ts_index < len(self._timestamps):
            ts = int(self._timestamps[self._ts_index])
            self._ts_index += 1
            return str(ts)
        if default is not None:
            return str(int(default))
        return str(int(self._timestamps[-1]) if self._timestamps else 1_700_000_000)

    def generate_random_key(self, length: int = 16) -> bytes:
        chars = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789"
        return "".join(chars[self.randbelow(len(chars))] for _ in range(length)).encode("utf-8")


def parity_context_from_fixture(fixture: dict) -> ParityContext:
    seed = int(fixture.get("deterministicSeed") or fixture.get("deterministic_seed") or 42)
    ts = fixture.get("requestTimestamps") or fixture.get("request_timestamps")
    if ts is None and fixture.get("requestTimestampBase") is not None:
        base = int(fixture["requestTimestampBase"])
        ts = [base + i for i in range(32)]
    return ParityContext(seed, timestamps=ts if isinstance(ts, list) else None)
