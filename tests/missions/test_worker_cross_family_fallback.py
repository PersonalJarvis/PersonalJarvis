"""Open-source AP-22/AP-23: the heavy-mission Worker LAST resort must be
key-aware and cross-family — never a dead-end on the Claude Max ``claude`` CLI.

Forensic shape of the bug (provider-agnostic feature parity audit, pre-public
release): a downloader whose ONLY credential is a Gemini / OpenRouter / OpenAI
key — and who never switched ``[brain.worker].provider`` off the ``claude-api``
default — could chat fine (the Brain has a cross-family fallback chain) but the
first heavy mission bricked: the worker factory fell through to
``ClaudeDirectWorker``, which needs either the ``claude`` CLI binary (absent on
a fresh install) or an Anthropic key (absent). The fix mirrors the Brain's
cross-family chain in ``_cross_family_last_resort_worker``.
"""
from __future__ import annotations

import pytest

from jarvis.missions import init as mi
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker
from jarvis.missions.workers.codex_direct_worker import CodexDirectWorker


def _patch_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    claude_binary: str | None = None,
    codex_oauth: bool = False,
    codex_reauth: bool = False,
    keys: tuple[str, ...] = (),
) -> None:
    """Pin every probe the last-resort helper consults to a known world."""
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
        lambda: claude_binary,
    )
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available",
        lambda: codex_oauth,
    )
    monkeypatch.setattr(
        "jarvis.codex_auth_state.codex_needs_reauth", lambda: codex_reauth
    )
    keyset = {k.strip().lower() for k in keys}
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret",
        lambda p: "KEY" if (p or "").strip().lower() in keyset else None,
    )
    # The claude-binary branch assembles MCP servers; keep that cheap + offline.
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: ())


# --- The brick scenario: a single non-Claude key must run, not dead-end -------


@pytest.mark.parametrize(
    "key,provider",
    [("gemini", "gemini"), ("openrouter", "openrouter"), ("openai", "openai")],
)
def test_single_nonclaude_key_runs_on_that_family(
    monkeypatch: pytest.MonkeyPatch, key: str, provider: str
) -> None:
    """No claude binary, no codex login, ONE non-Claude API key → the in-process
    ApiAgentWorker on THAT family — never None, never a Claude dead-end."""
    _patch_env(monkeypatch, keys=(key,))
    worker = mi._cross_family_last_resort_worker("do the task")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == provider


def test_claude_api_key_only_runs_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    """An Anthropic-API-key-only user with no `claude` binary runs in-process."""
    _patch_env(monkeypatch, keys=("claude-api",))
    worker = mi._cross_family_last_resort_worker("t")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "claude-api"


# --- Subscription-first ordering (no metered key before a metered key) --------


def test_claude_cli_binary_wins_subscription_first(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When the Claude Max OAuth `claude` binary IS present, it is the floor —
    the maintainer path is unchanged (no regression to a metered API key)."""
    _patch_env(monkeypatch, claude_binary="/usr/bin/claude", keys=("gemini",))
    worker = mi._cross_family_last_resort_worker("t")
    assert isinstance(worker, ClaudeDirectWorker)


def test_codex_oauth_beats_api_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live ChatGPT (codex) login is a subscription floor before metered keys."""
    _patch_env(monkeypatch, codex_oauth=True, keys=("openai",))
    worker = mi._cross_family_last_resort_worker("t")
    assert isinstance(worker, CodexDirectWorker)


def test_codex_needing_reauth_is_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dead codex login is skipped — it must not shadow a usable API key."""
    _patch_env(monkeypatch, codex_oauth=True, codex_reauth=True, keys=("gemini",))
    worker = mi._cross_family_last_resort_worker("t")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "gemini"


# --- The genuine no-credential case still degrades honestly -------------------


def test_nothing_reachable_returns_none(monkeypatch: pytest.MonkeyPatch) -> None:
    """No binary, no codex, no key → None, so the caller keeps the honest Claude
    last resort (which fails legibly rather than silently)."""
    _patch_env(monkeypatch)
    assert mi._cross_family_last_resort_worker("t") is None
