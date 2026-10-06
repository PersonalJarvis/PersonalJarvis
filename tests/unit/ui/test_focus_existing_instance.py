"""The launcher's focus ping must authenticate so CSRF does not reject it.

Live 2026-08-25: a headless instance held port 47821, the desktop launch POSTed
``/api/window/focus`` with no Origin and no Bearer, SurfaceSecurity answered
403 ``Untrusted Origin header.``, and the start reported "already running"
with no window.
"""

from __future__ import annotations


class _Resp:
    def __init__(self, status_code=200, payload=None, json_error=False):
        self.status_code = status_code
        self._payload = payload
        self._json_error = json_error

    def json(self):
        if self._json_error:
            raise ValueError("no body")
        return self._payload


def test_focus_response_requires_a_raised_window():
    from jarvis.ui import desktop_app

    assert desktop_app._focus_response_means_window_raised(
        _Resp(payload={"ok": True, "focused": True})
    )
    assert not desktop_app._focus_response_means_window_raised(
        _Resp(payload={"ok": True, "focused": False})
    )
    assert not desktop_app._focus_response_means_window_raised(
        _Resp(payload={"ok": False, "focused": False, "reason": "no_window"})
    )
    assert desktop_app._focus_response_means_window_raised(_Resp(payload={"ok": True}))
    assert not desktop_app._focus_response_means_window_raised(_Resp(status_code=403))


def test_a_window_the_foreground_lock_kept_back_still_counts():
    """Live 2026-10-06: a healthy instance answered "shown, but Windows would
    not activate it", and the launch called it stuck and offered to kill it."""
    from jarvis.ui import desktop_app

    assert desktop_app._focus_response_means_window_raised(
        _Resp(payload={"ok": True, "focused": False, "shown": True, "reason": "foreground_lock"})
    )
    # Holders from before the fix sent only the reason, after showing the window.
    assert desktop_app._focus_response_means_window_raised(
        _Resp(payload={"ok": False, "focused": False, "reason": "foreground_lock"})
    )
    assert not desktop_app._focus_response_means_window_raised(
        _Resp(payload={"ok": False, "focused": False, "shown": False, "reason": "no_window"})
    )


def test_the_launch_hands_its_foreground_right_to_the_running_instance(monkeypatch):
    from types import ModuleType

    from jarvis.ui import desktop_app

    order: list[str] = []
    granted: list[object] = []

    def _grant(pid=None):
        order.append("grant")
        granted.append(pid)
        return True

    def _post(url, headers=None, timeout=1.0):  # noqa: ARG001
        order.append("post")
        return _Resp(payload={"ok": True, "focused": True})

    monkeypatch.setattr("jarvis.ui.foreground_grant.allow_foreground", _grant)
    monkeypatch.setattr(desktop_app, "_read_meta", lambda: {"pid": 4242, "port": 47821})
    monkeypatch.setattr(desktop_app, "_focus_request_headers", lambda port: {})
    monkeypatch.setattr(desktop_app, "_bring_window_to_front_by_title", lambda _t: True)
    fake = ModuleType("httpx")
    fake.post = _post  # type: ignore[attr-defined]
    monkeypatch.setitem(__import__("sys").modules, "httpx", fake)

    assert desktop_app.focus_existing_instance_robust() is True
    assert granted == [4242]
    assert order[:2] == ["grant", "post"]


def test_allow_foreground_is_a_quiet_no_op_off_windows(monkeypatch):
    from jarvis.ui import foreground_grant

    monkeypatch.setattr(foreground_grant.sys, "platform", "linux")
    assert foreground_grant.allow_foreground(1234) is False


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
