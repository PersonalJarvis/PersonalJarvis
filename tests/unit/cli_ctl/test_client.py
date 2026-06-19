# tests/unit/cli_ctl/test_client.py
import httpx
import pytest

from jarvis.cli_ctl.client import ApiError, JarvisClient


def _client(handler) -> JarvisClient:
    transport = httpx.MockTransport(handler)
    return JarvisClient(
        base_url="http://test", control_key="jctl_k", transport=transport
    )


def test_sends_bearer_header_and_returns_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        return httpx.Response(200, json={"ok": True})

    out = _client(handler).request("GET", "/api/control/auth/probe")
    assert out == {"ok": True}
    assert seen["auth"] == "Bearer jctl_k"


def test_http_error_raises_apierror_with_status_and_detail():
    def handler(request):
        return httpx.Response(404, json={"detail": "nope"})

    with pytest.raises(ApiError) as ei:
        _client(handler).request("GET", "/api/tasks/x")
    assert ei.value.status_code == 404
    assert "nope" in ei.value.message


def test_connect_error_raises_apierror_unreachable():
    def handler(request):
        raise httpx.ConnectError("down")

    with pytest.raises(ApiError) as ei:
        _client(handler).request("GET", "/api/tasks")
    assert ei.value.status_code is None  # transport failure, not an HTTP status
    assert "unreachable" in ei.value.message.lower()
