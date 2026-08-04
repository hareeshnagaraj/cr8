"""Small dependency-free ULID generator for immutable public identifiers."""

from __future__ import annotations

import os
import time


_CROCKFORD = "0123456789ABCDEFGHJKMNPQRSTVWXYZ"


def new_ulid(*, timestamp_ms: int | None = None, randomness: bytes | None = None) -> str:
    """Return a canonical 26-character ULID."""
    timestamp = int(time.time_ns() // 1_000_000 if timestamp_ms is None else timestamp_ms)
    if not 0 <= timestamp < 1 << 48:
        raise ValueError("ULID timestamp must fit in 48 bits")
    random_bytes = os.urandom(10) if randomness is None else randomness
    if len(random_bytes) != 10:
        raise ValueError("ULID randomness must be exactly 10 bytes")
    value = (timestamp << 80) | int.from_bytes(random_bytes)
    encoded = ["0"] * 26
    for index in range(25, -1, -1):
        encoded[index] = _CROCKFORD[value & 31]
        value >>= 5
    return "".join(encoded)
