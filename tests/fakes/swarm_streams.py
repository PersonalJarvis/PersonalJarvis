"""Streams that fail if a consumer requests unbounded reads."""

import io


class BoundedStream(io.BytesIO):
    def __init__(self, content: bytes):
        super().__init__(content)
        self.requested: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert 0 < size <= 65536
        self.requested.append(size)
        return super().read(size)
