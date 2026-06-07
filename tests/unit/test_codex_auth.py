"""Unit tests for the rebuilt CodexAuthService.

The original module was lost (only a stub was ever committed); these tests pin
the behaviour the UI + provider routes rely on: an honest status snapshot that
reads ``~/.codex/auth.json`` (or ``$CODEX_HOME``) and reports whether Codex is
connected via the ChatGPT subscription (OAuth) or an OpenAI API key.

No real ``codex`` binary and no network are touched — the binary resolution and
version probe are seams that the tests stub; the auth file is a real temp file.
"""
from __future__ import annotations

import base64
import json
from pathlib import Path

import pytest

from jarvis.codex_auth import CodexAuthService, CodexAuthStatus, _derive_auth


def _write_auth(home: Path, payload: dict) -> None:
    home.mkdir(parents=True, exist_ok=True)
    (home / "auth.json").write_text(json.dumps(payload), encoding="utf-8")


def _jwt_with_email(email: str) -> str:
    """A minimal unsigned JWT whose payload carries an email claim."""
    header = base64.urlsafe_b64encode(b'{"alg":"none"}').rstrip(b"=").decode()
    body = base64.urlsafe_b64encode(
        json.dumps({"email": email}).encode()
    ).rstrip(b"=").decode()
    return f"{header}.{body}.sig"


# ----------------------------------------------------------------------
# _derive_auth — pure (connected, mode) decision
# ----------------------------------------------------------------------


def test_derive_auth_chatgpt_when_oauth_tokens_present() -> None:
    connected, mode = _derive_auth({"tokens": {"access_token": "abc"}})
    assert connected is True
    assert mode == "chatgpt"


def test_derive_auth_api_key_when_openai_key_present() -> None:
    connected, mode = _derive_auth({"OPENAI_API_KEY": "sk-test-123"})
    assert connected is True
    assert mode == "api_key"


def test_derive_auth_unknown_when_empty() -> None:
    assert _derive_auth(None) == (False, "unknown")
    assert _derive_auth({}) == (False, "unknown")
    assert _derive_auth({"OPENAI_API_KEY": ""}) == (False, "unknown")


# ----------------------------------------------------------------------
# CodexAuthStatus — wire contract the frontend + provider_routes consume
# ----------------------------------------------------------------------


def test_status_to_dict_contains_frontend_contract_fields() -> None:
    status = CodexAuthStatus(
        installed=True,
        connected=True,
        mode="chatgpt",
        message="Connected via ChatGPT",
        version="codex 1.2.3",
        accountLabel="ChatGPT/Codex-Login",
    )
    d = status.to_dict()
    for key in ("installed", "connected", "mode", "message", "version"):
        assert key in d, f"to_dict() must expose {key!r} for the UI"
    assert d["mode"] == "chatgpt"
    assert d["message"] == "Connected via ChatGPT"


# ----------------------------------------------------------------------
# CodexAuthService.status() — composes binary + auth.json
# ----------------------------------------------------------------------


def test_status_not_installed_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: None)
    status = svc.status()
    assert status.installed is False
    assert status.connected is False
    assert status.mode == "unknown"
    assert status.message  # never empty — UI shows this instead of "loading"


def test_status_detects_chatgpt_from_auth_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, {"tokens": {"access_token": "live-token"}})
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()
    assert status.installed is True
    assert status.connected is True
    assert status.mode == "chatgpt"
    assert status.version == "codex 1.2.3"


def test_status_detects_api_key_from_auth_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(tmp_path, {"OPENAI_API_KEY": "sk-codex-xyz"})
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()
    assert status.connected is True
    assert status.mode == "api_key"


def test_status_unknown_when_auth_file_absent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))  # dir exists, no auth.json
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()
    assert status.installed is True
    assert status.connected is False
    assert status.mode == "unknown"


def test_status_extracts_email_from_id_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    _write_auth(
        tmp_path,
        {"tokens": {"access_token": "x", "id_token": _jwt_with_email("you@example.com")}},
    )
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()
    assert status.user_email == "you@example.com"


def test_status_tolerates_corrupt_auth_json(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CODEX_HOME", str(tmp_path))
    tmp_path.mkdir(parents=True, exist_ok=True)
    (tmp_path / "auth.json").write_text("{ this is not json", encoding="utf-8")
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")
    monkeypatch.setattr(svc, "_probe_version", lambda _b: "codex 1.2.3")
    status = svc.status()  # must not raise
    assert status.connected is False
    assert status.mode == "unknown"


# ----------------------------------------------------------------------
# start_login / logout — spawn discipline (cross-platform, AP-1)
# ----------------------------------------------------------------------


def test_start_login_raises_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: None)
    with pytest.raises(FileNotFoundError):
        svc.start_login()


def test_start_login_posix_detaches_and_redirects_stdio(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """On a headless host the child must NOT inherit the server's stdio and must
    run in its own session (no zombie, no garbled HTTP stream)."""
    import subprocess as sp

    monkeypatch.setattr("jarvis.codex_auth.sys.platform", "linux")
    captured: dict = {}

    class _FakeProc:
        pid = 4321

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr("jarvis.codex_auth.subprocess.Popen", _fake_popen)
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")

    proc = svc.start_login()
    assert proc.pid == 4321
    assert captured["cmd"] == ["codex", "login"]
    assert captured["kwargs"].get("stdout") is sp.DEVNULL
    assert captured["kwargs"].get("stderr") is sp.DEVNULL
    assert captured["kwargs"].get("start_new_session") is True


def test_start_login_windows_uses_visible_console(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows: a fresh visible console for the device URL — stdout NOT to DEVNULL."""
    import subprocess as sp

    monkeypatch.setattr("jarvis.codex_auth.sys.platform", "win32")
    captured: dict = {}

    class _FakeProc:
        pid = 9

    def _fake_popen(cmd, **kwargs):  # noqa: ANN001, ANN002, ANN003
        captured["kwargs"] = kwargs
        return _FakeProc()

    monkeypatch.setattr("jarvis.codex_auth.subprocess.Popen", _fake_popen)
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: "codex")

    svc.start_login()
    assert "creationflags" in captured["kwargs"]
    assert captured["kwargs"].get("stdout") is not sp.DEVNULL


def test_logout_returns_false_when_binary_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    svc = CodexAuthService()
    monkeypatch.setattr(svc, "_resolve_binary", lambda: None)
    ok, err = svc.logout_blocking()
    assert ok is False
    assert err
