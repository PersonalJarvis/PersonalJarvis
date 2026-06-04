"""Tests for ``_build_context_ack`` — the spoken spawn acknowledgement.

Live regression 2026-05-26: Alex heard the same long template phrase every
time a sub-agent was force-spawned:

    "Mach ich, ich kümmere mich im Hintergrund darum, den vom User
     beschriebenen Workflow."

Two root causes converged on the same generic sentence:

* The force-spawn path (``BrainManager._force_spawn_worker``) hard-coded
  ``action="den vom User beschriebenen Workflow"`` because there is no
  LLM-tool-choice loop to formulate a contextual action verb.
* ``_build_context_ack`` then spliced that string into a fixed template, so
  the user heard a 17-syllable canned phrase on every force-spawn.

Fix:
* Force-spawn now passes ``action=""``.
* The empty-action branch picks from a small rotation of short, varied
  acknowledgements — never the long template phrase.
* Contextual phrasing (when the LLM does emit a real action verb) is
  unchanged.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.tool.spawn_worker import (
    _GENERIC_ACK_VARIANTS,
    _build_context_ack,
)


# --------------------------------------------------------------------------- #
# Empty action — short varied ACK, NEVER the long template phrase.            #
# --------------------------------------------------------------------------- #


def test_empty_action_returns_one_of_the_variants() -> None:
    """No action supplied (force-spawn) → must be one of the short variants."""
    result = _build_context_ack("", "")
    assert result in _GENERIC_ACK_VARIANTS, (
        f"empty-action ACK must come from the variant set, got {result!r}"
    )


def test_empty_action_with_target_still_returns_variant() -> None:
    """Force-spawn passes ``target=''`` too; even with an accidental target,
    a missing action must defeat the long template."""
    result = _build_context_ack("", "irgendwo")
    assert result in _GENERIC_ACK_VARIANTS, (
        f"empty-action must override any target; got {result!r}"
    )


def test_no_variant_contains_the_old_workflow_phrase() -> None:
    """Regression guard: the old standard phrase must NOT reappear in any
    variant. Alex specifically complained about that wording."""
    forbidden = "vom User beschriebenen Workflow"
    for variant in _GENERIC_ACK_VARIANTS:
        assert forbidden not in variant, (
            f"variant must not contain the forbidden phrase {forbidden!r}: {variant!r}"
        )


def test_variants_are_short_and_unique() -> None:
    """TTS-friendliness + no rotation through near-duplicates."""
    assert len(_GENERIC_ACK_VARIANTS) >= 4, (
        "need enough variants that the user does not hear the same one back-to-back"
    )
    assert len(set(_GENERIC_ACK_VARIANTS)) == len(_GENERIC_ACK_VARIANTS), (
        "duplicate variants defeat the point of rotation"
    )
    for v in _GENERIC_ACK_VARIANTS:
        assert 4 <= len(v) <= 60, (
            f"variant must be short and TTS-readable, got {len(v)} chars: {v!r}"
        )


def test_variants_eventually_rotate(monkeypatch: pytest.MonkeyPatch) -> None:
    """Across many calls with empty action we must see more than one variant.

    Verifies that the function actually USES the rotation rather than always
    returning the first element.
    """
    seen: set[str] = set()
    # Monkeypatch random.choice to walk the list deterministically so the test
    # cannot flake. We'd see 4+ distinct variants over 6 calls if rotation works.
    counter = {"i": 0}

    def _walk(seq):  # type: ignore[no-untyped-def]
        out = seq[counter["i"] % len(seq)]
        counter["i"] += 1
        return out

    monkeypatch.setattr(
        "jarvis.plugins.tool.spawn_worker.random.choice", _walk
    )
    for _ in range(len(_GENERIC_ACK_VARIANTS)):
        seen.add(_build_context_ack("", ""))
    assert len(seen) == len(_GENERIC_ACK_VARIANTS), (
        f"rotation must cover all variants, saw only {seen!r}"
    )


# --------------------------------------------------------------------------- #
# Non-empty action — contextual phrasing path is unchanged.                   #
# --------------------------------------------------------------------------- #


def test_real_action_keeps_contextual_phrase() -> None:
    """When the LLM emits a real action verb the contextual template still
    fires — that branch is the design intent for genuine tool-call spawns."""
    result = _build_context_ack("eine Datei erstellt", "")
    assert "Mach ich" in result
    assert "im Hintergrund" in result
    # 3rd-person verb folds to infinitive: "erstellt" → "erstellen"
    assert "erstellen" in result
    assert result not in _GENERIC_ACK_VARIANTS, (
        "a contextual action MUST NOT collapse into a generic variant"
    )


def test_real_action_with_target_includes_target() -> None:
    """Target appears verbatim in the contextual ACK."""
    result = _build_context_ack("eine Datei erstellt", "test.md")
    assert "test.md" in result
    assert "erstellen" in result


def test_contextual_path_never_uses_the_workflow_phrase() -> None:
    """The contextual branch (real action) must not regress to the old wording either."""
    result = _build_context_ack("eine HTML-Seite baut", "")
    assert "vom User beschriebenen Workflow" not in result
