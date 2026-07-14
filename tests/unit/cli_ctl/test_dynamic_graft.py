import json
import time

import httpx
from click.testing import CliRunner

import jarvis.cli_ctl.__main__ as entry
from jarvis.cli_ctl import openapi_cache, paths

SPEC = {
    "openapi": "3.1.0", "info": {"version": "1"},
    "paths": {"/api/ping": {"get": {"tags": ["diag"], "operationId": "ping",
              "summary": "Ping"}}},
}


def _mock_transport(monkeypatch, handler) -> None:
    import jarvis.cli_ctl.client as client_mod
    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)


def _no_network(monkeypatch) -> None:
    def handler(req):  # any request on this path is a regression
        raise AssertionError(f"unexpected network call: {req.url}")

    _mock_transport(monkeypatch, handler)


def test_grafted_root_has_api_group(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")

    def handler(req):
        if req.url.path == "/api/openapi.json":
            return httpx.Response(200, json=SPEC)
        return httpx.Response(200, json={"pong": True})

    _mock_transport(monkeypatch, handler)

    # An `api` invocation is the one path allowed to fetch the spec.
    root = entry.build_root_command(["api", "diag", "ping"])
    res = CliRunner().invoke(root, ["api", "diag", "ping"])
    assert res.exit_code == 0
    assert "pong" in res.output


def test_completion_marker_skips_network(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("_JARVISCTL_COMPLETE", "complete_bash")  # completion in flight

    _no_network(monkeypatch)
    # Even an `api` invocation stays cache-only while completing.
    root = entry.build_root_command(["api"])
    assert root is not None


def test_non_api_command_never_fetches(monkeypatch, tmp_path):
    """`jarvis version` (or --help) must not pay a spec fetch — ever."""
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")

    _no_network(monkeypatch)
    root = entry.build_root_command(["version"])
    assert "api" not in root.commands  # no cache, no fetch -> no dynamic group

    root = entry.build_root_command(["--json", "version"])
    assert root is not None


def test_non_api_command_grafts_from_stale_cache(monkeypatch, tmp_path):
    """A stale cache still lists the `api` group for help — without a refetch."""
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    openapi_cache._write_cache(SPEC)
    paths.openapi_meta_file().write_text(
        json.dumps({"fetched_at": time.time() - 10 * 24 * 3600}), encoding="utf-8"
    )

    _no_network(monkeypatch)
    root = entry.build_root_command(["version"])
    assert "api" in root.commands


def test_url_option_value_is_not_mistaken_for_subcommand(monkeypatch, tmp_path):
    """`--url <value>` consumes its value token when locating the subcommand."""
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")

    _no_network(monkeypatch)
    # "api" here is the --url VALUE, not the subcommand -> must stay cache-only.
    root = entry.build_root_command(["--url", "api", "version"])
    assert root is not None
    assert entry._first_subcommand(["--url", "api", "version"]) == "version"
    assert entry._first_subcommand(["--json", "api"]) == "api"
    assert entry._first_subcommand([]) is None
