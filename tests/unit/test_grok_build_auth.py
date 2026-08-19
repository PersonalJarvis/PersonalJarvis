"""Grok Build auth service — status / login parsing, isolated worker home."""
from __future__ import annotations

import json
from pathlib import Path

from jarvis.grok_build_auth import (
    GrokBuildAuthService,
    GrokBuildAuthStatus,
    _derive_auth,
    _email_from_auth,
    grok_build_install_command,
    grok_build_login_in,
    grok_build_provider_ready,
    prepare_worker_home,
)


def test_derive_auth_subscription_tokens() -> None:
    connected, mode = _derive_auth(
        {"tokens": {"access_token": "tok", "refresh_token": "ref"}}
    )
    assert connected is True
    assert mode == "subscription"


def test_derive_auth_api_key_field() -> None:
    connected, mode = _derive_auth({"XAI_API_KEY": "xai-test"})
    assert connected is True
    assert mode == "api_key"


def test_derive_auth_empty() -> None:
    assert _derive_auth({}) == (False, "unknown")
    assert _derive_auth(None) == (False, "unknown")


def test_email_from_user_object() -> None:
    assert _email_from_auth({"user": {"email": "ada@example.com"}}) == "ada@example.com"


def test_login_in_reads_one_home(tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "tok"}, "user": {"email": "ada@example.com"}}),
        encoding="utf-8",
    )
    connected, mode, email = grok_build_login_in(tmp_path)
    assert connected is True
    assert mode == "subscription"
    assert email == "ada@example.com"


def test_login_in_missing_file(tmp_path: Path) -> None:
    assert grok_build_login_in(tmp_path) == (False, "unknown", None)


def test_provider_ready_requires_installed_subscription() -> None:
    assert grok_build_provider_ready(GrokBuildAuthStatus()) is False
    assert (
        grok_build_provider_ready(
            GrokBuildAuthStatus(installed=True, connected=True, mode="api_key")
        )
        is False
    )
    assert (
        grok_build_provider_ready(
            GrokBuildAuthStatus(installed=True, connected=True, mode="subscription")
        )
        is True
    )


def test_install_command_is_os_specific() -> None:
    assert "install.ps1" in grok_build_install_command("win32")
    assert "install.sh" in grok_build_install_command("linux")


def test_prepare_worker_home_copies_auth_not_hooks(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dest = tmp_path / "dest"
    src.mkdir()
    (src / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "tok"}}),
        encoding="utf-8",
    )
    (src / "hooks").mkdir()
    (src / "hooks" / "evil.json").write_text("{}", encoding="utf-8")
    isolated = prepare_worker_home(src_home=src, dest_root=dest)
    assert isolated == dest
    assert (dest / "auth.json").is_file()
    assert not (dest / "hooks").exists()
    assert "[cli]" in (dest / "config.toml").read_text(encoding="utf-8")


def test_status_not_installed(monkeypatch, tmp_path: Path) -> None:
    service = GrokBuildAuthService(binary_path="grok-missing-binary", grok_home_dir=tmp_path)
    monkeypatch.setattr(service, "_resolve_binary", lambda: None)
    status = service.status()
    assert status.installed is False
    assert status.connected is False
    assert "not found" in status.message.lower() or "Install" in status.message


def test_status_connected_from_auth_file(monkeypatch, tmp_path: Path) -> None:
    (tmp_path / "auth.json").write_text(
        json.dumps({"tokens": {"access_token": "tok"}, "user": {"email": "ada@example.com"}}),
        encoding="utf-8",
    )
    service = GrokBuildAuthService(binary_path="grok", grok_home_dir=tmp_path)
    monkeypatch.setattr(service, "_resolve_binary", lambda: str(tmp_path / "grok"))
    monkeypatch.setattr(service, "_probe_version", lambda _binary: "grok 0.2.0")
    status = service.status()
    assert status.installed is True
    assert status.connected is True
    assert status.mode == "subscription"
    assert status.user_email == "ada@example.com"
