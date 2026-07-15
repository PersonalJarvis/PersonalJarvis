"""B2 (open-source AP-22): the mission Critic must be able to grade in-process via
any keyed API brain provider, so a mission's review no longer requires the absent
`claude` CLI binary. Covers the provider-agnostic critic resolver + the text→verdict
parser used by the in-process path.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import jarvis.core.config as cfg_mod
from jarvis.core.protocols import BrainDelta
from jarvis.missions.critic.runner import (
    REQUIRED_AXES,
    CriticAxis,
    CriticRunner,
    CriticVerdict,
    _parse_verdict_from_text,
    _resolve_api_critic_provider,
)


def _valid_verdict_json() -> str:
    v = CriticVerdict(
        verdict="approve",
        axes={ax: CriticAxis(status="pass", evidence=["ok"]) for ax in REQUIRED_AXES},
        issues=[],
        correction_instruction="",
        summary="Looks good.",
        summary_de="Looks good.",
        confidence=0.9,
        suggested_next_action="accept",
    )
    return v.model_dump_json()


def test_parse_verdict_from_clean_json():
    out = _parse_verdict_from_text(_valid_verdict_json(), iteration=0, adversarial_reframe=False)
    assert out is not None and out.verdict == "approve"


def test_parse_verdict_from_fenced_json():
    fenced = "```json\n" + _valid_verdict_json() + "\n```"
    out = _parse_verdict_from_text(fenced, iteration=0, adversarial_reframe=False)
    assert out is not None and out.verdict == "approve"


def test_parse_verdict_recovers_from_surrounding_prose():
    noisy = "Let me think...\nIssuing the JSON verdict:\n" + _valid_verdict_json() + "\nDone."
    out = _parse_verdict_from_text(noisy, iteration=1, adversarial_reframe=True)
    assert out is not None and out.verdict == "approve"


def test_parse_verdict_returns_none_on_garbage():
    assert _parse_verdict_from_text("no json here at all", iteration=0, adversarial_reframe=False) is None


def test_api_critic_provider_prefers_keyed_primary(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: "k" if p == "openrouter" else None)
    prov, model = _resolve_api_critic_provider("openrouter", "anthropic/claude-opus-4.8")
    assert prov == "openrouter" and model == "anthropic/claude-opus-4.8"


def test_api_critic_falls_back_to_any_keyed_api_provider(monkeypatch):
    # Worker provider is antigravity (no API critic backend); the critic must grade
    # via whatever API key the user actually has (here: gemini).
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: "k" if p == "gemini" else None)
    prov, model = _resolve_api_critic_provider("antigravity", "gemini-3.1-pro-preview")
    assert prov == "gemini"


def test_api_critic_none_when_no_api_key(monkeypatch):
    monkeypatch.setattr(cfg_mod, "get_provider_secret", lambda p: None)
    prov, model = _resolve_api_critic_provider("antigravity", None)
    assert prov is None


def test_api_critic_resolver_can_exclude_a_failed_family(monkeypatch):
    monkeypatch.setattr(
        "jarvis.missions.init._api_key_family_viable",
        lambda provider: provider in {"openrouter", "gemini"},
    )

    provider, _model = _resolve_api_critic_provider(
        "antigravity",
        None,
        excluded_providers={"openrouter"},
    )

    assert provider == "gemini"


@pytest.mark.asyncio
async def test_api_critic_uses_scoped_jarvis_agent_key(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    observed: list[str | None] = []

    class _Brain:
        def __init__(self, model=None):  # noqa: ANN001
            pass

        async def complete(self, req):  # noqa: ANN001, ANN201
            observed.append(cfg_mod.get_provider_secret("openai"))
            yield BrainDelta(content=_valid_verdict_json())

    monkeypatch.setattr(
        cfg_mod,
        "get_secret",
        lambda key, *args, **kwargs: {
            "jarvis_agent_openai_api_key": "agent-key",
            "openai_api_key": "brain-key",
        }.get(key),
    )
    monkeypatch.setattr(
        "jarvis.brain.provider_registry.BrainProviderRegistry.get_class",
        lambda self, provider: _Brain,
    )

    verdict = await CriticRunner()._invoke_via_api_critic(
        prompt="Review it.",
        model="gpt-test",
        provider="openai",
        iteration=0,
        adversarial_reframe=False,
    )

    assert verdict is not None and verdict.verdict == "approve"
    assert observed == ["agent-key"]
    assert cfg_mod.get_provider_secret("openai") == "brain-key"


@pytest.mark.asyncio
async def test_critic_walks_to_next_api_family_after_invalid_verdict(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    monkeypatch.setattr(
        "jarvis.missions.critic.runner._resolve_critic_provider_model",
        lambda: ("antigravity", None),
    )
    monkeypatch.setattr(
        "jarvis.missions.critic.runner._claude_cli_critic_viable",
        lambda: False,
    )
    monkeypatch.setattr(
        "jarvis.missions.init._api_key_family_viable",
        lambda provider: provider in {"openrouter", "gemini"},
    )
    attempted: list[str] = []

    async def _fake_api_critic(self, **kwargs):  # noqa: ANN001, ANN202
        attempted.append(kwargs["provider"])
        if kwargs["provider"] == "openrouter":
            return None
        return CriticVerdict(
            verdict="approve",
            axes={
                axis: CriticAxis(status="pass", evidence=["verified"])
                for axis in REQUIRED_AXES
            },
            issues=[],
            correction_instruction="",
            summary="The mission output is verified.",
            summary_de="The mission output is verified.",
            confidence=0.9,
            suggested_next_action="accept",
        )

    monkeypatch.setattr(CriticRunner, "_invoke_via_api_critic", _fake_api_critic)

    verdict = await CriticRunner().run(
        mission_prompt="Implement the requested feature.",
        worker_diff="diff --git a/a.py b/a.py\n+print('done')",
        worker_log="worker completed",
        prior_reflections="",
        iteration=0,
        worktree=tmp_path,
        env={},
        _capability_check=False,
    )

    assert verdict.verdict == "approve"
    assert attempted == ["openrouter", "gemini"]
