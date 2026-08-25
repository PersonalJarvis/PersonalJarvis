"""`jarvis local-models assistant ...` hits the assistant routes with the exact bodies."""

from __future__ import annotations

import pytest
from typer.testing import CliRunner

from jarvis.cli_ctl.__main__ import app

runner = CliRunner()
_BASE = "/api/providers/ollama/local-models/assistant"


def _one(capture_api) -> dict:
    assert len(capture_api["calls"]) == 1, capture_api["calls"]
    return capture_api["calls"][0]


@pytest.mark.parametrize("mode", ["setup", "diagnose"])
def test_run_modes_post_without_a_confirmation(capture_api, mode: str) -> None:
    res = runner.invoke(app, ["local-models", "assistant", mode])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("POST", f"{_BASE}/run")
    assert call["body"] == {"mode": mode}


def test_test_posts_the_role_filter(capture_api) -> None:
    argv = ["local-models", "assistant", "test", "--role", "chat", "--role", "deep"]
    res = runner.invoke(app, argv)
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("POST", f"{_BASE}/test")
    assert call["body"] == {"roles": ["chat", "deep"]}


def test_test_without_roles_sends_no_body(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "assistant", "test"])
    assert res.exit_code == 0, res.output
    assert _one(capture_api)["body"] is None


def test_benchmarks_health_and_session_are_gets(capture_api) -> None:
    res = runner.invoke(app, ["local-models", "assistant", "benchmarks", "--refresh"])
    assert res.exit_code == 0, res.output
    call = _one(capture_api)
    assert (call["method"], call["path"]) == ("GET", f"{_BASE}/benchmarks")
    assert call["query"] == {"refresh": "1"}

    capture_api["calls"].clear()
    res = runner.invoke(app, ["local-models", "assistant", "health", "--provider", "llamacpp"])
    assert res.exit_code == 0, res.output
    assert _one(capture_api)["path"] == "/api/providers/llamacpp/local-models/assistant/health"

    capture_api["calls"].clear()
    res = runner.invoke(app, ["local-models", "assistant", "session"])
    assert res.exit_code == 0, res.output
    assert (_one(capture_api)["method"], _one(capture_api)["path"]) == ("GET", f"{_BASE}/session")
