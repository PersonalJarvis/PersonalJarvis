"""Verdict functions over realtime session postmortem counters.

Shared by the two consumers so their verdicts can never drift apart:
``scripts/diag_voice_sessions.py`` (flight-recorder audits after live rounds)
and the codex live-call probe. Pure functions over plain dicts - no bus, no
I/O, no heavy imports; nothing on the boot path imports this module
(AP-26/AP-9).

The input is the payload of one ``RealtimeSessionPostmortem`` event
(``jarvis/core/events.py``): session-lifetime counters, all zero in a healthy
call except ``turns_completed`` - and, until the real ChatGPT-Live terminal
item is confirmed, ``quiescence_boundary_turns``.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

#: Spawn budgets. The forensics report treats an overrun as a WARN; the live
#: probe starts at WARN too and flips to FAIL once the latency phase lands
#: (that flip lives in the probe, deliberately not here).
#:
#: The first-audio budget judges ``first_final_to_first_audio_ms`` — the wait
#: the USER experiences between finishing an utterance and hearing the answer.
#: It deliberately does NOT judge ``first_audio_ms``: that metric counts from
#: session start and therefore includes the user's own speaking/listening
#: time, so a healthy codex call (8 311 ms from start, 923 ms after the
#: final, live 2026-08-08) read as a budget breach. ``first_audio_ms`` stays
#: in the payload and the detail line for continuity with old recordings.
SPAWN_READY_BUDGET_MS = 4_000
SPAWN_FIRST_AUDIO_BUDGET_MS = 6_000

#: Mirrors the adapter's refusal-storm verdict (``_REFUSALS_BEFORE_REBUILD``):
#: at this many refused automatic responses the far end was talking to itself.
_REFUSAL_STORM = 5


@dataclass(frozen=True, slots=True)
class RealtimeFinding:
    severity: str  # "high" | "warn" | "info"
    kind: str
    detail: str


def _count(pm: Mapping[str, Any], key: str) -> int:
    try:
        return int(pm.get(key, 0) or 0)
    except (TypeError, ValueError):
        # A malformed postmortem field reads as zero — the findings below
        # judge counts, and "unknown" must not fabricate a failure class.
        return 0


def evaluate_postmortem(pm: Mapping[str, Any]) -> list[RealtimeFinding]:
    """Map one postmortem payload onto the known realtime failure classes."""
    findings: list[RealtimeFinding] = []
    add = findings.append
    turns = _count(pm, "turns_completed")

    splices = _count(pm, "response_splices")
    if splices:
        add(
            RealtimeFinding(
                "high",
                "splice-seam",
                f"{splices} back-to-back response(s) spliced into one "
                "playback stream - audible garble",
            )
        )

    identity_drops = _count(pm, "response_identity_drops")
    if identity_drops:
        add(
            RealtimeFinding(
                "high",
                "response-identity-mismatch",
                f"{identity_drops} late or mismatched provider response "
                "event(s) were dropped before transcript could clear PCM",
            )
        )

    unsafe_cancellations = _count(pm, "unsafe_output_cancellations")
    recovery_failures = _count(pm, "output_transcript_recovery_failures")
    if unsafe_cancellations or recovery_failures:
        add(
            RealtimeFinding(
                "high",
                "output-transcript-recovery-failed",
                f"{recovery_failures} local transcript recovery attempt(s) "
                f"failed and {unsafe_cancellations} provider response(s) "
                "were cancelled fail-closed",
            )
        )
    elif _count(pm, "output_shadow_recovery_exhausted"):
        add(
            RealtimeFinding(
                "warn",
                "output-shadow-recovery-exhausted",
                "bounded early output-transcript recovery was exhausted; "
                "the provider transcript or terminal pass still completed",
            )
        )

    quiescence = _count(pm, "quiescence_boundary_turns")
    if quiescence:
        add(
            RealtimeFinding(
                "warn" if quiescence >= 2 else "info",
                "turn-without-boundary",
                f"{quiescence} of {turns} turn(s) ended on the local silence "
                "backstop, not a protocol boundary",
            )
        )

    captions = _count(pm, "ungrounded_captions_dropped")
    if captions >= 2:
        add(
            RealtimeFinding(
                "high",
                "ungrounded-caption-storm",
                f"{captions} server user captions with no microphone energy - "
                "the far end invented user turns",
            )
        )
    elif captions:
        add(
            RealtimeFinding(
                "warn",
                "ungrounded-caption",
                "1 server user caption with no microphone energy",
            )
        )

    refused = _count(pm, "ungrounded_responses_refused")
    if refused >= _REFUSAL_STORM:
        add(
            RealtimeFinding(
                "high",
                "refusal-storm",
                f"{refused} automatic responses refused with no grounded "
                "speech between them",
            )
        )
    elif refused >= 3:
        add(
            RealtimeFinding(
                "warn",
                "refusal-run",
                f"{refused} automatic responses refused",
            )
        )

    if _count(pm, "self_dialogue_rebuilds"):
        add(
            RealtimeFinding(
                "high",
                "self-dialogue",
                "the self-dialogue detector forced a transport replacement",
            )
        )

    handoff_misses = _count(pm, "handoff_obligation_misses")
    if handoff_misses:
        add(
            RealtimeFinding(
                "high" if handoff_misses >= 2 else "warn",
                "handoff-obligation-miss",
                f"{handoff_misses} planner-confirmed action turn(s) did not "
                "receive the provider's required handoff control event",
            )
        )

    mutes = _count(pm, "mute_emergency_releases")
    if mutes:
        add(
            RealtimeFinding(
                "warn",
                "mute-emergency-release",
                f"{mutes} half-duplex mute(s) released only by the emergency "
                "timer - a turn ended without any boundary",
            )
        )

    stall = _count(pm, "max_loop_stall_ms")
    if stall > 1_000:
        add(
            RealtimeFinding(
                "high",
                "event-loop-stall",
                f"event loop stalled {stall} ms mid-call - a blocking call "
                "ran on the loop",
            )
        )

    resyncs = _count(pm, "sender_pacing_resyncs")
    shed = _count(pm, "sender_shed_frames")
    if resyncs or shed:
        add(
            RealtimeFinding(
                "high",
                "sender-behind-wall-clock",
                f"microphone sender resynced {resyncs}x and shed {shed} stale "
                "frame(s) - user speech was lost",
            )
        )

    dropped = _count(pm, "recv_dropped_frames")
    if dropped:
        add(
            RealtimeFinding(
                "warn",
                "recv-overflow",
                f"{dropped} provider audio frame(s) dropped on a full "
                "receive queue",
            )
        )

    rebuilds = _count(pm, "rebuilds")
    stun = _count(pm, "stun_retries")
    if rebuilds >= 2 or stun >= 1:
        add(
            RealtimeFinding(
                "high",
                "rebuild-loop",
                f"{rebuilds} transport rebuild(s) and {stun} STUN retry(ies) "
                "in one session",
            )
        )
    elif rebuilds:
        add(RealtimeFinding("warn", "transport-rebuild", "1 transport rebuild"))

    flips = _count(pm, "language_flips")
    if flips >= 2:
        add(
            RealtimeFinding(
                "warn",
                "language-mismatch",
                f"the call's output language flipped {flips}x",
            )
        )

    ready_ms = _count(pm, "ready_ms")
    first_audio_ms = _count(pm, "first_audio_ms")
    response_ms = _count(pm, "first_final_to_first_audio_ms")
    over_ready = ready_ms > SPAWN_READY_BUDGET_MS
    # 0 means "never measured" (pre-metric recording, no final, or no audible
    # audio after one) — silence there is reported by other counters, and an
    # absent metric must not fabricate a budget breach.
    over_first = bool(response_ms) and response_ms > SPAWN_FIRST_AUDIO_BUDGET_MS
    if over_ready or over_first:
        add(
            RealtimeFinding(
                "warn",
                "spawn-over-budget",
                f"ready {ready_ms} ms (budget {SPAWN_READY_BUDGET_MS}), "
                f"first audio {response_ms} ms after the first user final "
                f"(budget {SPAWN_FIRST_AUDIO_BUDGET_MS}; "
                f"{first_audio_ms} ms from session start)",
            )
        )

    if not pm.get("close_clean", True):
        add(
            RealtimeFinding(
                "warn",
                "unclean-close",
                "teardown abandoned the provider socket or the stream failed",
            )
        )

    return findings


def extract_postmortems(
    rows: Iterable[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    """Pull postmortem payloads out of flight-recorder or probe JSONL rows.

    Accepts both shapes: the flight recorder's
    ``{"event": "RealtimeSessionPostmortem", "payload": {...}}`` and the live
    probe's ``{"src": "postmortem", "data": {...}}``.
    """
    payloads: list[dict[str, Any]] = []
    for row in rows:
        if row.get("event") == "RealtimeSessionPostmortem":
            payload = row.get("payload")
            if isinstance(payload, Mapping):
                payloads.append(dict(payload))
        elif row.get("src") == "postmortem":
            data = row.get("data")
            if isinstance(data, Mapping):
                payloads.append(dict(data))
    return payloads
