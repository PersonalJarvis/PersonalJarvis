"""The probe's scenario judge, exercised without any live call.

``evaluate_scenario`` is pure over the capture rows + postmortem, so every
assert's pass/fail boundary is pinned here — the live integration test then
only has to trust one judgment path, not two.
"""

from __future__ import annotations

from typing import Any

from jarvis.diagnostics.realtime_probe import (
    CONTINUITY_GAP_S,
    evaluate_scenario,
)

MANIFEST = [
    {
        "id": "en_giraffe",
        "text": "What color is the violet giraffe from my example? Answer in one short sentence.",
        "nonce": "violet giraffe",
        "language": "en",
    },
]


def _row(mono: float, src: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    return {"ts": mono, "mono": mono, "src": src, "kind": kind, "data": data}


def _transcript(mono: float, role: str, text: str, *, final: bool = True) -> dict[str, Any]:
    return _row(
        mono,
        "surface",
        "json",
        {"type": "transcript", "role": role, "text": text, "is_final": final},
    )


def _happy_rows() -> list[dict[str, Any]]:
    rows = [
        _row(0.0, "probe", "scenario", {"name": "t"}),
        _row(1.0, "probe", "ready", {"type": "audio_ready"}),
        _row(2.0, "probe", "speak", {"id": "en_giraffe"}),
        _transcript(4.0, "user", "what color is the violet giraffe"),
    ]
    # A contiguous reply: chunks 100 ms apart, each carrying 100 ms of audio.
    mono = 5.0
    for _ in range(10):
        rows.append(_row(mono, "surface", "binary", {"bytes": 4800}))
        mono += 0.1
    rows.append(_transcript(mono, "assistant", "It is violet."))
    rows.append(_row(mono + 0.2, "surface", "json", {"type": "turn_complete"}))
    return rows


def _pm(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "turns_completed": 1,
        "rebuilds": 0,
        "stun_retries": 0,
        "recv_dropped_frames": 0,
        "sender_pacing_resyncs": 0,
        "sender_shed_frames": 0,
        "ready_ms": 2500,
        "first_audio_ms": 4200,
        "close_clean": True,
    }
    base.update(overrides)
    return base


def _judge(
    rows: list[dict[str, Any]],
    pm: dict[str, Any],
    asserts: list[str],
    *,
    budgets: dict[str, Any] | None = None,
    end_duration_s: float = 1.0,
) -> dict[str, dict[str, str]]:
    return evaluate_scenario(
        rows,
        pm,
        {"name": "t", "asserts": asserts},
        MANIFEST,
        budgets=budgets or {},
        end_duration_s=end_duration_s,
    )


def test_happy_path_passes_everything() -> None:
    verdicts = _judge(
        _happy_rows(),
        _pm(),
        [
            "turns>=1",
            "no_ungrounded",
            "no_roleplay",
            "audio_continuity",
            "no_rebuilds",
            "mic_wall_clock",
            "spawn_budget",
            "clean_close",
        ],
    )
    assert all(v["status"] == "pass" for v in verdicts.values()), verdicts


def test_missing_turn_fails_the_count() -> None:
    rows = _happy_rows()
    verdicts = _judge(rows, _pm(), ["turns>=2"])
    assert verdicts["turns>=2"]["status"] == "fail"


def test_ungrounded_boundary_is_allowed_once_as_greeting_then_fails() -> None:
    rows = _happy_rows()
    # A greeting boundary 3 s after ready (inside the window) - allowed.
    rows.insert(3, _row(3.0, "surface", "json", {"type": "turn_complete"}))
    assert _judge(rows, _pm(), ["no_ungrounded"])["no_ungrounded"]["status"] == "pass"
    # A second ungrounded boundary long after the window - self-talk.
    rows.append(_row(30.0, "surface", "json", {"type": "turn_complete"}))
    assert _judge(rows, _pm(), ["no_ungrounded"])["no_ungrounded"]["status"] == "fail"


def test_roleplay_detects_the_full_question_but_not_the_topic() -> None:
    rows = _happy_rows()
    # Answering with the nonce topic is legitimate.
    assert _judge(rows, _pm(), ["no_roleplay"])["no_roleplay"]["status"] == "pass"
    # Speaking the user's full question verbatim is the both-sides signature.
    rows.append(
        _transcript(
            9.0,
            "assistant",
            "Hi! What color is the violet giraffe from my example? "
            "Answer in one short sentence. Well, violet!",
        )
    )
    assert _judge(rows, _pm(), ["no_roleplay"])["no_roleplay"]["status"] == "fail"


def test_selftalk_on_a_silent_microphone_fails() -> None:
    rows = [
        _row(1.0, "probe", "ready", {"type": "audio_ready"}),
        _transcript(20.0, "assistant", "Hey. Hi, what's up?", final=False),
    ]
    assert _judge(rows, _pm(), ["no_selftalk"])["no_selftalk"]["status"] == "fail"
    early = [
        _row(1.0, "probe", "ready", {"type": "audio_ready"}),
        _transcript(4.0, "assistant", "Hey!", final=False),
    ]
    assert _judge(early, _pm(), ["no_selftalk"])["no_selftalk"]["status"] == "pass"


def test_audio_hole_beyond_the_gap_budget_fails_continuity() -> None:
    rows = _happy_rows()
    verdict = _judge(rows, _pm(), ["audio_continuity"])["audio_continuity"]
    assert verdict["status"] == "pass"
    # Inject a hole: the next chunk arrives seconds late inside the same turn.
    late = _happy_rows()
    hole_at = late[-3]["mono"] + CONTINUITY_GAP_S + 1.0
    late.insert(-2, _row(hole_at, "surface", "binary", {"bytes": 4800}))
    assert _judge(late, _pm(), ["audio_continuity"])["audio_continuity"]["status"] == "fail"


def test_postmortem_counters_gate_their_asserts() -> None:
    rows = _happy_rows()
    assert _judge(rows, _pm(rebuilds=1), ["no_rebuilds"])["no_rebuilds"]["status"] == "fail"
    assert (
        _judge(rows, _pm(sender_shed_frames=3), ["mic_wall_clock"])["mic_wall_clock"]["status"]
        == "fail"
    )
    assert (
        _judge(rows, _pm(close_clean=False), ["clean_close"])["clean_close"]["status"]
        == "fail"
    )
    assert (
        _judge(rows, _pm(), ["clean_close"], end_duration_s=30.0)["clean_close"]["status"]
        == "fail"
    )


def test_spawn_budget_warns_until_enforced() -> None:
    rows = _happy_rows()
    slow = _pm(ready_ms=9000)
    assert _judge(rows, slow, ["spawn_budget"])["spawn_budget"]["status"] == "warn"
    enforced = _judge(rows, slow, ["spawn_budget"], budgets={"enforce": True})
    assert enforced["spawn_budget"]["status"] == "fail"


def test_unknown_assert_is_a_scenario_bug() -> None:
    verdicts = _judge(_happy_rows(), _pm(), ["definitely_not_a_check"])
    assert verdicts["definitely_not_a_check"]["status"] == "fail"
