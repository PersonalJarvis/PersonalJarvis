import httpx
from click.testing import CliRunner

import jarvis.cli_ctl.__main__ as entry

SPEC = {
    "openapi": "3.1.0", "info": {"version": "1"},
    "paths": {"/api/ping": {"get": {"tags": ["diag"], "operationId": "ping",
              "summary": "Ping"}}},
}


def test_grafted_root_has_api_group(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("JARVISCTL_CONTROL_KEY", "jctl_x")

    def handler(req):
        if req.url.path == "/api/openapi.json":
            return httpx.Response(200, json=SPEC)
        return httpx.Response(200, json={"pong": True})

    import jarvis.cli_ctl.client as client_mod
    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)

    root = entry.build_root_command()  # builds Typer root + grafts api group
    res = CliRunner().invoke(root, ["api", "diag", "ping"])
    assert res.exit_code == 0
    assert "pong" in res.output


def test_completion_marker_skips_network(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("_JARVISCTL_COMPLETE", "complete_bash")  # completion in flight

    def handler(req):  # must NOT be called during completion
        raise AssertionError("network during completion")

    import jarvis.cli_ctl.client as client_mod
    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)
    # Should not raise: no cache + completion marker => no fetch, no api group.
    root = entry.build_root_command()
    assert root is not None
