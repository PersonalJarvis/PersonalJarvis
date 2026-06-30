"""B2 (open-source AP-22): the mission Critic must be able to grade in-process via
any keyed API brain provider, so a mission's review no longer requires the absent
`claude` CLI binary. Covers the provider-agnostic critic resolver + the text→verdict
parser used by the in-process path.
"""
from __future__ import annotations

import jarvis.core.config as cfg_mod
from jarvis.missions.critic.runner import (
    REQUIRED_AXES,
    CriticAxis,
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
        summary_de="Sieht gut aus.",
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
