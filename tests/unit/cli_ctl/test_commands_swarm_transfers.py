"""Owner CLI lifecycle transfers use the shared gate and stream verified bytes."""

import hashlib
import json
from pathlib import Path

import httpx
import pytest
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app
from jarvis.cli_ctl.client import JarvisClient
from tests.fakes.cli_transfers import TransferStream, TransferTransport

TEAM_ID = "a" * 32
BACKUP_ID = "b" * 32
BASE = f"/api/swarm/teams/{TEAM_ID}"
CONTENT = b"portable archive test bytes"
runner = CliRunner()


@pytest.fixture
def transfer_api(monkeypatch):
    calls, responses = [], {}

    def handler(request):
        chunks = list(request.stream)
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "chunks": chunks,
                "body": b"".join(chunks),
                "headers": dict(request.headers),
            }
        )
        response = responses.get((request.method, request.url.path))
        assert response is not None, f"Unexpected request: {request.method} {request.url.path}"
        return response() if callable(response) else httpx.Response(200, json=response)

    monkeypatch.setattr(
        "jarvis.cli_ctl.__main__.make_client",
        lambda: JarvisClient(
            "http://jarvis.test",
            None,
            transport=TransferTransport(handler),
        ),
    )
    return calls, responses


def backup_responses(responses, **receipt_changes):
    receipt = {
        "team_id": TEAM_ID,
        "backup_id": BACKUP_ID,
        "size_bytes": str(len(CONTENT)),
        "sha256": hashlib.sha256(CONTENT).hexdigest(),
        "download_url": f"{BASE}/backups/{BACKUP_ID}",
        **receipt_changes,
    }
    responses[("POST", f"{BASE}/backup")] = receipt
    responses[("GET", f"{BASE}/backups/{BACKUP_ID}")] = lambda: httpx.Response(
        200,
        headers={"Content-Length": str(len(CONTENT))},
        stream=TransferStream([CONTENT]),
    )


def test_export_posts_then_streams_exact_owned_backup(transfer_api, tmp_path):
    calls, responses = transfer_api
    backup_responses(responses)
    output = tmp_path / "backup.zip"
    result = runner.invoke(
        app,
        [
            "--json",
            "swarm",
            "export",
            TEAM_ID,
            "--output",
            str(output),
            "--yes",
            "--request-key",
            "same-backup",
            "--expected-version",
            "7",
        ],
    )
    assert result.exit_code == 0, result.output
    assert [(call["method"], call["path"]) for call in calls] == [
        ("POST", f"{BASE}/backup"),
        ("GET", f"{BASE}/backups/{BACKUP_ID}"),
    ]
    assert json.loads(calls[0]["body"]) == {"request_key": "same-backup", "expected_version": 7}
    assert output.read_bytes() == CONTENT
    result_body = json.loads(result.output)
    assert result_body["sha256"] == hashlib.sha256(CONTENT).hexdigest()
    assert result_body["size_bytes"] == str(len(CONTENT))
    assert result_body["request_key"] == "same-backup"


@pytest.mark.parametrize(
    "receipt_changes",
    [
        {"team_id": "c" * 32},
        {"backup_id": "../escape"},
        {"download_url": "https://outside.invalid/backup"},
        {"download_url": f"/api/swarm/teams/{'c' * 32}/backups/{BACKUP_ID}"},
        {"size_bytes": "999999999999999999999999999"},
        {"size_bytes": "-1"},
        {"sha256": "invalid"},
        {"sha256": False},
    ],
)
def test_export_refuses_untrusted_receipt_before_any_download(
    transfer_api, tmp_path, receipt_changes
):
    calls, responses = transfer_api
    backup_responses(responses, **receipt_changes)
    output = tmp_path / "backup.zip"
    result = runner.invoke(app, ["swarm", "export", TEAM_ID, "--output", str(output), "--yes"])
    assert result.exit_code == 1, result.output
    assert len(calls) == 1
    assert not output.exists()


def test_export_needs_explicit_force_before_overwriting(transfer_api, tmp_path):
    calls, responses = transfer_api
    backup_responses(responses)
    output = tmp_path / "backup.zip"
    output.write_bytes(b"existing")
    args = ["swarm", "export", TEAM_ID, "--output", str(output), "--yes"]
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert calls == []
    assert output.read_bytes() == b"existing"
    result = runner.invoke(app, [*args, "--force"])
    assert result.exit_code == 0, result.output
    assert output.read_bytes() == CONTENT


def test_restore_sends_streamed_multipart_fields(transfer_api, tmp_path):
    calls, responses = transfer_api
    responses[("POST", "/api/swarm/restores")] = {"restore_id": BACKUP_ID, "team": {"id": TEAM_ID}}
    source = tmp_path / "backup.zip"
    source.write_bytes(CONTENT)
    result = runner.invoke(
        app,
        [
            "swarm",
            "restore",
            str(source),
            "--request-key",
            "same-restore",
            "--replace-team-id",
            TEAM_ID,
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    call = calls[0]
    assert call["headers"]["content-type"].startswith("multipart/form-data;")
    assert CONTENT in call["body"]
    assert b'name="request_key"\r\n\r\nsame-restore' in call["body"]
    assert b'name="replace_team_id"\r\n\r\n' + TEAM_ID.encode() in call["body"]
    assert max(map(len, call["chunks"])) <= 65536


@pytest.mark.parametrize("operation", ["export", "restore"])
@pytest.mark.parametrize("dry_run", [False, True])
def test_transfer_gate_never_reads_upload_or_writes_output(
    transfer_api, tmp_path, monkeypatch, operation, dry_run
):
    calls, _ = transfer_api
    chosen = tmp_path / "does-not-exist.zip"
    original = Path.open

    def forbid_file_access(path, *args, **kwargs):
        assert path != chosen, "Transfer opened its file before the gate"
        return original(path, *args, **kwargs)

    monkeypatch.setattr(Path, "open", forbid_file_access)
    args = (
        ["swarm", "export", TEAM_ID, "--output", str(chosen)]
        if operation == "export"
        else ["swarm", "restore", str(chosen), "--request-key", "same-restore"]
    )
    if dry_run:
        args.append("--dry-run")
    result = runner.invoke(app, args)
    assert result.exit_code == (0 if dry_run else 1), result.output
    assert calls == []
    assert not chosen.exists()


def test_lifecycle_commands_keep_identity_and_danger_gate(capture_api):
    for args in (
        ["delete", TEAM_ID, "--request-key", "one-delete"],
        ["retention", TEAM_ID],
        ["resume-restore", BACKUP_ID],
    ):
        result = runner.invoke(app, ["swarm", *args])
        assert result.exit_code == 1
    assert capture_api["calls"] == []
    result = runner.invoke(
        app,
        [
            "swarm",
            "delete",
            TEAM_ID,
            "--request-key",
            "one-delete",
            "--expected-version",
            "4",
            "--yes",
        ],
    )
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["body"] == {
        "confirm_team_id": TEAM_ID,
        "request_key": "one-delete",
        "expected_version": 4,
    }
    result = runner.invoke(app, ["swarm", "retention", TEAM_ID, "--before-days", "14", "--yes"])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["body"] == {"before_days": 14}
    result = runner.invoke(app, ["swarm", "storage", TEAM_ID])
    assert result.exit_code == 0
    assert capture_api["calls"][-1]["path"] == f"{BASE}/storage"
    result = runner.invoke(app, ["swarm", "restores"])
    assert result.exit_code == 0
    assert capture_api["calls"][-1]["path"] == "/api/swarm/restores"


@pytest.mark.parametrize("kind", ["decisions", "checkpoints"])
def test_control_history_collections_are_cli_reachable(capture_api, kind):
    result = runner.invoke(app, ["swarm", "records", TEAM_ID, kind])
    assert result.exit_code == 0, result.output
    assert capture_api["calls"][-1]["path"] == f"{BASE}/{kind}"


def test_cli_and_runtime_agree_on_backup_size_bound():
    from jarvis.cli_ctl.commands.swarm import MAX_BACKUP_BYTES
    from jarvis.swarm.lifecycle import MAX_ARCHIVE_BYTES

    assert MAX_BACKUP_BYTES == MAX_ARCHIVE_BYTES
