"""UUIDv7 helper for mission events.

UUIDv7 (RFC 9562) is time-prefixed (lexicographically sortable) and contains
74 bits of randomness. Available in the standard library from Python 3.13+;
inlined here because this repo targets Python 3.11 — no additional dependency
(`uuid_extensions` would be the alternative).

Layout (128 bits, big-endian):
  | 48 unix_ts_ms | 4 ver=0b0111 | 12 rand_a | 2 var=0b10 | 62 rand_b |

Monotonicity: two successive calls have different random components; the
timestamp prefix sorts them approximately in time. Sub-millisecond ordering is
not guaranteed to be strictly monotonic — for event-store ordering we rely on
the `seq` column value, not on the UUID.
"""
from __future__ import annotations

import secrets
import time
from uuid import UUID


def uuid7() -> UUID:
    """Generate a time-prefixed UUIDv7."""
    ts_ms = time.time_ns() // 1_000_000
    rand_a = secrets.randbits(12)
    rand_b = secrets.randbits(62)

    value = (ts_ms & 0xFFFF_FFFF_FFFF) << 80
    value |= (0x7 & 0xF) << 76
    value |= (rand_a & 0xFFF) << 64
    value |= (0b10 & 0x3) << 62
    value |= rand_b & 0x3FFF_FFFF_FFFF_FFFF

    return UUID(int=value)


def uuid7_str() -> str:
    """UUIDv7 as a canonical string (e.g. for Pydantic default_factory)."""
    return str(uuid7())
