import json

import httpx

from jarvis.cli_ctl import openapi_cache as oc

SPEC = {"openapi": "3.1.0", "info": {"version": "1"}, "paths": {}}


def _client(handler):
    from jarvis.cli_ctl.client import JarvisClient

    return JarvisClient("http://t", "jctl_k", transport=httpx.MockTransport(handler))


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
    (tmp_path / "openapi.json").write_text(json.dumps(SPEC), encoding="utf-8")
    (tmp_path / "openapi.meta.json").write_text(
        json.dumps({"fetched_at": 0}),
        encoding="utf-8",
    )

    def handler(req):
        raise httpx.ConnectError("down")

    spec = oc.load_spec(_client(handler), ttl_seconds=0)  # force revalidation
    assert spec is not None and spec["info"]["version"] == "1"


def test_stale_cache_refetches_when_reachable(tmp_path, monkeypatch):
    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    (tmp_path / "openapi.json").write_text(
        json.dumps({"openapi": "3.1.0", "info": {"version": "old"}, "paths": {}}),
        encoding="utf-8",
    )
    (tmp_path / "openapi.meta.json").write_text(
        json.dumps({"fetched_at": 0}), encoding="utf-8"
    )
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
    import time

    monkeypatch.setenv("JARVISCTL_CACHE_HOME", str(tmp_path))
    (tmp_path / "openapi.json").write_text(
        json.dumps({"openapi": "3.1.0", "info": {"version": "old"}, "paths": {}}),
        encoding="utf-8",
    )
    (tmp_path / "openapi.meta.json").write_text(
        json.dumps({"fetched_at": time.time() + 10 * 24 * 3600}), encoding="utf-8"
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
    oc.clear_cache()
    assert not (tmp_path / "openapi.json").exists()
    assert not (tmp_path / "openapi.meta.json").exists()
