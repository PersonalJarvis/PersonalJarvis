"""reachable_worker_families() — the cheap, offline family probe that feeds
the Sub-Agents section health (which families could run a mission RIGHT NOW).
Same probe seams as tests/missions/test_worker_cross_family_fallback.py."""
from __future__ import annotations

import pytest

from jarvis.missions import init as mi


def _patch_env(
    monkeypatch: pytest.MonkeyPatch,
    *,
    claude_binary: str | None = None,
    claude_auth_viable: bool = True,
    codex_oauth: bool = False,
    codex_reauth: bool = False,
    codex_quota_capped: bool = False,
    keys: tuple[str, ...] = (),
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
        lambda: claude_binary,
    )
    monkeypatch.setattr(mi, "_claude_cli_auth_viable", lambda: claude_auth_viable)
    monkeypatch.setattr(
        "jarvis.missions.workers.codex_direct_worker._codex_oauth_available",
        lambda: codex_oauth,
    )
    monkeypatch.setattr(
        "jarvis.codex_auth_state.codex_needs_reauth", lambda: codex_reauth
    )
    monkeypatch.setattr(
        "jarvis.codex_quota_state.codex_in_quota_cooldown",
        lambda **_k: codex_quota_capped,
    )
    keyset = {k.strip().lower() for k in keys}
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret",
        lambda p: "KEY" if (p or "").strip().lower() in keyset else None,
    )


def test_all_dead_returns_empty(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_env(monkeypatch)
    assert mi.reachable_worker_families() == []


def test_subscription_first_ordering(monkeypatch: pytest.MonkeyPatch) -> None:
    _patch_env(
        monkeypatch,
        claude_binary="/usr/bin/claude", claude_auth_viable=True,
        codex_oauth=True, keys=("openrouter",),
    )
    fams = mi.reachable_worker_families()
    assert fams[0] == "claude"
    assert "codex" in fams and "openrouter" in fams


def test_dead_claude_is_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-07-06 shape: binary present, auth dead -> claude absent."""
    _patch_env(
        monkeypatch,
        claude_binary="/usr/bin/claude", claude_auth_viable=False,
        codex_oauth=True,
    )
    fams = mi.reachable_worker_families()
    assert "claude" not in fams
    assert fams == ["codex"]


def test_usage_capped_codex_is_not_listed(monkeypatch: pytest.MonkeyPatch) -> None:
    """The 2026-07-07 shape: codex login connected but the ChatGPT plan is
    usage-capped -> codex absent, the healthy API-key family remains, so the
    Sub-Agents section health warns honestly instead of staying green."""
    _patch_env(
        monkeypatch,
        codex_oauth=True, codex_quota_capped=True,
        keys=("openrouter",),
    )
    fams = mi.reachable_worker_families()
    assert "codex" not in fams
    assert fams == ["openrouter"]


def test_stale_oauth_bearer_claude_api_is_not_listed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stored `sk-ant-oat` bearer is a stale OAuth copy, not an API key —
    listing claude-api as reachable on its existence alone kept the section
    green while every ApiAgentWorker('claude-api') spawn 401'd (2026-07-07,
    mission 019f3d01)."""
    _patch_env(monkeypatch)
    monkeypatch.setattr(
        "jarvis.core.config.get_provider_secret",
        lambda p: {
            "claude-api": "sk-ant-oat01-STALE-COPY",
            "openrouter": "KEY",
        }.get((p or "").strip().lower()),
    )
    fams = mi.reachable_worker_families()
    assert "claude-api" not in fams
    assert fams == ["openrouter"]
