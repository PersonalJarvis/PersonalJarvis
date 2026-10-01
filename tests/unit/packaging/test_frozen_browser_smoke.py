"""Failure and isolation guarantees for the native-package acceptance probe."""

import stat
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

from jarvis.core.instance import DEV_PORT_OFFSET
from scripts.ci import check_frozen_browser as probe


def test_probe_does_not_inherit_account_or_runtime_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "must-not-reach-frozen-app")
    monkeypatch.setenv("JARVIS_CONFIG", "existing-user-config")
    monkeypatch.setenv("GROK_AUTH_PATH", "existing-subscription")
    monkeypatch.setenv("PYTHONPATH", "source-checkout")
    env = probe.isolated_env(tmp_path, 50000, "isolated-control-key")
    assert not {"OPENAI_API_KEY", "GROK_AUTH_PATH", "PYTHONPATH"} & env.keys()
    assert env["JARVIS_CONFIG"] == str(tmp_path / "jarvis.toml")
    assert env["JARVIS_CONTROL_API_KEY"] == "isolated-control-key"
    assert env["PYTHON_KEYRING_BACKEND"] == "keyring.backends.null.Keyring"


def test_probe_refuses_http_redirect_instead_of_forwarding_its_key():
    class Redirect(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(302)
            self.send_header("Location", "http://127.0.0.1:1/unrelated")
            self.end_headers()

        def log_message(self, *_args):
            pass  # Test traffic is intentionally quiet.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Redirect)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        with pytest.raises(probe.ProbeHTTPError) as error:
            probe.request_json(server.server_port, "/api/health", "test-key")
        assert error.value.code == 302
    finally:
        server.shutdown()
        thread.join(timeout=5)
        server.server_close()


def test_failed_stream_cannot_count_as_first_frame(tmp_path, monkeypatch):
    class FailedViewer:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def recv(self, **_kwargs):
            return '{"kind":"error","detail":"private runtime failure"}'

    monkeypatch.setattr(probe, "connect", lambda *_args, **_kwargs: FailedViewer())
    with pytest.raises(RuntimeError, match="failed before its first frame"):
        probe.capture_frame(50000, "test-key", tmp_path / "frame.jpg")
    assert not (tmp_path / "frame.jpg").exists()


def test_first_install_requires_a_fresh_profile(tmp_path, monkeypatch):
    (tmp_path / "profile").mkdir()
    monkeypatch.setattr(
        probe.sys,
        "argv",
        ["probe", "--executable", __file__, "--output", str(tmp_path)],
    )
    with pytest.raises(FileExistsError):
        probe.main()


def _snapshot(microphone: str | None, bundle_id: str | None) -> dict:
    rows = [] if microphone is None else [{"id": "microphone", "status": microphone}]
    return {"permissions": rows, "app_identity": {"bundle_id": bundle_id}}


def test_permission_check_asks_nothing_off_macos(monkeypatch):
    """Windows and Linux have no permission port to ask."""
    monkeypatch.setattr(probe.sys, "platform", "linux")

    def explode(*_args, **_kwargs):
        raise AssertionError("no request may be made off macOS")

    monkeypatch.setattr(probe, "request_json", explode)

    assert probe.check_macos_permissions(50000, "test-key") == {}


def test_a_frozen_mac_app_that_can_read_its_microphone_passes(monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    monkeypatch.setattr(
        probe,
        "request_json",
        lambda *_args: _snapshot("not_determined", probe.ACCEPTED_BUNDLE_IDS[-1]),
    )

    assert probe.check_macos_permissions(50000, "test-key") == {
        "microphone_permission": "not_determined",
        "bundle_id": probe.ACCEPTED_BUNDLE_IDS[-1],
    }


@pytest.mark.parametrize("microphone", ["unavailable", None])
def test_a_frozen_mac_app_without_its_microphone_framework_fails(monkeypatch, microphone):
    """The v2.5.0 image: AVFoundation missing, so the row reads 'unavailable' for good."""
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    monkeypatch.setattr(
        probe,
        "request_json",
        lambda *_args: _snapshot(microphone, probe.ACCEPTED_BUNDLE_IDS[0]),
    )

    with pytest.raises(RuntimeError, match="AVFoundation"):
        probe.check_macos_permissions(50000, "test-key")


def test_a_frozen_mac_app_with_a_foreign_bundle_id_fails(monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "darwin")
    monkeypatch.setattr(
        probe,
        "request_json",
        lambda *_args: _snapshot("denied", "com.example.other"),
    )

    with pytest.raises(RuntimeError, match="com.example.other"):
        probe.check_macos_permissions(50000, "test-key")


def test_a_missing_permission_route_fails_at_once_but_a_starting_app_is_retried(monkeypatch):
    monkeypatch.setattr(probe.sys, "platform", "darwin")

    def respond(code):
        def request(*_args):
            raise probe.ProbeHTTPError(code)

        return request

    monkeypatch.setattr(probe, "request_json", respond(404))
    with pytest.raises(RuntimeError, match="does not serve"):
        probe.check_macos_permissions(50000, "test-key")

    # 503 is "still starting": the boot loop retries it, so it must pass through.
    monkeypatch.setattr(probe, "request_json", respond(503))
    with pytest.raises(probe.ProbeHTTPError) as starting:
        probe.check_macos_permissions(50000, "test-key")
    assert starting.value.code == 503


# A stand-in for the frozen app: `<exe> serve` answers the three requests the
# smoke makes, with the permission status a given build would report.
_FAKE_APP = """#!{python}
import json, os, re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

config = open(os.environ["JARVIS_CONFIG"], encoding="utf-8").read()
port = int(re.search(r"admin_api_port\\s*=\\s*(\\d+)", config).group(1)) + {offset}
mode = "{mode}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.headers.get("Authorization") != "Bearer " + os.environ["JARVIS_CONTROL_API_KEY"]:
            return self.reply(401, {{}})
        if self.path == "/api/health":
            return self.reply(200, {{"ok": True, "instance": "dev"}})
        if self.path == "/api/society/browser/status":
            return self.reply(200, {{"phase": "ready", "installed": True}})
        if self.path == "/api/permissions/status" and mode != "missing-route":
            microphone = "unavailable" if mode == "no-avfoundation" else "not_determined"
            return self.reply(200, {{
                "permissions": [{{"id": "microphone", "status": microphone}}],
                "app_identity": {{"bundle_id": "ai.personaljarvis.desktop"}},
            }})
        return self.reply(404, {{}})

    def reply(self, code, body):
        data = json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        pass


ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
"""


def _boot_fake_app(tmp_path, monkeypatch, *, platform, mode):
    app = tmp_path / "fake-app"
    app.write_text(
        _FAKE_APP.format(python=sys.executable, offset=DEV_PORT_OFFSET, mode=mode),
        encoding="utf-8",
    )
    app.chmod(app.stat().st_mode | stat.S_IXUSR)
    profile = tmp_path / "profile"
    profile.mkdir()
    monkeypatch.setattr(probe.sys, "platform", platform)
    monkeypatch.setattr(probe, "capture_frame", lambda *_args, **_kwargs: {})
    return probe.boot(app, profile, tmp_path / "first-install", "test-key", timeout=60)


posix_only = pytest.mark.skipif(
    sys.platform == "win32", reason="the stand-in app is a POSIX shebang script"
)


@posix_only
def test_a_mac_boot_records_the_permission_check(tmp_path, monkeypatch):
    report = _boot_fake_app(tmp_path, monkeypatch, platform="darwin", mode="ok")

    assert report["permissions"] == {
        "microphone_permission": "not_determined",
        "bundle_id": "ai.personaljarvis.desktop",
    }
    assert "healthy_seconds" in report


@posix_only
def test_a_mac_boot_without_the_microphone_framework_fails_instead_of_passing(
    tmp_path, monkeypatch
):
    with pytest.raises(RuntimeError, match="AVFoundation"):
        _boot_fake_app(tmp_path, monkeypatch, platform="darwin", mode="no-avfoundation")


@posix_only
def test_a_mac_boot_without_the_permission_route_fails_instead_of_waiting(tmp_path, monkeypatch):
    with pytest.raises(RuntimeError, match="does not serve"):
        _boot_fake_app(tmp_path, monkeypatch, platform="darwin", mode="missing-route")


@posix_only
def test_a_boot_off_macos_never_asks_for_permissions(tmp_path, monkeypatch):
    report = _boot_fake_app(tmp_path, monkeypatch, platform="linux", mode="no-avfoundation")

    assert report["permissions"] == {}
