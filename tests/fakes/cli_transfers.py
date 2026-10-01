"""Observable synchronous streams for bounded CLI transfer tests."""

from collections.abc import Callable, Iterator
from typing import BinaryIO

import httpx


class TransferStream(httpx.SyncByteStream):
    def __init__(self, chunks: list[bytes], on_chunk: Callable[[], None] | None = None):
        self.chunks, self.on_chunk = chunks, on_chunk
        self.closed = False

    def __iter__(self) -> Iterator[bytes]:
        for chunk in self.chunks:
            if self.on_chunk is not None:
                self.on_chunk()
            yield chunk

    def close(self) -> None:
        self.closed = True


class TransferTransport(httpx.BaseTransport):
    """Do not use MockTransport: it eagerly reads request bodies before handlers."""

    def __init__(self, handler):
        self.handler = handler
        self.closed = False

    def handle_request(self, request: httpx.Request) -> httpx.Response:
        return self.handler(request)

    def close(self) -> None:
        self.closed = True


class BoundedReadFile:
    def __init__(self, source: BinaryIO):
        self.source = source
        self.read_sizes: list[int] = []

    def read(self, size: int = -1) -> bytes:
        assert 0 <= size <= 65536, "Upload attempted an unbounded file read"
        self.read_sizes.append(size)
        return self.source.read(size)

    def fileno(self):
        return self.source.fileno()

    def tell(self):
        return self.source.tell()

    def seek(self, offset, whence=0):
        return self.source.seek(offset, whence)

    def __enter__(self):
        return self

    def __exit__(self, *error):
        self.source.close()
