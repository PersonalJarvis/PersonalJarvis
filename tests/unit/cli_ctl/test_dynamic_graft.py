import json
import time

import click
import httpx
import pytest
from click.testing import CliRunner

import jarvis.cli_ctl.__main__ as entry
from jarvis.cli_ctl import openapi_cache

SPEC = {
    "openapi": "3.1.0",
    "info": {"version": "1"},
    "paths": {"/api/ping": {"get": {"tags": ["diag"], "operationId": "ping", "summary": "Ping"}}},
}

MARS_SPEC = {
    "openapi": "3.1.0",
    "info": {"version": "mars"},
    "paths": {
        "/api/society/mars/definition": {
            "get": {
                "tags": ["mars"],
                "operationId": "definition",
                "summary": "Read Mars definition",
            }
        }
    },
}


def _mock_transport(monkeypatch, handler) -> None:
    import jarvis.cli_ctl.client as client_mod

    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)


def _no_network(monkeypatch) -> list[str]:
    calls = []

    def handler(req):  # any request on this path is a regression
        calls.append(str(req.url))
        raise AssertionError(f"unexpected network call: {req.url}")

    _mock_transport(monkeypatch, handler)
    return calls


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

    network = _no_network(monkeypatch)
    # Even an `api` invocation stays cache-only while completing.
    root = entry.build_root_command(["api"])
    assert root is not None
    assert not network


def test_non_api_command_never_fetches(monkeypatch, tmp_path):
    """`jarvis version` (or --help) must not pay a spec fetch — ever."""
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")

    network = _no_network(monkeypatch)
    root = entry.build_root_command(["version"])
    assert "api" not in root.commands  # no cache, no fetch -> no dynamic group

    root = entry.build_root_command(["--json", "version"])
    assert root is not None
    assert not network


def test_non_api_command_grafts_from_stale_cache(monkeypatch, tmp_path):
    """A stale cache still lists the `api` group for help — without a refetch."""
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")
    url = entry._config.DEFAULT_BASE_URL
    openapi_cache._write_cache(SPEC, base_url=url)
    path = openapi_cache._cache_path(url)
    cached = json.loads(path.read_text(encoding="utf-8"))
    cached["fetched_at"] = time.time() - 10 * 24 * 3600
    path.write_text(json.dumps(cached), encoding="utf-8")

    network = _no_network(monkeypatch)
    root = entry.build_root_command(["version"])
    assert "api" in root.commands
    assert not network


def test_url_option_value_is_not_mistaken_for_subcommand(monkeypatch, tmp_path):
    """`--url <value>` consumes its value token when locating the subcommand."""
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")

    network = _no_network(monkeypatch)
    # "api" here is the --url VALUE, not the subcommand -> must stay cache-only.
    root = entry.build_root_command(["--url", "api", "version"])
    assert root is not None
    assert entry._first_subcommand(["--url", "api", "version"]) == "version"
    assert entry._first_subcommand(["--json", "api"]) == "api"
    assert entry._first_subcommand([]) is None
    assert not network


@pytest.mark.parametrize(
    "options",
    [
        ["--url", "http://127.0.0.1:47869", "--key", "fixture-selected-key"],
        ["--url=http://127.0.0.1:47869", "--key=fixture-selected-key"],
    ],
)
def test_dynamic_schema_honors_root_url_and_key_before_callback(monkeypatch, tmp_path, options):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_BASE_URL", "http://127.0.0.1:47821")
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "fixture-profile-key")
    # Embedded invocations must not inherit the preceding callback's profile.
    monkeypatch.setitem(entry.STATE, "url", "http://127.0.0.1:47999")
    monkeypatch.setitem(entry.STATE, "key", "fixture-stale-key")
    before = dict(entry.STATE)
    requests = []

    def handler(request):
        requests.append((request.url.port, request.url.path, request.headers.get("Authorization")))
        return httpx.Response(200, json=MARS_SPEC if request.url.port == 47869 else SPEC)

    _mock_transport(monkeypatch, handler)
    argv = [*options, "api", "mars", "--help"]
    root = entry.build_root_command(argv)
    assert entry.STATE == before  # Building the tree did not invoke the root callback.
    assert requests == [(47869, "/api/openapi.json", "Bearer fixture-selected-key")]
    result = CliRunner().invoke(root, argv)
    assert result.exit_code == 0 and "definition" in result.output
    assert "fixture-selected-key" not in result.output
    assert "fixture-selected-key" not in "".join(
        path.read_text(encoding="utf-8") for path in tmp_path.glob("openapi-*.json")
    )


def test_next_invocation_without_overrides_uses_profile_instead_of_old_state(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_BASE_URL", "http://127.0.0.1:47821")
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "fixture-profile-key")
    monkeypatch.setitem(entry.STATE, "url", "http://127.0.0.1:47869")
    monkeypatch.setitem(entry.STATE, "key", "fixture-old-key")
    observed = []

    def handler(request):
        observed.append((request.url.port, request.headers.get("Authorization")))
        return httpx.Response(200, json=SPEC)

    _mock_transport(monkeypatch, handler)
    root = entry.build_root_command(["api", "diag", "--help"])
    assert "api" in root.commands
    assert observed == [(47821, "Bearer fixture-profile-key")]


@pytest.mark.parametrize(
    "argv",
    [
        ["--url", "http://127.0.0.1:47869", "--key", "fixture-key", "version"],
        ["--url=http://127.0.0.1:47869", "--help"],
        ["--help", "api"],
    ],
)
def test_static_commands_and_root_help_remain_network_free(monkeypatch, tmp_path, argv):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    network = _no_network(monkeypatch)
    assert entry.build_root_command(argv) is not None
    assert not network


def test_cache_only_graft_uses_selected_origin_for_help_and_completion(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_BASE_URL", "http://127.0.0.1:47821")
    openapi_cache._write_cache(SPEC, base_url="http://127.0.0.1:47821")
    openapi_cache._write_cache(MARS_SPEC, base_url="http://127.0.0.1:47869")
    network = _no_network(monkeypatch)
    selected = entry.build_root_command(["--url", "http://127.0.0.1:47869", "--help"])
    assert "mars" in selected.commands["api"].commands
    assert "diag" not in selected.commands["api"].commands
    monkeypatch.setenv("_JARVISCTL_COMPLETE", "complete_bash")
    completed = entry.build_root_command(["--url=http://127.0.0.1:47869", "api", "mars"])
    assert "mars" in completed.commands["api"].commands
    foreign = entry.build_root_command(["--url", "http://127.0.0.1:47999", "--help"])
    assert "api" not in foreign.commands
    assert not network


def test_structured_option_parse_never_calls_parameter_or_command_callbacks():
    def forbidden(*args, **kwargs):
        raise AssertionError("schema construction must not invoke callbacks")

    root = click.Group(
        callback=forbidden,
        params=[
            click.Option(["--url"], callback=forbidden),
            click.Option(["--key"], callback=forbidden),
        ],
    )
    values, command = entry._root_options(
        root,
        ["--url=http://127.0.0.1:47869", "--key", "fixture-key", "api", "--url=ignored"],
    )
    assert values == {"url": "http://127.0.0.1:47869", "key": "fixture-key"}
    assert command == "api"


def test_dynamic_build_diagnostics_do_not_log_credentials(monkeypatch, tmp_path, caplog):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    caplog.set_level("DEBUG", logger=entry.__name__)
    credential = "fixture-private-key"

    def handler(_request):
        raise RuntimeError("transport diagnostic contains " + credential)

    _mock_transport(monkeypatch, handler)
    root = entry.build_root_command(["--key", credential, "api"])
    assert "api" not in root.commands
    assert credential not in caplog.text
    assert "RuntimeError" in caplog.text


def test_dynamic_click_usage_error_uses_cli_exit_semantics(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _mock_transport(monkeypatch, lambda _request: httpx.Response(200, json=MARS_SPEC))
    root = entry.build_root_command(["api", "mars", "missing-command"])
    result = CliRunner().invoke(root, ["api", "mars", "missing-command"])
    assert result.exit_code == 2
    assert "No such command" in result.output
    assert "Traceback" not in result.output


def test_dynamic_help_returns_success_in_nonstandalone_mode(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _mock_transport(monkeypatch, lambda _request: httpx.Response(200, json=MARS_SPEC))
    root = entry.build_root_command(["api", "mars", "--help"])
    result = CliRunner().invoke(root, ["api", "mars", "--help"], standalone_mode=False)
    assert result.exit_code == 0
    assert "definition" in result.output
