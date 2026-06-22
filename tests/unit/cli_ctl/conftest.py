import json

import httpx
import pytest


@pytest.fixture(autouse=True)
def _isolate_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path / "cache"))
    # Neutralize live-instance discovery so a really-running Jarvis on the dev
    # box can never leak its port/token into a test. Points at a missing file;
    # discovery tests override this with their own path.
    monkeypatch.setenv("JARVIS_CLI_SESSION_FILE", str(tmp_path / "no-session.json"))
    monkeypatch.delenv("JARVIS_CLI_ASSUME_YES", raising=False)
    for k in ("JARVISCTL_BASE_URL", "JARVISCTL_CONTROL_KEY"):
        monkeypatch.delenv(k, raising=False)
    # Prevent the local control_key fallback from finding a real key in tests.
    monkeypatch.setattr(
        "jarvis.core.control_key.get_control_key", lambda: None, raising=False
    )


@pytest.fixture
def mock_api(monkeypatch):
    """Patch JarvisClient construction to use a MockTransport handler."""
    routes: dict[tuple[str, str], object] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        key = (request.method, request.url.path)
        spec = routes.get(key)
        if spec is None:
            return httpx.Response(404, json={"detail": f"no route {key}"})
        status, payload = spec
        return httpx.Response(status, json=payload)

    import jarvis.cli_ctl.client as client_mod

    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)
    return routes  # tests register routes[("GET","/path")] = (200, {...})


@pytest.fixture
def capture_api(monkeypatch):
    """Like mock_api but records every request and defaults unknown routes to 200.

    Returns a dict with ``calls`` (list of {method, path, query, body}) and
    ``routes`` (override the response for a specific (method, path))."""
    routes: dict[tuple[str, str], object] = {}
    calls: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = None
        if request.content:
            try:
                body = json.loads(request.content)
            except ValueError:
                body = request.content.decode()
        calls.append(
            {
                "method": request.method,
                "path": request.url.path,
                "query": dict(request.url.params),
                "body": body,
            }
        )
        status, payload = routes.get((request.method, request.url.path), (200, {"ok": True}))
        return httpx.Response(status, json=payload)

    import jarvis.cli_ctl.client as client_mod

    real_init = client_mod.JarvisClient.__init__

    def patched_init(self, base_url, control_key, **kw):
        kw["transport"] = httpx.MockTransport(handler)
        real_init(self, base_url, control_key, **kw)

    monkeypatch.setattr(client_mod.JarvisClient, "__init__", patched_init)
    return {"calls": calls, "routes": routes}
