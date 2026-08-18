"""The reasoning-effort field must admit the values callers actually pass.

The composer now passes ``none``; the work splitter still passes ``medium``.
Both must stay in the annotation — an annotation that is wrong is worse
than none, because the next caller trusts it.
"""
from __future__ import annotations

import typing

from jarvis.core.protocols import BrainRequest


def test_declared_values_cover_every_level_in_use() -> None:
    hints = typing.get_type_hints(BrainRequest)
    literal = typing.get_args(typing.get_args(hints["reasoning_effort"])[0])
    assert {"none", "low", "medium", "high"} <= set(literal)


def test_medium_is_accepted_by_the_work_splitter() -> None:
    """The work splitter still passes medium; the composer now passes none."""
    request = BrainRequest(messages=(), reasoning_effort="medium")
    assert request.reasoning_effort == "medium"


def test_none_still_means_disable_thinking() -> None:
    """The original meaning must survive the widening — structured-output
    callers rely on it to keep thinking from eating their token budget."""
    request = BrainRequest(messages=(), reasoning_effort="none")
    assert request.reasoning_effort == "none"


def test_unset_stays_the_provider_default() -> None:
    assert BrainRequest(messages=()).reasoning_effort is None
