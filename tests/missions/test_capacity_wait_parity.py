"""Five-layer parity for WAITING_CAPACITY (AP-4).

The parked state and its reason vocabulary cross Python (state machine, event
payload, voice phrases), SQL (the state column), TypeScript (missions.ts) and
the UI (badge + i18n). A value added on one side only is the BUG-008 drift
class: the frontend parser rejects the envelope or a badge renders a raw key.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import get_args

import pytest

from jarvis.missions.events import CAPACITY_WAIT_REASONS, MissionWaitingCapacity
from jarvis.missions.state_machine import MissionState
from jarvis.missions.voice.readback import CAPACITY_WAIT_PHRASES

_FRONTEND = Path(__file__).resolve().parents[2] / "jarvis" / "ui" / "web" / "frontend" / "src"
_MISSIONS_TS = (_FRONTEND / "types" / "missions.ts").read_text(encoding="utf-8")
_LOCALES = ("de", "en", "es")


def _ts_union(name: str) -> set[str]:
    m = re.search(rf"export type {name}\s*=([^;]+);", _MISSIONS_TS)
    assert m, f"{name} union not found in missions.ts"
    return set(re.findall(r'"([^"]+)"', m.group(1)))


def _locale(lang: str) -> dict:
    return json.loads((_FRONTEND / "i18n" / "locales" / f"{lang}.json").read_text(encoding="utf-8"))


def test_mission_state_python_ts_parity() -> None:
    py = {s.value for s in MissionState}
    ts = _ts_union("MissionState")
    assert py == ts, f"state drift — python-only={py - ts}, ts-only={ts - py}"


def test_reason_vocabulary_python_ts_parity() -> None:
    payload_reasons = set(get_args(MissionWaitingCapacity.model_fields["reason"].annotation))
    assert payload_reasons == set(CAPACITY_WAIT_REASONS)
    assert _ts_union("CapacityWaitReason") == set(CAPACITY_WAIT_REASONS)


def test_event_type_listed_in_ts() -> None:
    assert "MissionWaitingCapacity" in _ts_union("EventType")


@pytest.mark.parametrize("lang", ["de", "en"])
def test_voice_phrases_cover_every_reason(lang: str) -> None:
    assert set(CAPACITY_WAIT_REASONS) <= set(CAPACITY_WAIT_PHRASES[lang])


def test_voice_phrase_tables_share_keys() -> None:
    assert set(CAPACITY_WAIT_PHRASES["de"]) == set(CAPACITY_WAIT_PHRASES["en"])


@pytest.mark.parametrize("lang", _LOCALES)
def test_ui_locales_cover_badge_and_reasons(lang: str) -> None:
    data = _locale(lang)
    assert data["mission_state"]["waiting_capacity"]
    card = data["missions_view"]["capacity_wait"]
    for reason in CAPACITY_WAIT_REASONS:
        assert "{provider}" in card[reason]


def test_ui_locales_share_capacity_keys() -> None:
    keys = [set(_locale(lang)["missions_view"]["capacity_wait"]) for lang in _LOCALES]
    assert all(k == keys[0] for k in keys)


def test_event_fields_python_ts_parity() -> None:
    m = re.search(
        r"export interface MissionWaitingCapacity extends BasePayload \{([^}]+)\}", _MISSIONS_TS
    )
    assert m, "MissionWaitingCapacity interface not found in missions.ts"
    ts_fields = set(re.findall(r"^\s*(\w+):", m.group(1), re.MULTILINE))
    assert ts_fields == set(MissionWaitingCapacity.model_fields)


def test_repeated_pause_is_not_announced_again() -> None:
    from jarvis.missions.events import EventEnvelope
    from jarvis.missions.voice.listener import MissionVoiceListener
    from jarvis.missions.voice.readback import MissionReadback

    first = MissionWaitingCapacity(reason="provider_quota", provider="claude", steps_total=2)
    again = first.model_copy(update={"resume_attempt": 1, "repeat": True})
    listener = MissionVoiceListener.__new__(MissionVoiceListener)
    listener._readback = MissionReadback()
    listener._announce_critic_loop = False

    def env(payload: MissionWaitingCapacity) -> EventEnvelope:
        return EventEnvelope(mission_id="m", source_actor="kontrollierer", ts_ms=1, payload=payload)

    assert listener._render(env(first), "de")
    assert listener._render(env(again), "de") == ""
