"""One key per provider family (2026-08-24).

A key entered on ANY surface (Realtime card, Agent row, Codex, Brain) is the
provider's key everywhere unless the user deliberately keeps a second one.
``plan_secret_save`` is the pure decision; these tests pin its rules with an
in-memory secret store, never the real keyring.
"""
from __future__ import annotations

import pytest

from jarvis.core import config as cfg


@pytest.fixture
def store(monkeypatch: pytest.MonkeyPatch) -> dict[str, str]:
    data: dict[str, str] = {}

    def _get(key: str, env_fallback: str | None = None) -> str | None:
        return data.get(key)

    monkeypatch.setattr(cfg, "get_secret", _get)
    return data


# ── classification is derived from the chains ───────────────────────────────


def test_primary_slots_are_family_primaries() -> None:
    scope = cfg.secret_slot_scope("gemini_api_key")
    assert scope == cfg.SecretSlotScope("gemini", "gemini_api_key", dedicated=False)


@pytest.mark.parametrize(
    ("slot", "family"),
    [
        ("realtime_gemini_api_key", "gemini"),
        ("realtime_openai_api_key", "openai"),
        ("realtime_vertex_api_key", "vertex"),
        ("jarvis_agent_gemini_api_key", "gemini"),
        ("jarvis_agent_anthropic_api_key", "claude-api"),
        ("codex_openai_api_key", "openai"),
    ],
)
def test_scoped_slots_point_at_their_family(slot: str, family: str) -> None:
    scope = cfg.secret_slot_scope(slot)
    assert scope is not None and scope.dedicated and scope.family == family
    assert scope.primary_slot == cfg.secret_family_primary_slot(family)


def test_legacy_aliases_and_foreign_slots_have_no_scope() -> None:
    assert cfg.secret_slot_scope("google_api_key") is None
    assert cfg.secret_slot_scope("elevenlabs_api_key") is None


def test_family_scoped_slots_enumerate_every_surface() -> None:
    assert set(cfg.secret_family_scoped_slots("gemini")) == {
        "realtime_gemini_api_key",
        "jarvis_agent_gemini_api_key",
    }
    assert set(cfg.secret_family_scoped_slots("openai")) == {
        "realtime_openai_api_key",
        "jarvis_agent_openai_api_key",
        "codex_openai_api_key",
    }


# ── the first key saved anywhere is the family key ──────────────────────────


def test_first_key_on_a_scoped_surface_lands_in_the_family_slot(store: dict[str, str]) -> None:
    plan = cfg.plan_secret_save("realtime_gemini_api_key", "AIza-new")
    assert plan.writes == ("gemini_api_key",)
    assert plan.deletes == ()
    assert not plan.choice_required


def test_same_key_on_a_scoped_surface_drops_the_redundant_copy(store: dict[str, str]) -> None:
    store["gemini_api_key"] = "AIza-one"
    store["realtime_gemini_api_key"] = "AIza-stale"
    plan = cfg.plan_secret_save("realtime_gemini_api_key", "AIza-one")
    assert plan.writes == ("gemini_api_key",)
    assert plan.deletes == ("realtime_gemini_api_key",)


def test_unscoped_slot_is_written_verbatim(store: dict[str, str]) -> None:
    plan = cfg.plan_secret_save("elevenlabs_api_key", "el-1")
    assert plan.writes == ("elevenlabs_api_key",)
    assert plan.family is None


# ── a different second key asks the question ────────────────────────────────


def test_different_key_on_scoped_surface_requires_a_choice(store: dict[str, str]) -> None:
    store["gemini_api_key"] = "AIza-one"
    plan = cfg.plan_secret_save("jarvis_agent_gemini_api_key", "AIza-two")
    assert plan.choice_required
    assert plan.writes == () and plan.deletes == ()
    assert plan.choice_kind == "dedicated_vs_family"
    assert plan.family == "gemini"


def test_scope_here_keeps_the_second_key_local(store: dict[str, str]) -> None:
    store["gemini_api_key"] = "AIza-one"
    plan = cfg.plan_secret_save("jarvis_agent_gemini_api_key", "AIza-two", "here")
    assert plan.writes == ("jarvis_agent_gemini_api_key",)
    assert plan.deletes == ()


def test_scope_everywhere_replaces_family_and_drops_old_mirrors(store: dict[str, str]) -> None:
    store["gemini_api_key"] = "AIza-one"
    store["realtime_gemini_api_key"] = "AIza-one"  # mirror of the old key
    store["jarvis_agent_gemini_api_key"] = "AIza-third"  # deliberate own key
    plan = cfg.plan_secret_save("realtime_gemini_api_key", "AIza-two", "everywhere")
    assert plan.writes == ("gemini_api_key",)
    # The realtime copy of the OLD key goes; the agent's third key is a choice
    # the user made and stays.
    assert plan.deletes == ("realtime_gemini_api_key",)


# ── replacing the family key ────────────────────────────────────────────────


def test_family_key_replace_with_no_dedicated_keys_is_plain(store: dict[str, str]) -> None:
    store["openai_api_key"] = "sk-old"
    plan = cfg.plan_secret_save("openai_api_key", "sk-new")
    assert plan.writes == ("openai_api_key",)
    assert plan.deletes == ()
    assert not plan.choice_required


def test_family_key_replace_drops_mirrors_of_the_old_key(store: dict[str, str]) -> None:
    store["openai_api_key"] = "sk-old"
    store["realtime_openai_api_key"] = "sk-old"
    plan = cfg.plan_secret_save("openai_api_key", "sk-new")
    assert plan.writes == ("openai_api_key",)
    assert plan.deletes == ("realtime_openai_api_key",)
    assert not plan.choice_required


def test_family_key_replace_asks_about_deliberate_dedicated_keys(store: dict[str, str]) -> None:
    store["openai_api_key"] = "sk-old"
    store["codex_openai_api_key"] = "sk-codex"
    plan = cfg.plan_secret_save("openai_api_key", "sk-new")
    assert plan.choice_required
    assert plan.choice_kind == "family_vs_dedicated"
    assert plan.conflicting_slots == ("codex_openai_api_key",)


def test_family_key_replace_scope_here_keeps_dedicated_keys(store: dict[str, str]) -> None:
    store["openai_api_key"] = "sk-old"
    store["codex_openai_api_key"] = "sk-codex"
    plan = cfg.plan_secret_save("openai_api_key", "sk-new", "here")
    assert plan.writes == ("openai_api_key",)
    assert plan.deletes == ()


def test_family_key_replace_scope_everywhere_drops_dedicated_keys(store: dict[str, str]) -> None:
    store["openai_api_key"] = "sk-old"
    store["codex_openai_api_key"] = "sk-codex"
    plan = cfg.plan_secret_save("openai_api_key", "sk-new", "everywhere")
    assert plan.writes == ("openai_api_key",)
    assert plan.deletes == ("codex_openai_api_key",)


def test_unknown_scope_is_rejected(store: dict[str, str]) -> None:
    with pytest.raises(ValueError):
        cfg.plan_secret_save("openai_api_key", "sk-new", "somewhere")
