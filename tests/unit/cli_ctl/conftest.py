import httpx
import pytest


@pytest.fixture(autouse=True)
def _isolate_dirs(monkeypatch, tmp_path):
    monkeypatch.setenv("JARVISCTL_CONFIG_HOME", str(tmp_path / "cfg"))
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path / "cache"))
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
