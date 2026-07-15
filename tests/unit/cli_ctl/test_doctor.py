"""Unreachable-diagnosis contract: the CLI must say WHAT is actually wrong
(booting / crashed / not started / remote target) instead of one canned
"start the app" line — and phrase it with some variety."""
from __future__ import annotations

import json
import os

import click
import httpx
import pytest
from click.testing import CliRunner

from jarvis.cli_ctl import discovery, doctor
from jarvis.cli_ctl.client import ApiError
from jarvis.cli_ctl.dynamic import build_api_group


@pytest.fixture(autouse=True)
def _isolated_profile(monkeypatch, tmp_path):
    monkeypatch.delenv("JARVISCTL_BASE_URL", raising=False)
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path / "cache"))
    yield


def _write_session(tmp_path, monkeypatch, *, pid: int) -> str:
    session = tmp_path / "session.json"
    session.write_text(
        json.dumps({"port": 47821, "token": "jctl_t", "pid": pid}),
        encoding="utf-8",
    )
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(session))
    return "http://127.0.0.1:47821"


def test_running_but_not_answering_never_says_start_the_app(
    monkeypatch, tmp_path
) -> None:
    """The app process is ALIVE (own pid) — advising a start would be wrong;
    the message must point at booting/retry/restart instead."""
    url = _write_session(tmp_path, monkeypatch, pid=os.getpid())
    msg = doctor.unreachable_message(url)
    assert str(os.getpid()) in msg
    assert "run.bat" not in msg
    assert "retry" in msg.lower() or "try again" in msg.lower()


def test_stale_session_after_crash_recommends_a_fresh_start(
    monkeypatch, tmp_path
) -> None:
    url = _write_session(tmp_path, monkeypatch, pid=424242)
    monkeypatch.setattr(discovery, "_pid_alive", lambda pid: False)
    msg = doctor.unreachable_message(url)
    assert "run.bat" in msg
    assert "424242" in msg  # names the dead pid — honest, not generic


def test_nothing_running_recommends_start_or_remote(monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", "")  # discovery disabled
    msg = doctor.unreachable_message(None)
    assert "run.bat" in msg
    assert "--url" in msg


def test_explicit_remote_target_never_recommends_local_start(
    monkeypatch,
) -> None:
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", "")
    monkeypatch.setenv("JARVISCTL_BASE_URL", "http://10.0.0.5:9999")
    msg = doctor.unreachable_message("http://10.0.0.5:9999")
    assert "run.bat" not in msg
    assert "10.0.0.5:9999" in msg


def test_phrasing_varies_across_invocations(monkeypatch, tmp_path) -> None:
    """Not a broken record: repeated failures must not always read the same."""
    url = _write_session(tmp_path, monkeypatch, pid=os.getpid())
    seen = {doctor.unreachable_message(url) for _ in range(25)}
    assert len(seen) >= 2


def test_dynamic_api_layer_fails_clean_with_diagnosis(monkeypatch) -> None:
    """A dead server on `jarvis api ...` must exit 1 with the diagnosis —
    never a raw Python traceback."""
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", "")
    spec = {
        "openapi": "3.1.0", "info": {"version": "1"},
        "paths": {"/api/ping": {"get": {"tags": ["diag"], "operationId": "ping",
                  "summary": "Ping"}}},
    }

    def runner(method, path, params, body, *, timeout_s=None):
        raise ApiError(
            "Jarvis at http://127.0.0.1:47821 is unreachable.",
            None, base_url="http://127.0.0.1:47821",
        )

    group = build_api_group(spec, runner)
    root = click.Group("jarvis")
    root.add_command(group)
    result = CliRunner().invoke(root, ["api", "diag", "ping"])
    try:  # Click >= 8.2 captures stderr separately; older raises ValueError
        stderr = result.stderr
    except (ValueError, AttributeError):
        stderr = ""
    combined = result.output + stderr
    assert result.exit_code == 1
    assert "Traceback" not in combined
    assert "run.bat" in combined or "--url" in combined


def test_client_transport_error_carries_base_url() -> None:
    from jarvis.cli_ctl.client import JarvisClient

    def handler(request):
        raise httpx.ConnectError("down")

    client = JarvisClient(
        base_url="http://test", control_key="jctl_k",
        transport=httpx.MockTransport(handler),
    )
    with pytest.raises(ApiError) as ei:
        client.request("GET", "/api/tasks")
    assert ei.value.base_url == "http://test"
    assert ei.value.status_code is None
