"""Browser launcher diagnostics must never expose pairing or OAuth credentials."""

import logging
import subprocess

from jarvis.platform import open_path


def test_opener_timeout_does_not_log_credential(monkeypatch, caplog):
    url = "https://example.test/enter#private-example-ticket"

    def timeout(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, 8)

    monkeypatch.setattr(open_path.subprocess, "run", timeout)
    with caplog.at_level(logging.DEBUG):
        assert not open_path._run_opener_checked(["open", url])
    assert "private-example-ticket" not in caplog.text
    assert "TimeoutExpired" in caplog.text


def test_browser_success_does_not_log_url_fragment(monkeypatch, caplog):
    monkeypatch.setattr(open_path, "_windows_browser_candidates", lambda: ["browser"])
    monkeypatch.setattr(open_path.subprocess, "Popen", lambda *args, **kwargs: object())
    with caplog.at_level(logging.DEBUG):
        assert open_path._open_url_windows("https://example.test/enter#private-example-ticket")
    assert "private-example-ticket" not in caplog.text
    assert "example.test" in caplog.text
