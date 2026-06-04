"""Bloom filter used for set reconciliation between sync peers (ADR-0006).

Pure-Python, no external dependencies. Bits are packed in a bytearray; the
``k`` independent hash functions are derived from SHA-256 by splitting its
output into k 32-bit unsigned integers (sufficient for any practical
``m``).
"""

from __future__ import annotations

import hashlib
import struct
from collections.abc import Iterable
from dataclasses import dataclass

_MAGIC = b"SKB_BLOOM_V1"


@dataclass(slots=True)
class BloomFilter:
    m: int
    k: int
    bits: bytearray = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.m <= 0 or self.k <= 0:
            raise ValueError("m and k must be positive")
        if self.k > 8:
            # Each hash consumes 4 bytes of SHA-256; 8 hashes fits in 32 bytes.
            raise ValueError("k must be <= 8 with the SHA-256 backing scheme")
        byte_len = (self.m + 7) // 8
        if self.bits is None:
            self.bits = bytearray(byte_len)
        elif len(self.bits) != byte_len:
            raise ValueError(f"bits length {len(self.bits)} != expected {byte_len}")

    def _positions(self, item: str) -> list[int]:
        digest = hashlib.sha256(item.encode("utf-8")).digest()
        return [
            struct.unpack_from(">I", digest, offset=i * 4)[0] % self.m
            for i in range(self.k)
        ]

    def add(self, item: str) -> None:
        for pos in self._positions(item):
            self.bits[pos // 8] |= 1 << (pos % 8)

    def __contains__(self, item: object) -> bool:
        if not isinstance(item, str):
            return False
        for pos in self._positions(item):
            if not (self.bits[pos // 8] & (1 << (pos % 8))):
                return False
        return True

    def serialize(self) -> bytes:
        header = _MAGIC + struct.pack(">II", self.m, self.k)
        return header + bytes(self.bits)

    @classmethod
    def deserialize(cls, blob: bytes) -> "BloomFilter":
        if not blob.startswith(_MAGIC):
            raise ValueError("not a SKB_BLOOM_V1 payload")
        m, k = struct.unpack(">II", blob[len(_MAGIC) : len(_MAGIC) + 8])
        bits = bytearray(blob[len(_MAGIC) + 8 :])
        return cls(m=m, k=k, bits=bits)

    @classmethod
    def from_items(cls, items: Iterable[str], *, m: int = 8192, k: int = 4) -> "BloomFilter":
        bf = cls(m=m, k=k)
        for it in items:
            bf.add(it)
        return bf
