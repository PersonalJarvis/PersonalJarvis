"""The real module entry point must never discard an explicit request target."""

from __future__ import annotations

import json
import runpy
import sys
from types import SimpleNamespace

import httpx
import pytest

from jarvis.cli_ctl import __main__ as canonical
from jarvis.cli_ctl import config
from jarvis.cli_ctl.client import JarvisClient

pytestmark = pytest.mark.filterwarnings(
    "ignore:.*jarvis.cli_ctl.__main__.*found in sys.modules.*:RuntimeWarning"
)

DEFAULT = "http://fallback.invalid:47900/default"
EXPLICIT = "http://127.0.0.1:47999/proxy"
LOCAL = "http://127.0.0.1:47998/local"


@pytest.fixture
def targets(monkeypatch):
    calls: list[tuple[str, str | None]] = []
    response = {"status": 200, "unreachable": False}

    def handler(request):
        calls.append((str(request.url), request.headers.get("Authorization")))
        if response["unreachable"]:
            raise httpx.ConnectError("fixture target unavailable", request=request)
        return httpx.Response(response["status"], json={"ok": response["status"] == 200})

    original = JarvisClient.__init__

    def initialize(self, base_url, control_key, **kwargs):
        original(
            self, base_url, control_key, **{**kwargs, "transport": httpx.MockTransport(handler)}
        )

    monkeypatch.setattr(JarvisClient, "__init__", initialize)
    monkeypatch.setattr(
        config,
        "resolve_profile",
        lambda: SimpleNamespace(base_url=DEFAULT, control_key="fixture-default"),
    )
    return calls, response


def invoke_module(monkeypatch, args):
    monkeypatch.setattr(sys, "argv", ["jarvis.cli_ctl", *args])
    with pytest.raises(SystemExit) as outcome:
        runpy.run_module("jarvis.cli_ctl", run_name="__main__")
    return outcome.value.code


@pytest.mark.parametrize("command", [["system", "status"], ["auth", "status"]])
@pytest.mark.parametrize("equals", [False, True])
def test_module_entry_uses_explicit_base_path_key_and_json(
    monkeypatch, targets, capsys, command, equals
):
    calls, _ = targets
    flags = (
        [f"--url={EXPLICIT}", "--key=fixture-explicit"]
        if equals
        else ["--url", EXPLICIT, "--key", "fixture-explicit"]
    )
    assert invoke_module(monkeypatch, [*flags, "--json", *command]) == 0
    assert calls == [(EXPLICIT + "/api/control/auth/probe", "Bearer fixture-explicit")]
    assert canonical.as_json() is True
    assert json.loads(capsys.readouterr().out)["reachable"] is True


@pytest.mark.parametrize("failure", ["unreachable", "unauthorized"])
@pytest.mark.parametrize("command", [["system", "status"], ["auth", "status"]])
def test_explicit_target_failure_never_probes_a_discovered_server(
    monkeypatch, targets, capsys, failure, command
):
    calls, response = targets
    response.update(unreachable=failure == "unreachable", status=401)
    assert (
        invoke_module(
            monkeypatch, ["--url", EXPLICIT, "--key", "fixture-explicit", "--json", *command]
        )
        == 1
    )
    assert calls == [(EXPLICIT + "/api/control/auth/probe", "Bearer fixture-explicit")]
    assert json.loads(capsys.readouterr().out)["reachable"] is False


def test_auth_local_options_override_root_without_default_target(monkeypatch, targets, capsys):
    calls, _ = targets
    assert (
        invoke_module(
            monkeypatch,
            [
                "--url",
                EXPLICIT,
                "--key",
                "fixture-explicit",
                "--json",
                "auth",
                "status",
                "--url",
                LOCAL,
                "--key",
                "fixture-local",
            ],
        )
        == 0
    )
    assert calls == [(LOCAL + "/api/control/auth/probe", "Bearer fixture-local")]
    assert json.loads(capsys.readouterr().out)["base_url"] == LOCAL


def test_missing_explicit_target_retains_discovery(monkeypatch, targets, capsys):
    calls, _ = targets
    assert invoke_module(monkeypatch, ["--json", "system", "status"]) == 0
    assert calls == [(DEFAULT + "/api/control/auth/probe", "Bearer fixture-default")]
    assert json.loads(capsys.readouterr().out)["reachable"] is True
