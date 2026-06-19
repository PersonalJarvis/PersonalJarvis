"""Deterministic proof that the clarify-question timer no longer barges in on a
user who resumed speaking — replays the exact live incident (2026-06-17 14:47,
session f6403ec0) WITHOUT a microphone, so the intermittent "interrupted me
mid-sentence" bug can be verified every run instead of by luck.

Live sequence being reproduced (from data/jarvis_desktop.log):
  1. user trails off on "...liegt sie im..." → VAD silence endpoint after ~3 s
  2. ContinuationBuffer holds it (reason=trailing_ellipsis) and FORCE-arms the
     clarify timer (2.5 s)
  3. ~4 ms later the user RESUMES speaking the continuation (turn-state
     USER_SPEAKING)
  4. BUG (pre-fix): the timer fired 2.5 s into the continuation, spoke
     "Wie meinst du das genau?" over the user, and discarded the held half →
     the continuation reached the brain alone → confused non-answer.

This driver asserts the FIXED behaviour:
  A. while the user holds the floor the timer NEVER speaks and NEVER discards
  B. once the floor clears (genuine trail-off into silence) the question still
     fires — the AD-OE6 zero-silent-drop fallback is preserved
  C. the held fragment + the continuation coalesce into ONE complete question

Run:  "C:\\Program Files\\Python311\\python.exe" scripts/verify_clarify_floor_guard.py
Exit code 0 = PASS, 1 = FAIL (regression).
"""
from __future__ import annotations

import asyncio
import sys
from unittest.mock import MagicMock

# Windows console is cp1252 by default; the transcript + the "→" marker are
# UTF-8 (CLAUDE.md Windows-specifics: reconfigure stdout or stick to ASCII).
try:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
except Exception:  # noqa: BLE001 — best effort; older streams lack reconfigure
    pass

from jarvis.speech.completion import REASON_TRAILING_ELLIPSIS, is_incomplete
from jarvis.speech.continuation_buffer import ContinuationBuffer
from jarvis.speech.pipeline import SpeechPipeline, TurnTakingState

# The real transcript pair from the incident.
TURN0 = "Was ist die größte Search-Engine der Welt und mit wie viel Prozent liegt sie im..."  # noqa: E501 - i18n-allow: real German voice transcript under test
TURN1 = "im Lead zu Vergleich zu anderen."  # i18n-allow: real German voice transcript under test


def _make_pipe(clarify_after_ms: int = 60) -> SpeechPipeline:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._continuation_buffer = ContinuationBuffer()
    pipe._clarify_timer_task = None
    pipe._turn_state = TurnTakingState.LISTENING

    voice_cfg = MagicMock()
    voice_cfg.clarify_incomplete_enabled = True
    voice_cfg.clarify_after_ms = clarify_after_ms
    cfg = MagicMock()
    cfg.voice = voice_cfg
    pipe._config = cfg

    pipe._spoken = []  # type: ignore[attr-defined]

    async def _fake_speak(text, language=None, *, kind="reply"):
        pipe._spoken.append((text, language))  # type: ignore[attr-defined]
        return True

    async def _fake_set_turn_state(state):
        pipe._turn_state = state

    pipe._speak = _fake_speak  # type: ignore[method-assign]
    pipe._set_turn_state = _fake_set_turn_state  # type: ignore[method-assign]
    return pipe


def _ok(label: str, cond: bool) -> bool:
    print(f"  [{'PASS' if cond else 'FAIL'}] {label}")
    return cond


async def main() -> int:
    print("Replaying the 2026-06-17 14:47 clarify-barge-in incident\n")
    results: list[bool] = []

    # Step 1+2: user trails off, fragment held, clarify force-armed.
    pipe = _make_pipe(clarify_after_ms=60)
    held = pipe._continuation_buffer.process(TURN0, language="de")
    results.append(_ok("turn 0 held as incomplete (not dispatched)", held is None))
    results.append(
        _ok(
            "held reason is trailing_ellipsis",
            pipe._continuation_buffer.last_reason == REASON_TRAILING_ELLIPSIS,
        )
    )

    # Step 3: the user RESUMED speaking the continuation — they hold the floor.
    pipe._turn_state = TurnTakingState.USER_SPEAKING
    pipe._arm_clarify_question("de", force=True)

    # Step 4 (the bug window): let SEVERAL grace windows elapse while the user
    # is still speaking. Pre-fix this is exactly when it spoke over the user.
    await asyncio.sleep(0.30)
    results.append(_ok("A) did NOT speak while user holds the floor", pipe._spoken == []))
    results.append(
        _ok(
            "A) held fragment was NOT discarded",
            pipe._continuation_buffer.has_pending() is True,
        )
    )

    # Step C: the continuation finalises → it coalesces with the held half into
    # ONE complete question (this is the path _handle_utterance takes).
    pipe._cancel_clarify_question()
    coalesced = pipe._continuation_buffer.process(TURN1, language="de")
    merged_ok = (
        coalesced is not None
        and TURN0.split("...")[0].strip()[:20] in coalesced
        and TURN1.rstrip(".") in coalesced
        and is_incomplete(coalesced, language="de") is None
    )
    results.append(_ok("C) continuation coalesced into ONE complete question", merged_ok))
    print(f"      → merged: {coalesced!r}")

    # Step B: the fallback is preserved — a GENUINE trail-off into silence (user
    # never continues) still gets the clarifying question once the floor clears.
    pipe2 = _make_pipe(clarify_after_ms=60)
    pipe2._continuation_buffer.process(TURN0, language="de")
    pipe2._turn_state = TurnTakingState.USER_SPEAKING
    pipe2._arm_clarify_question("de", force=True)
    await asyncio.sleep(0.2)  # deferred while floor held
    deferred_clean = pipe2._spoken == []
    pipe2._turn_state = TurnTakingState.LISTENING  # user truly stopped
    await asyncio.sleep(0.2)  # re-armed timer now fires
    fallback_ok = deferred_clean and len(pipe2._spoken) == 1 and pipe2._spoken[0][0].strip().endswith("?")
    results.append(_ok("B) clarify still fires after the floor genuinely clears", fallback_ok))

    passed = all(results)
    print(f"\n{'='*60}\n{'PASS — clarify floor guard holds' if passed else 'FAIL — REGRESSION'}\n{'='*60}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
