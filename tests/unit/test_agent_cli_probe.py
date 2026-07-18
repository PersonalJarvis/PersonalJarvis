"""Tests for jarvis.agent_cli_probe — the live "Test" button probe.

Contract under guard: the probe never raises, reports an honest not-installed
result WITH the searched PATH (so a GUI-PATH miss is diagnosable), busts the
version caches so a test is a real spawn, and names which Google CLI resolved
(agy vs Gemini) on the Antigravity card.
"""
from __future__ import annotations

import pytest

from jarvis import agent_cli_probe, claude_auth, codex_auth
from jarvis.claude_auth import ClaudeAuthStatus
from jarvis.codex_auth import CodexAuthStatus
from jarvis.google_cli.resolver import GoogleCli


@pytest.fixture(autouse=True)
def _quiet_path_augment(monkeypatch):
    """Keep the probe from mutating the test process PATH."""
    monkeypatch.setattr(agent_cli_probe, "ensure_cli_paths", lambda: [])


def test_claude_not_installed_reports_searched_path(monkeypatch):
    monkeypatch.setattr(
        claude_auth.ClaudeAuthService,
        "status",
        lambda self: ClaudeAuthStatus(installed=False),
    )
    result = agent_cli_probe.test_claude()
    assert result.ok is False
    assert result.installed is False
    assert result.searched_path, "a miss must list the searched PATH dirs"
    assert "npm i -g @anthropic-ai/claude-code" in result.message


def test_claude_installed_and_answering_is_ok(monkeypatch):
    monkeypatch.setattr(
        claude_auth.ClaudeAuthService,
        "status",
        lambda self: ClaudeAuthStatus(
            installed=True,
            connected=True,
            mode="subscription",
            message="Connected via Claude Max.",
            version="2.1.0",
            user_email="user@example.com",
            binary_path="/usr/local/bin/claude",
        ),
    )
    result = agent_cli_probe.test_claude()
    assert result.ok is True
    assert result.binary_path == "/usr/local/bin/claude"
    assert result.version == "2.1.0"
    assert result.connected is True
    assert result.account == "user@example.com"


def test_claude_present_but_mute_binary_is_not_ok(monkeypatch):
    """Binary resolves but --version never answered → honest broken-install."""
    monkeypatch.setattr(
        claude_auth.ClaudeAuthService,
        "status",
        lambda self: ClaudeAuthStatus(
            installed=True, version=None, binary_path="/x/claude"
        ),
    )
    result = agent_cli_probe.test_claude()
    assert result.ok is False
    assert result.installed is True
    assert "did not answer" in result.message


def test_claude_test_busts_the_version_cache(monkeypatch):
    calls: list[str] = []
    monkeypatch.setattr(
        claude_auth, "clear_version_cache", lambda: calls.append("cleared")
    )
    monkeypatch.setattr(
        claude_auth.ClaudeAuthService,
        "status",
        lambda self: ClaudeAuthStatus(installed=False),
    )
    agent_cli_probe.test_claude()
    assert calls == ["cleared"]


def test_codex_honors_configured_binary_path(monkeypatch):
    seen: dict[str, str | None] = {}

    real_init = codex_auth.CodexAuthService.__init__

    def spy_init(self, binary_path=None):
        seen["binary_path"] = binary_path
        real_init(self, binary_path)

    monkeypatch.setattr(codex_auth.CodexAuthService, "__init__", spy_init)
    monkeypatch.setattr(
        codex_auth.CodexAuthService,
        "status",
        lambda self: CodexAuthStatus(installed=False),
    )
    result = agent_cli_probe.test_codex("/custom/codex")
    assert seen["binary_path"] == "/custom/codex"
    assert result.ok is False
    assert "npm i -g @openai/codex" in result.message


def test_antigravity_names_the_resolved_cli_kind(monkeypatch):
    from jarvis.google_cli import auth_service as gsvc

    monkeypatch.setattr(
        "jarvis.google_cli.resolver.resolve_google_cli",
        lambda: GoogleCli(kind="gemini", argv_prefix=["/usr/local/bin/gemini"]),
    )
    monkeypatch.setattr(agent_cli_probe, "_live_version", lambda argv: "0.9.0")
    monkeypatch.setattr(
        gsvc.GoogleCliAuthService,
        "status",
        lambda self: gsvc.GoogleCliAuthStatus(
            installed=True,
            connected=False,
            mode="unknown",
            cli_kind="gemini",
            message="Gemini CLI installed but not logged in — run the Google login.",
        ),
    )
    result = agent_cli_probe.test_antigravity()
    assert result.ok is True
    assert result.cli_kind == "gemini"
    assert "Gemini CLI" in result.message


def test_antigravity_not_installed(monkeypatch):
    monkeypatch.setattr(
        "jarvis.google_cli.resolver.resolve_google_cli", lambda: None
    )
    result = agent_cli_probe.test_antigravity()
    assert result.ok is False
    assert result.installed is False
    assert result.searched_path


def test_results_serialize_without_secrets(monkeypatch):
    monkeypatch.setattr(
        claude_auth.ClaudeAuthService,
        "status",
        lambda self: ClaudeAuthStatus(installed=False),
    )
    payload = agent_cli_probe.test_claude().to_dict()
    assert set(payload) == {
        "cli",
        "ok",
        "installed",
        "binary_path",
        "version",
        "connected",
        "auth_mode",
        "account",
        "message",
        "searched_path",
        "duration_ms",
        "cli_kind",
    }
