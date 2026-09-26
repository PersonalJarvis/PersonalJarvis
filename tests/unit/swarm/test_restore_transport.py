"""Multipart upload limits apply before spooling untrusted backup bodies."""

import asyncio

import pytest
from starlette.datastructures import Headers
from starlette.formparsers import MultiPartException

from jarvis.ui.web.swarm_routes import _BackupMultipartParser


@pytest.mark.asyncio
async def test_cancelled_upload_closes_every_created_spool_file():
    async def interrupted_body():
        yield (
            b'--backup\r\nContent-Disposition: form-data; name="file"; filename="team.zip"\r\n'
            b"Content-Type: application/zip\r\n\r\nsome streamed bytes"
        )
        raise asyncio.CancelledError

    parser = _BackupMultipartParser(
        Headers({"Content-Type": "multipart/form-data; boundary=backup"}), interrupted_body()
    )
    with pytest.raises(asyncio.CancelledError):
        await parser.parse()
    assert parser._files_to_close_on_error
    assert all(stream.closed for stream in parser._files_to_close_on_error)


@pytest.mark.asyncio
async def test_multipart_headers_have_their_own_small_memory_limit():
    async def oversized_headers():
        yield (
            b'--backup\r\nContent-Disposition: form-data; name="file"; filename="'
            + b"x" * 9000
            + b'"\r\n\r\nbody\r\n--backup--\r\n'
        )

    parser = _BackupMultipartParser(
        Headers({"Content-Type": "multipart/form-data; boundary=backup"}), oversized_headers()
    )
    with pytest.raises((MultiPartException, ValueError), match="header.*exceed"):
        await parser.parse()
    assert all(stream.closed for stream in parser._files_to_close_on_error)
