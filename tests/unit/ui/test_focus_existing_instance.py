"""The launcher's focus ping must authenticate so CSRF does not reject it.

Live 2026-08-25: a headless instance held port 47821, the desktop launch POSTed
``/api/window/focus`` with no Origin and no Bearer, SurfaceSecurity answered
403 ``Untrusted Origin header.``, and the start reported "already running"
with no window.
"""

from __future__ import annotations


def test_focus_headers_send_the_control_key_when_one_exists(monkeypatch):
    from jarvis.ui import desktop_app

    monkeypatch.setattr("jarvis.core.control_key.get_control_key", lambda: "jctl_test_key")
    headers = desktop_app._focus_request_headers(47821)
    assert headers["Authorization"] == "Bearer jctl_test_key"
    assert "Origin" not in headers


def test_focus_headers_fall_back_to_loopback_origin_without_a_key(monkeypatch):
    from jarvis.ui import desktop_app

    monkeypatch.setattr("jarvis.core.control_key.get_control_key", lambda: None)
    headers = desktop_app._focus_request_headers(47821)
    assert headers["Origin"] == "http://127.0.0.1:47821"
    assert "Authorization" not in headers


def test_focus_existing_instance_sends_those_headers(monkeypatch):
    from types import ModuleType

    from jarvis.ui import desktop_app

    seen: list[dict] = []

    class _Resp:
        status_code = 200

    def _post(url, headers=None, timeout=1.0):  # noqa: ARG001
        seen.append({"url": url, "headers": dict(headers or {})})
        return _Resp()

    monkeypatch.setattr(desktop_app, "_read_meta", lambda: {"pid": 1, "port": 47821})
    monkeypatch.setattr(
        desktop_app,
        "_focus_request_headers",
        lambda port: {"Authorization": "Bearer x"},
    )
    fake = ModuleType("httpx")
    fake.post = _post  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake)

    assert desktop_app._focus_existing_instance() is True
    assert seen == [
        {
            "url": "http://127.0.0.1:47821/api/window/focus",
            "headers": {"Authorization": "Bearer x"},
        }
    ]
