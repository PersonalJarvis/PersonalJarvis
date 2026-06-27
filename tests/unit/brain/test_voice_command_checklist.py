"""Runs the whole recognition checklist, and guards that every command KIND the
gate can emit has a matching honesty test — so a new deterministic command
cannot be added without an honest readback (the audit 2026-06-27 root class).
"""
from __future__ import annotations

import typing

import pytest

from jarvis.brain.voice_command_gate import VoiceCommandMatch, match_voice_command

from tests.unit.brain.voice_command_cases import RECOGNITION_CASES


@pytest.mark.parametrize("utterance,kind,target", RECOGNITION_CASES)
def test_recognition_checklist(utterance: str, kind: str, target: str) -> None:
    m = match_voice_command(utterance)
    assert m is not None, f"not recognised: {utterance!r}"
    assert m.kind == kind, f"{utterance!r}: expected {kind}, got {m.kind}"
    if target:
        assert m.target == target, f"{utterance!r}: expected target {target!r}, got {m.target!r}"


def test_every_command_kind_has_an_honesty_test() -> None:
    """Anti-drift: the set of kinds the gate can emit must equal the set we have
    deliberately given an honest readback. Adding a new kind to the gate without
    updating this guard (and adding an honesty test) fails here — the structural
    insurance against a new silent/blind command."""
    # The kinds the gate's Literal can emit (single source of truth). get_type_hints
    # resolves the string annotation (from __future__ import annotations) to the
    # real Literal so get_args returns the values.
    hints = typing.get_type_hints(VoiceCommandMatch)
    gate_kinds = set(typing.get_args(hints["kind"]))
    # The kinds we have audited and given an honest readback (2026-06-27).
    audited_kinds = {
        "provider_switch",
        "subagent_switch",
        "language_switch",
        "cancel",
        "depth_deep",
        "depth_fast",
    }
    assert gate_kinds == audited_kinds, (
        "voice command kinds drifted — a new kind needs an honest readback + an "
        f"honesty test. gate={sorted(gate_kinds)} audited={sorted(audited_kinds)}"
    )
