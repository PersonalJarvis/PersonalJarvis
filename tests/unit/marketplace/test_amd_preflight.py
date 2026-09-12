"""AMD availability must be honest before enabling a local connector."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from jarvis.marketplace import amd_mcp


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_unsupported_os_does_not_launch_cli(monkeypatch, platform):
    monkeypatch.setattr(amd_mcp.sys, "platform", platform)

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported OS must not invoke AMD tooling")

    monkeypatch.setattr(amd_mcp.shutil, "which", forbidden)
    monkeypatch.setattr(amd_mcp.subprocess, "run", forbidden)
    assert "operating system" in amd_mcp.amd_unavailable_reason()
    with pytest.raises(RuntimeError, match="operating system"):
        amd_mcp.read_amd_status()


@pytest.fixture
def linux_cli(monkeypatch):
    monkeypatch.setattr(amd_mcp.sys, "platform", "linux")
    monkeypatch.setattr(amd_mcp.shutil, "which", lambda _: "/opt/rocm/bin/amd-smi")


@pytest.mark.parametrize(
    "payload",
    [
        {"gpu_data": []},
        {"error": "driver failed"},
        1,
        "error",
        [None],
        [{}],
        {"gpu_data": [{"error": "missing device"}]},
    ],
)
def test_empty_and_error_payloads_never_enable_connector(monkeypatch, linux_cli, payload):
    monkeypatch.setattr(
        amd_mcp.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload)),
    )
    with pytest.raises(RuntimeError, match="no GPU data"):
        amd_mcp.read_amd_status()


def test_unavailable_metrics_are_preserved_without_zero_defaults(monkeypatch, linux_cli):
    payload = {"gpu_data": [{"gpu": 0, "temperature": "N/A", "power": None}]}
    monkeypatch.setattr(
        amd_mcp.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload)),
    )
    assert amd_mcp.read_amd_status() == {"static": payload, "metric": payload}


@pytest.mark.parametrize(
    "error", [OSError("private path"), subprocess.TimeoutExpired("secret", 10)]
)
def test_command_failures_have_actionable_safe_messages(monkeypatch, linux_cli, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(amd_mcp.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as caught:
        amd_mcp.read_amd_status()
    assert "Check" in str(caught.value)
    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)
    assert type(error).__name__ not in str(caught.value)
