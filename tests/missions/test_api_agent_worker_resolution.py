"""Regression: two mission worker-factory branches must be VIABILITY-gated
(not existence-gated) and cross provider families instead of re-picking a
dead/depleted provider every critic round or handing back a guaranteed-fail
worker.

1. ``kind == "api_agent"`` (openai/openrouter/nvidia run on their own API key)
   previously used a bare ``bool(getattr(ep, "credential", None))`` existence
   check — a family a worker already proved quota-depleted / auth-dead this
   session was re-picked EVERY critic round instead of crossing to a healthy
   one (BUG-042 twin, AP-22). Fixed by routing through the same
   ``_api_key_family_viable`` helper the sibling branches already use.
2. ``kind == "codex_direct"`` with a dead ChatGPT login previously returned
   ``ClaudeDirectWorker`` unconditionally, without checking whether Claude
   itself is actually reachable (CLI binary + auth, quota cooldown, or an
   API key) — mirrors the claude_direct branch's own gates and crosses to
   another provider family when Claude is not reachable either (AP-22/AP-23).

Both branches were extracted into standalone, directly-testable helpers
(``_resolve_api_agent_worker`` / ``_resolve_codex_dead_login_worker``) so the
worker-factory closure inside ``bootstrap_missions`` stays a thin dispatcher —
same pattern as ``_cross_family_last_resort_worker``.
"""
from __future__ import annotations

import pytest

from jarvis.missions import init as mi
from jarvis.missions.workers.api_agent_worker import ApiAgentWorker
from jarvis.missions.workers.claude_direct_worker import ClaudeDirectWorker

# --- _resolve_api_agent_worker ------------------------------------------------


def test_viable_provider_runs_in_process(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda p: p == "openrouter")
    worker = mi._resolve_api_agent_worker("openrouter", "do the task")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "openrouter"


def test_unviable_provider_crosses_family_instead_of_being_reused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """BUG-042 twin: a provider a worker already proved quota-depleted /
    auth-dead this session must NOT be re-picked just because a key exists —
    `_api_key_family_viable` reports False, so the branch crosses family."""
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda p: False)
    monkeypatch.setattr(
        mi, "_cross_family_last_resort_worker",
        lambda task_text: ApiAgentWorker("gemini"),
    )
    worker = mi._resolve_api_agent_worker("openrouter", "do the task")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "gemini"


def test_no_family_reachable_falls_back_to_claude_honest_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(mi, "_api_key_family_viable", lambda p: False)
    monkeypatch.setattr(mi, "_cross_family_last_resort_worker", lambda task_text: None)
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: {})
    worker = mi._resolve_api_agent_worker("openrouter", "do the task")
    assert isinstance(worker, ClaudeDirectWorker)


def test_viability_check_failure_is_treated_as_no_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def _boom(_p: str) -> bool:
        raise RuntimeError("keyring unavailable")

    monkeypatch.setattr(mi, "_api_key_family_viable", _boom)
    monkeypatch.setattr(
        mi, "_cross_family_last_resort_worker",
        lambda task_text: ApiAgentWorker("gemini"),
    )
    worker = mi._resolve_api_agent_worker("openrouter", "do the task")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "gemini"


# --- _resolve_codex_dead_login_worker ----------------------------------------


def _patch_codex_dead_login(
    monkeypatch: pytest.MonkeyPatch,
    *,
    claude_binary: str | None,
    claude_auth_viable: bool = True,
    claude_quota_capped: bool = False,
    claude_api_key_viable: bool = False,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.workers.claude_direct_worker._resolve_claude_binary",
        lambda: claude_binary,
    )
    monkeypatch.setattr(mi, "_claude_cli_auth_viable", lambda: claude_auth_viable)
    monkeypatch.setattr(
        "jarvis.claude_quota_state.claude_in_quota_cooldown",
        lambda: claude_quota_capped,
    )
    monkeypatch.setattr(
        mi, "_api_key_family_viable",
        lambda p: claude_api_key_viable if p == "claude-api" else False,
    )
    monkeypatch.setattr(mi, "_assemble_worker_mcp_servers", lambda **_k: {})


def test_dead_codex_login_no_binary_uses_claude_api_key_in_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_codex_dead_login(monkeypatch, claude_binary=None, claude_api_key_viable=True)
    worker = mi._resolve_codex_dead_login_worker("do the task")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "claude-api"


def test_dead_codex_login_viable_claude_cli_runs_claude_direct(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_codex_dead_login(
        monkeypatch, claude_binary="/usr/bin/claude", claude_auth_viable=True,
    )
    worker = mi._resolve_codex_dead_login_worker("do the task")
    assert isinstance(worker, ClaudeDirectWorker)


def test_dead_codex_login_claude_quota_cooldown_crosses_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both subscription lanes are dead this session (codex needs reauth,
    Claude in its OWN quota cooldown) — must cross to another provider
    family instead of handing back a doomed ClaudeDirectWorker."""
    _patch_codex_dead_login(
        monkeypatch,
        claude_binary="/usr/bin/claude",
        claude_auth_viable=True,
        claude_quota_capped=True,
    )
    monkeypatch.setattr(
        mi, "_cross_family_last_resort_worker",
        lambda task_text: ApiAgentWorker("openrouter"),
    )
    worker = mi._resolve_codex_dead_login_worker("do the task")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "openrouter"


def test_dead_codex_login_and_dead_claude_auth_crosses_family(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_codex_dead_login(
        monkeypatch, claude_binary="/usr/bin/claude", claude_auth_viable=False,
    )
    monkeypatch.setattr(
        mi, "_cross_family_last_resort_worker",
        lambda task_text: ApiAgentWorker("gemini"),
    )
    worker = mi._resolve_codex_dead_login_worker("do the task")
    assert isinstance(worker, ApiAgentWorker)
    assert worker.provider == "gemini"


def test_dead_codex_login_nothing_reachable_falls_back_to_claude_honest_fail(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _patch_codex_dead_login(
        monkeypatch, claude_binary="/usr/bin/claude", claude_auth_viable=False,
    )
    monkeypatch.setattr(mi, "_cross_family_last_resort_worker", lambda task_text: None)
    worker = mi._resolve_codex_dead_login_worker("do the task")
    assert isinstance(worker, ClaudeDirectWorker)
