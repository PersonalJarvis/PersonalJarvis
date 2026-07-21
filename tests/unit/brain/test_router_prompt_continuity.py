"""BUG-106 guards: the router prompt must carry the spoken-input continuity
rules.

Live 2026-07-21 11:36: a conversation about a Gulfstream 800 got the garbled
STT turn "braucht die Golf 100 Start- und Landebahn"; the router searched the  # i18n-allow: quoted garbled STT transcript under test
literal G100 (a different aircraft) and then delivered a verdict contradicting
its own search numbers, anchored on an earlier fabricated claim in history.
"""

from jarvis.brain.router import SYSTEM_PROMPT


def test_prompt_resolves_sound_alike_entities_to_the_discussed_one():
    """A garbled sound-alike variant of an entity under discussion must be
    resolved to that entity, not researched literally."""
    assert "SPOKEN-INPUT CONTINUITY" in SYSTEM_PROMPT
    assert "sound-alike variant" in SYSTEM_PROMPT
    # The resolution must reach every downstream request, not just the reply.
    assert "search_web queries" in SYSTEM_PROMPT


def test_prompt_ranks_fresh_tool_data_above_prior_assistant_claims():
    """Conclusions must follow from fresh tool data; an earlier assistant
    claim in history must never bend new numbers to match it."""
    normalized = " ".join(SYSTEM_PROMPT.split())
    assert "Fresh tool data outranks your own previous statements" in normalized
    assert "your conclusion must follow from THAT data" in normalized
