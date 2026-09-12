import json
import time

import httpx
import pytest

from jarvis.cli_ctl import openapi_cache as oc

SPEC = {"openapi": "3.1.0", "info": {"version": "1"}, "paths": {}}


def _client(handler, *, base_url="http://t", control_key="jctl_k"):
    from jarvis.cli_ctl.client import JarvisClient

    return JarvisClient(base_url, control_key, transport=httpx.MockTransport(handler))


def _cached(spec=SPEC, *, base_url="http://t", fetched_at=0):
    oc._write_cache(spec, base_url=base_url)
    path = oc._cache_path(base_url)
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["fetched_at"] = fetched_at
    path.write_text(json.dumps(entry), encoding="utf-8")


def test_fetches_and_caches(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=SPEC)

    spec = oc.load_spec(_client(handler))
    assert spec["info"]["version"] == "1"
    assert calls["n"] == 1
    # Second call within TTL hits disk, no new request.
    spec2 = oc.load_spec(_client(handler))
    assert spec2["info"]["version"] == "1"
    assert calls["n"] == 1


def test_unreachable_falls_back_to_stale_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _cached()

    def handler(req):
        raise httpx.ConnectError("down")

    spec = oc.load_spec(_client(handler), ttl_seconds=0)  # force revalidation
    assert spec is not None and spec["info"]["version"] == "1"


def test_stale_cache_refetches_when_reachable(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _cached({"openapi": "3.1.0", "info": {"version": "old"}, "paths": {}})
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=SPEC)  # info.version == "1"

    # TTL=0 forces revalidation: the stale "old" spec is replaced by the fetch.
    spec = oc.load_spec(_client(handler), ttl_seconds=0)
    assert spec["info"]["version"] == "1"
    assert calls["n"] == 1


def test_no_cache_and_unreachable_returns_none(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))

    def handler(req):
        raise httpx.ConnectError("down")

    assert oc.load_spec(_client(handler)) is None


def test_future_fetched_at_is_treated_as_stale(tmp_path, monkeypatch):
    """Backward clock skew (VM restore) must not pin the cache 'fresh' forever."""
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _cached(
        {"openapi": "3.1.0", "info": {"version": "old"}, "paths": {}},
        fetched_at=time.time() + 10 * 24 * 3600,
    )
    calls = {"n": 0}

    def handler(req):
        calls["n"] += 1
        return httpx.Response(200, json=SPEC)  # info.version == "1"

    spec = oc.load_spec(_client(handler))  # default TTL; age is negative
    assert spec["info"]["version"] == "1"
    assert calls["n"] == 1


def test_refresh_clears_cache(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    (tmp_path / "openapi.json").write_text("{}", encoding="utf-8")
    (tmp_path / "openapi.meta.json").write_text("{}", encoding="utf-8")
    oc._write_cache(SPEC, base_url="http://127.0.0.1:47821")
    oc._write_cache(SPEC, base_url="http://127.0.0.1:47869")
    (tmp_path / "unrelated.json").write_text("{}", encoding="utf-8")
    oc.clear_cache()
    assert not (tmp_path / "openapi.json").exists()
    assert not (tmp_path / "openapi.meta.json").exists()
    assert not list(tmp_path.glob("openapi-*.json"))
    assert (tmp_path / "unrelated.json").exists()


def test_two_server_origins_keep_separate_fresh_and_offline_schemas(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    first_url, second_url = "http://127.0.0.1:47821", "http://127.0.0.1:47869"
    second_spec = {**SPEC, "info": {"version": "mars"}}
    calls = []

    def handler(request):
        calls.append(request.url.port)
        return httpx.Response(200, json=SPEC if request.url.port == 47821 else second_spec)

    with (
        _client(handler, base_url=first_url) as first,
        _client(handler, base_url=second_url) as second,
    ):
        assert oc.load_spec(first) == SPEC
        assert oc.load_spec(second) == second_spec
        assert oc.load_spec(first) == SPEC
        assert oc.load_spec(second) == second_spec
    assert calls == [47821, 47869]

    def offline(_request):
        raise httpx.ConnectError("fixture offline")

    with (
        _client(offline, base_url=first_url) as first,
        _client(offline, base_url=second_url) as second,
    ):
        assert oc.load_spec(first, ttl_seconds=0) == SPEC
        assert oc.load_spec(second, ttl_seconds=0) == second_spec


def test_an_unseen_offline_origin_never_receives_another_servers_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    oc._write_cache(SPEC, base_url="http://127.0.0.1:47821")

    def offline(_request):
        raise httpx.ConnectError("fixture offline")

    with _client(offline, base_url="http://127.0.0.1:47869") as other:
        assert oc.load_spec(other) is None


def test_legacy_unbound_cache_is_not_trusted_for_offline_commands(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    (tmp_path / "openapi.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (tmp_path / "openapi.meta.json").write_text(
        json.dumps({"fetched_at": time.time()}), encoding="utf-8"
    )

    def offline(_request):
        raise httpx.ConnectError("fixture offline")

    with _client(offline) as client:
        assert oc.load_spec(client) is None


def test_origin_metadata_and_cache_filenames_never_contain_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    url = (
        "https://fixture-user:fixture-password@EXAMPLE.test:443/?key=fixture-query#fixture-fragment"
    )
    with _client(
        lambda _request: httpx.Response(200, json=SPEC),
        base_url=url,
        control_key="fixture-control-key",
    ) as client:
        assert oc.load_spec(client) == SPEC
    stored = "".join(path.name + path.read_text(encoding="utf-8") for path in tmp_path.iterdir())
    for private in (
        "fixture-user",
        "fixture-password",
        "fixture-query",
        "fixture-fragment",
        "fixture-control-key",
    ):
        assert private not in stored
    spec, meta = oc._read_cache(base_url="https://example.test")
    assert spec == SPEC and meta["origin"] == "https://example.test"


def test_mismatched_origin_metadata_is_rejected_even_in_matching_filename(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _cached()
    path = oc._cache_path("http://t")
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["origin"] = "http://other"
    path.write_text(json.dumps(entry), encoding="utf-8")
    assert oc._read_cache(base_url="http://t") == (None, {})


def test_http_credential_refusal_is_not_offline_cache_fallback(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _cached()
    with _client(lambda _request: httpx.Response(401, json={"detail": "unauthorized"})) as client:
        assert oc.load_spec(client, ttl_seconds=0) is None


def test_two_base_paths_on_one_origin_keep_separate_schemas(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    primary_url, mars_url = "http://127.0.0.1:47869/primary", "http://127.0.0.1:47869/mars"
    mars_spec = {**SPEC, "info": {"version": "mars"}}
    requests = []

    def handler(request):
        requests.append(request.url.path)
        return httpx.Response(
            200, json=SPEC if request.url.path.startswith("/primary/") else mars_spec
        )

    with (
        _client(handler, base_url=primary_url) as primary,
        _client(handler, base_url=mars_url) as mars,
    ):
        assert oc.load_spec(primary) == SPEC
        assert oc.load_spec(mars) == mars_spec
        assert oc.load_spec(primary) == SPEC
        assert oc.load_spec(mars) == mars_spec
    assert requests == ["/primary/api/openapi.json", "/mars/api/openapi.json"]
    assert oc._cache_path(primary_url) != oc._cache_path(mars_url)

    def offline(_request):
        raise httpx.ConnectError("fixture offline")

    with (
        _client(offline, base_url=primary_url + "/") as primary,
        _client(offline, base_url=mars_url) as mars,
    ):
        assert oc.load_spec(primary, ttl_seconds=0) == SPEC
        assert oc.load_spec(mars, ttl_seconds=0) == mars_spec
    with _client(offline, base_url="http://127.0.0.1:47869/another") as unseen:
        assert oc.load_spec(unseen) is None


def test_base_path_normalization_preserves_httpx_request_boundaries():
    assert oc._cache_path("https://example.test/mars") == oc._cache_path(
        "https://EXAMPLE.test:443/mars/"
    )
    assert oc._cache_path("https://example.test/mars/") != oc._cache_path(
        "https://example.test/mars//"
    )
    assert oc._cache_path("https://example.test/a%2Fb") != oc._cache_path(
        "https://example.test/a/b"
    )


@pytest.mark.parametrize(
    "status,payload",
    [
        (200, {"detail": "login required"}),
        (200, []),
        (200, "<html>Login</html>"),
        (200, {"openapi": "3.1.0", "info": {}, "paths": []}),
        (302, "<html>Redirecting to login</html>"),
    ],
)
def test_reachable_invalid_schema_never_uses_or_caches_stale_commands(
    tmp_path, monkeypatch, status, payload
):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _cached()

    def handler(_request):
        if isinstance(payload, str):
            return httpx.Response(status, text=payload, headers={"Location": "/login"})
        return httpx.Response(status, json=payload)

    with _client(handler) as client:
        assert oc.load_spec(client, ttl_seconds=0) is None
    # The invalid response did not replace the known schema in the offline cache.
    assert oc._read_cache(base_url="http://t")[0] == SPEC


def test_invalid_cache_payload_and_direct_invalid_write_are_rejected(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    _cached()
    path = oc._cache_path("http://t")
    entry = json.loads(path.read_text(encoding="utf-8"))
    entry["spec"] = {"detail": "login required"}
    path.write_text(json.dumps(entry), encoding="utf-8")
    assert oc._read_cache(base_url="http://t") == (None, {})
    with pytest.raises(ValueError, match="invalid OpenAPI schema"):
        oc._write_cache({"detail": "login required"}, base_url="http://t")
