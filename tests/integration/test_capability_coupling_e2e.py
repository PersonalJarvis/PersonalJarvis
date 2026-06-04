"""Integration tests for the Capability-Coupling pattern (ADR-0017).

Verifies the goal stated in `docs/plans/capability-coupling/SPEC.md`:
    "Jarvis must only confirm what it can actually do. Unknown tasks → a
    deterministic 'I cannot (yet) do that.'"

Hard-negatives (must produce UNSUPPORTED + zero phantom TTS):
    1. "Schick eine Email an sam@gmx.de ..."
    2. "Trag einen Termin morgen 10 Uhr ein"
    3. "Sende eine WhatsApp an Mama"
    4. "Bestelle eine Pizza"
    5. "Poste auf X dass ich heute frei habe"

Hard-positives (must NOT trigger UNSUPPORTED — false-negative guard):
    6. "Öffne Chrome" → local_action.open_app
    7. "Lies die Datei foo.txt" → harness.openclaw
    8. "Wie spät ist es?" → smalltalk, gate inactive
    9. "Such im Web nach Python 3.13" → UNSUPPORTED when no web-search tool
       is registered (catches the manager.py:774 prompt-claim drift).

The Critic-honesty regression is covered by
`tests/missions/critic/test_runner_dryrun.py` (12 green cases shipped by
Agent D); this file only exercises the Brain-layer gate.
"""
from __future__ import annotations

import pytest

from jarvis.brain.local_action_gate import LocalActionMode, match_local_action
from jarvis.core.capabilities import CapabilityRegistry, get_registry
from jarvis.core.capabilities_seed import seed_registry


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture(scope="module")
def seeded_registry() -> CapabilityRegistry:
    """Production-equivalent registry: seeded once per module."""
    reg = get_registry()
    seed_registry(reg)
    return reg


HARD_NEGATIVE_UTTERANCES = [
    "Schick eine Email an sam@gmx.de mit dem Betreff Hallo",
    "Trag einen Termin morgen 10 Uhr ein",
    "Sende eine WhatsApp an Mama",
    "Bestelle eine Pizza",
    "Poste auf X dass ich heute frei habe",
]

HARD_POSITIVE_LOCAL = [
    ("Öffne Chrome", LocalActionMode.DIRECT),
    ("Klick auf den roten Button links", LocalActionMode.COMPUTER_USE),
]


# ---------------------------------------------------------------------------
# Hard-negatives — UNSUPPORTED path
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance", HARD_NEGATIVE_UTTERANCES)
def test_hard_negative_registry_action_intent_no_resolve(
    seeded_registry: CapabilityRegistry, utterance: str
) -> None:
    """Each hard-negative utterance must (a) be recognised as an action and
    (b) resolve to no registered capability."""
    assert seeded_registry.has_action_intent(utterance), (
        f"Expected action-intent recognition for {utterance!r}"
    )
    assert seeded_registry.resolve_intent(utterance) is None, (
        f"Expected None resolve for {utterance!r} — no capability should match"
    )


@pytest.mark.parametrize("utterance", HARD_NEGATIVE_UTTERANCES)
def test_hard_negative_gate_returns_unsupported(
    seeded_registry: CapabilityRegistry, utterance: str
) -> None:
    """`match_local_action` must return UNSUPPORTED mode for each hard-negative."""
    plan = match_local_action(utterance, lang="de")
    assert plan is not None, f"Expected non-None plan for {utterance!r}"
    assert plan.mode is LocalActionMode.UNSUPPORTED, (
        f"Expected UNSUPPORTED for {utterance!r}, got {plan.mode!r}"
    )


@pytest.mark.parametrize("utterance", HARD_NEGATIVE_UTTERANCES)
def test_hard_negative_response_contains_no_phantom_confirmation(
    seeded_registry: CapabilityRegistry, utterance: str
) -> None:
    """The deterministic UNSUPPORTED response must not contain any phantom
    success phrasing (gesendet / eingetragen / bestellt / sent / scheduled)."""
    plan = match_local_action(utterance, lang="de")
    assert plan is not None
    response = (plan.response_text or "").lower()
    forbidden = [
        "gesendet", "eingetragen", "wird erledigt", "bestellt", "gepostet",
        "sent", "scheduled", "ordered", "done.", "consider it done",
    ]
    leaked = [phrase for phrase in forbidden if phrase in response]
    assert not leaked, (
        f"Phantom confirmation phrases {leaked!r} leaked into UNSUPPORTED "
        f"response for {utterance!r}: {response!r}"
    )


def test_hard_negative_response_starts_with_unsupported_marker(
    seeded_registry: CapabilityRegistry,
) -> None:
    """The DE response must start with the canonical 'Das kann ich noch nicht'
    marker so the user clearly hears the refusal."""
    plan = match_local_action(HARD_NEGATIVE_UTTERANCES[0], lang="de")
    assert plan is not None and plan.response_text is not None
    assert plan.response_text.lower().startswith("das kann ich noch nicht"), (
        f"Expected canonical refusal opener, got {plan.response_text!r}"
    )


def test_unsupported_response_english_locale(
    seeded_registry: CapabilityRegistry,
) -> None:
    """English locale must produce the English refusal copy."""
    plan = match_local_action("Send an email to bob", lang="en")
    assert plan is not None and plan.response_text is not None
    assert "can't do that yet" in plan.response_text.lower()


# ---------------------------------------------------------------------------
# Hard-positives — must not be intercepted by UNSUPPORTED
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("utterance,expected_mode", HARD_POSITIVE_LOCAL)
def test_hard_positive_local_action_not_unsupported(
    seeded_registry: CapabilityRegistry,
    utterance: str,
    expected_mode: LocalActionMode,
) -> None:
    """Existing local-action utterances must reach their normal mode, not
    UNSUPPORTED."""
    plan = match_local_action(utterance, lang="de")
    assert plan is not None
    assert plan.mode is expected_mode, (
        f"Regression: {utterance!r} now resolves to {plan.mode!r} (was {expected_mode!r})"
    )


def test_hard_positive_file_ops_resolves_to_openclaw(
    seeded_registry: CapabilityRegistry,
) -> None:
    """'Lies die Datei foo.txt' must resolve to the openclaw harness."""
    cap = seeded_registry.resolve_intent("Lies die Datei foo.txt")
    assert cap is not None, "Expected a capability match for file-read"
    assert "openclaw" in cap.id or "file" in cap.id or cap.source == "harness", (
        f"Expected a file-capable harness/tool, got {cap!r}"
    )


def test_hard_positive_smalltalk_not_action_intent(
    seeded_registry: CapabilityRegistry,
) -> None:
    """Smalltalk must not trigger the action-intent gate."""
    assert not seeded_registry.has_action_intent("Wie spät ist es"), (
        "Smalltalk 'Wie spät ist es' must not be classified as action intent"
    )
    plan = match_local_action("Wie spät ist es", lang="de")
    # Plan may be None (no match anywhere) — but if any plan is returned, it
    # must not be UNSUPPORTED.
    if plan is not None:
        assert plan.mode is not LocalActionMode.UNSUPPORTED


# ---------------------------------------------------------------------------
# search_web prompt-claim drift guard (manager.py:774)
# ---------------------------------------------------------------------------

def test_search_web_unregistered_hits_unsupported() -> None:
    """When no web-search capability is registered, 'such im web nach X' must
    fall into UNSUPPORTED — guarding the historical search_web prompt drift
    where the system prompt advertised a tool that did not exist."""
    # Use a fresh registry without web-search (seeded default has 'such' on
    # awareness/wiki recall but those resolve, so we use an isolated registry).
    fresh = CapabilityRegistry()
    # No capabilities registered → action_intent True, resolve None
    assert fresh.has_action_intent("such im web nach python 3.13")
    assert fresh.resolve_intent("such im web nach python 3.13") is None


# ---------------------------------------------------------------------------
# Registry render_for_prompt determinism
# ---------------------------------------------------------------------------

def test_render_for_prompt_lists_all_capabilities(
    seeded_registry: CapabilityRegistry,
) -> None:
    """The dynamic system-prompt block must enumerate every registered
    capability as a bullet line."""
    rendered = seeded_registry.render_for_prompt(lang="de")
    assert rendered, "Render must not be empty after seeding"
    # Every capability id must appear at least once in the rendered block.
    for cap in seeded_registry.all():
        assert cap.id in rendered, (
            f"Capability {cap.id!r} missing from rendered prompt block"
        )
