"""The reasoning-effort field must admit the values callers actually pass.

Found while wiring the Agentic IDE writer: the composer has been passing
"medium" since it was written, into a field declared to accept only "none". It
survives only because nothing validates it — the annotation is documentation
that is wrong, and the next person to trust it writes a bug.
"""
from __future__ import annotations

import typing

from jarvis.core.protocols import BrainRequest


def test_declared_values_cover_every_level_in_use() -> None:
    hints = typing.get_type_hints(BrainRequest)
    literal = typing.get_args(typing.get_args(hints["reasoning_effort"])[0])
    assert {"none", "low", "medium", "high"} <= set(literal)


def test_medium_is_accepted_by_the_agentic_ide_composer() -> None:
    """The composer's live value: judgement work, not transcription."""
    request = BrainRequest(messages=(), reasoning_effort="medium")
    assert request.reasoning_effort == "medium"


def test_none_still_means_disable_thinking() -> None:
    """The original meaning must survive the widening — structured-output
    callers rely on it to keep thinking from eating their token budget."""
    request = BrainRequest(messages=(), reasoning_effort="none")
    assert request.reasoning_effort == "none"


def test_unset_stays_the_provider_default() -> None:
    assert BrainRequest(messages=()).reasoning_effort is None
