"""Failure and isolation guarantees for the native-package acceptance probe."""

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import pytest

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
