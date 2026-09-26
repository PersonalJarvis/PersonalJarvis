"""Stream bounds, integrity, cleanup and credential confinement for CLI files."""

import hashlib
from pathlib import Path

import httpx
import pytest

from jarvis.cli_ctl.client import ApiError, JarvisClient
from tests.fakes.cli_transfers import BoundedReadFile, TransferStream, TransferTransport


def client_for(handler):
    credential = "test-only-control"
    return JarvisClient(
        "http://jarvis.test",
        credential,
        transport=TransferTransport(handler),
    )


def test_upload_streams_bounded_multipart_with_auth_and_closes_file(tmp_path, monkeypatch):
    source = tmp_path / "selected.zip"
    data = b"PK" * 100000
    source.write_bytes(data)
    opened = []
    original = Path.open

    def guarded(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        if path == source:
            wrapper = BoundedReadFile(handle)
            opened.append(wrapper)
            return wrapper
        return handle

    def handler(request):
        assert request.headers["authorization"] == "Bearer test-only-control"
        assert request.headers["content-type"].startswith("multipart/form-data; boundary=")
        chunks = list(request.stream)
        assert max(map(len, chunks)) <= 65536
        body = b"".join(chunks)
        assert data in body
        assert b'name="file"; filename="swarm-backup.zip"' in body
        assert b'name="request_key"\r\n\r\nrestore-one' in body
        assert b'name="replace_team_id"\r\n\r\n' + b"a" * 32 in body
        assert int(request.headers["content-length"]) == len(body)
        return httpx.Response(200, json={"restored": True})

    monkeypatch.setattr(Path, "open", guarded)
    with client_for(handler) as client:
        result = client.upload_file(
            "/api/swarm/restores",
            source,
            fields={"request_key": "restore-one", "replace_team_id": "a" * 32},
            max_bytes=len(data),
            timeout_s=42,
        )
    assert result == {"restored": True}
    assert opened[0].source.closed
    assert len(opened[0].read_sizes) >= 4


def test_upload_closes_file_on_transport_failure_and_rejects_oversize(tmp_path, monkeypatch):
    source = tmp_path / "selected.zip"
    source.write_bytes(b"selected bytes")
    opened = []
    original = Path.open

    def track(path, *args, **kwargs):
        handle = original(path, *args, **kwargs)
        if path == source:
            opened.append(handle)
        return handle

    def failure(request):
        next(iter(request.stream))
        raise httpx.ReadTimeout("interrupted")

    monkeypatch.setattr(Path, "open", track)
    with client_for(failure) as client:
        with pytest.raises(ApiError, match="unreachable"):
            client.upload_file("/api/swarm/restores", source, fields={}, max_bytes=100)
        with pytest.raises(ApiError, match="upload limit"):
            client.upload_file("/api/swarm/restores", source, fields={}, max_bytes=1)
    assert all(handle.closed for handle in opened)


def test_upload_refuses_file_growth_after_initial_size_check(tmp_path):
    source = tmp_path / "selected.zip"
    source.write_bytes(b"selected bytes")

    def grow_during_upload(request):
        with source.open("ab") as file:
            file.write(b"added after admission")
        list(request.stream)
        pytest.fail("A changed upload must not reach a successful response")

    with client_for(grow_during_upload) as client, pytest.raises(ApiError, match="changed size"):
        client.upload_file("/api/swarm/restores", source, fields={}, max_bytes=100)


def test_download_verifies_stream_and_preserves_existing_output_until_success(tmp_path):
    output = tmp_path / "export.zip"
    output.write_bytes(b"old verified output")
    content = b"PK" * 90000
    digest = hashlib.sha256(content).hexdigest()
    stream = TransferStream([content[:70000], content[70000:]])

    def handler(request):
        assert output.read_bytes() == b"old verified output"
        assert request.headers["authorization"] == "Bearer test-only-control"
        assert request.headers["accept-encoding"] == "identity"
        assert request.extensions["timeout"]["read"] == 42
        return httpx.Response(
            200,
            headers={
                "Content-Length": str(len(content)),
                "X-Content-SHA256": digest,
            },
            stream=stream,
        )

    with client_for(handler) as client:
        result = client.download_file(
            "/api/swarm/teams/team/backups/archive",
            output,
            expected_size=len(content),
            expected_sha256=digest,
            max_bytes=len(content),
            overwrite=True,
            timeout_s=42,
        )
    assert result["sha256"] == digest
    assert result["size_bytes"] == str(len(content))
    assert output.read_bytes() == content
    assert stream.closed
    assert list(tmp_path.glob("*.part")) == []


@pytest.mark.parametrize("failure", ["short", "long", "header", "hash", "redirect", "transport"])
def test_failed_download_removes_partial_and_preserves_previous_file(tmp_path, failure):
    output = tmp_path / "export.zip"
    output.write_bytes(b"original")
    content = b"selected backup"
    stream = TransferStream([content[:-1] if failure == "short" else content])

    def handler(request):
        if failure == "transport":

            def interrupt():
                raise httpx.ReadTimeout("interrupted")

            stream.on_chunk = interrupt
        if failure == "redirect":
            return httpx.Response(302, headers={"location": "https://outside.invalid/archive"})
        return httpx.Response(
            200,
            headers={
                "content-length": str(len(content) + (1 if failure == "header" else 0)),
            },
            stream=stream,
        )

    expected_size = len(content) - 1 if failure == "long" else len(content)
    expected_hash = "0" * 64 if failure == "hash" else hashlib.sha256(content).hexdigest()
    with client_for(handler) as client, pytest.raises(ApiError):
        client.download_file(
            "/api/swarm/teams/team/backups/archive",
            output,
            expected_size=expected_size,
            expected_sha256=expected_hash,
            max_bytes=100,
            overwrite=True,
        )
    assert output.read_bytes() == b"original"
    assert list(tmp_path.glob("*.part")) == []
    if failure != "redirect":
        assert stream.closed


def test_no_force_refuses_overwrite_and_a_destination_created_during_download(tmp_path):
    output = tmp_path / "export.zip"
    content = b"selected backup"

    def concurrent_file():
        output.write_bytes(b"other process")

    def handler(request):
        return httpx.Response(200, stream=TransferStream([content], on_chunk=concurrent_file))

    with client_for(handler) as client:
        with pytest.raises(ApiError, match="exists"):
            client.download_file(
                "/api/swarm/teams/team/backups/archive",
                output,
                expected_size=len(content),
                max_bytes=100,
            )
        with pytest.raises(ApiError, match="exists"):
            client.download_file(
                "/api/swarm/teams/team/backups/archive",
                output,
                expected_size=len(content),
                max_bytes=100,
            )
    assert output.read_bytes() == b"other process"
    assert list(tmp_path.glob("*.part")) == []


def test_failed_final_copy_cleans_exclusively_created_output(tmp_path, monkeypatch):
    output = tmp_path / "export.zip"
    content = b"selected backup"

    def fail_copy(source, destination, length):
        assert length == 65536
        destination.write(b"partial")
        raise OSError("Disk write failed")

    monkeypatch.setattr("jarvis.cli_ctl.client.shutil.copyfileobj", fail_copy)
    with client_for(lambda _: httpx.Response(200, stream=TransferStream([content]))) as client:
        with pytest.raises(ApiError, match="Cannot save"):
            client.download_file(
                "/api/swarm/teams/team/backups/archive",
                output,
                expected_size=len(content),
                max_bytes=100,
            )
    assert not output.exists()
    assert list(tmp_path.glob("*.part")) == []


@pytest.mark.parametrize(
    "path", ["https://outside.invalid/x", "//outside.invalid/x", "\\\\host\\x"]
)
def test_transfer_never_forwards_auth_to_another_origin(tmp_path, path):
    def forbidden(request):
        pytest.fail("No request may leave the configured API origin")

    with client_for(forbidden) as client, pytest.raises(ApiError, match="relative API path"):
        client.download_file(path, tmp_path / "out.zip", expected_size=1, max_bytes=10)
