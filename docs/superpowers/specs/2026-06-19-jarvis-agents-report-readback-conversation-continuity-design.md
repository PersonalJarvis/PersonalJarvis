# Jarvis-Agent / Mission Report Readback — Conversation Continuity, Speaking Indicator, Transcript Attribution

**Date:** 2026-06-19
**Status:** Design — approved in brainstorming, pending spec review
**Area:** Voice pipeline (L2), Supervisor state (L6), Sessions transcript (recorder + UI), Orb overlay

---

## 1. Problem

When a voice turn spawns a background worker/mission, Jarvis acknowledges optimistically and the
mission runs off the chat transcript. Some time later the mission finishes and Jarvis reads the
result back to the user via the out-of-band announcement path
(`_on_announcement` → TTS in `jarvis/speech/pipeline.py`). The maintainer reported four distinct
problems with that readback experience:

- **A — "cut off mid-sentence."** The spoken report appeared to stop mid-word ("…and then I'll
  fetch a bit more" → silence), reading as if Jarvis hung up in the middle of the summary.
- **B — Jarvis "hangs up" after the report.** After the report is delivered the user cannot keep
  talking; the microphone is closed. The maintainer wants to be able to either ask a follow-up or
  hang up himself — the conversation should not be unilaterally ended by Jarvis.
- **C — the readback is invisible in the transcript.** What Jarvis said during the report did not
  appear in the transcription log, and it was not attributed to the session/turn where the mission
  was started. The maintainer additionally wants it visually distinguished — a distinct colour and
  a label such as "Jarvis Sub-Agent / Output".
- **D — the mascot/orb does not show "speaking."** During a normal answer the avatar/orb animates
  as speaking; during the mission report it stays idle, so the user has no visual signal that
  Jarvis is talking.

The maintainer framed this as "not a bug, an unimplemented feature" — the desired end state is a
single uninterrupted conversation where a background result lands gracefully and the user stays in
control of when the conversation ends.

## 2. Current reality (verified by code exploration, 2026-06-19)

Two of the four are already partly or fully built:

- **A is already fixed and committed** (`e60f6fd4`). `jarvis/missions/stream_evidence.py::summarize_answers`
  (≈ lines 1016–1046) now truncates the readback on the last sentence boundary in the back half of
  the budget, else the last word boundary, reserving two chars for a trailing `" …"`. The old hard
  `joined[:cap-1]+"…"` slice (which cut mid-word at 600 chars) is gone. → **Part 4 is verification
  only.** The maintainer may still be experiencing the old behaviour because the running app
  instance predates the fix (needs `POST /api/settings/restart-app`).
- **C is half built.** The "afterglow" mechanism in `jarvis/sessions/recorder.py` already attaches a
  post-hangup completion readback to the just-ended session's last turn
  (`_afterglow=(session_id, last_turn_id)` captured at session end ≈ line 312; `_record_posthangup_readback`
  ≈ lines 650–671, guarded to `spoken_kind == SPOKEN_KIND_COMPLETION`). The frontend already renders a
  "Spoken output" block (`TurnCard.tsx` ≈ lines 209–248, sky-blue, badge from `SPOKEN_KIND_LABEL`,
  `completion` → "Background result"). → **What remains for C is the distinct colour + label, and a
  check that the attribution actually reaches every transcript surface the maintainer looks at.**

The two genuine gaps:

- **B — mic closes after the readback.** `_active_session()` (`jarvis/speech/pipeline.py`
  ≈ lines 3752–3839) loops `while not self._hangup_event.is_set()`. On explicit hangup it returns
  `HANGUP_HOTKEY` → `VoiceSessionEnded` → mic closes. A completion readback that arrives afterwards
  punches through the hangup gate (`_on_announcement` ≈ lines 2223–2255, `kind == "completion"`) and
  is *spoken into a closed session* — the mic is never re-opened, so the user cannot continue. A
  `_post_readback_grace_s` window exists but only extends the **idle-timeout** path, not the explicit
  hangup path.
- **D — announcement path skips the speaking state.** The "speaking" visual is driven by
  `SystemStateChanged(new_state="SPEAKING")`, emitted by `Supervisor.set_state()`
  (`jarvis/state/supervisor.py` ≈ lines 47–63), consumed by the frontend
  (`useWebSocket.ts` ≈ lines 87–93 → `VoiceIndicator.tsx` ≈ lines 4–10, pink pulse) and the orb
  (`OverlayBridge.emit_state` in `jarvis/overlay/bridge.py` ≈ lines 202–216; state vocabulary
  `Literal["idle","listening","thinking","typing","clicking","speaking","error","hidden"]`). The
  normal utterance path transitions via `_transition("SPEAKING")` (≈ line 1584). The announcement
  path `_on_announcement` **never calls** `_transition` / `supervisor.set_state`, so no
  `SystemStateChanged` is published and the avatar/orb stay in their previous state.

## 3. Approved behaviour

### Part 1 — Keep listening after the report (the core change)

"The user is still present" is defined as: a background mission was in flight **and** the user had
**not** explicitly hung up (`self._hangup_event` is not set) at the moment the completion readback is
spoken.

- **Present:** after the spoken report, the session stays **open**. The microphone keeps listening,
  the silence/idle timer is reset to a fresh listening window exactly as after a normal answer. The
  user can ask a follow-up or hang up themselves. If the user says nothing, the existing normal idle
  timeout eventually closes the session — Jarvis no longer closes it unilaterally right after the
  report.
- **Already hung up:** the report is still spoken (the existing `kind == "completion"` punch-through
  is preserved — AD-OE6 zero-silent-drop is untouched), but the microphone is **not** re-opened. No
  surprising mic re-activation after the user deliberately ended the session.

Concretely: when a completion readback is voiced and `_hangup_event` is not set, the `_active_session`
loop must return to a fresh LISTENING window instead of closing — extend the existing
`_post_readback_grace_s` / `_last_announcement_spoken_monotonic` logic so it also re-arms the active
listening path (not only the idle-timeout path), and reset the idle deadline at readback time. The
hung-up branch is left exactly as today.

**Critical constraints (voice critical path):**
- No new blocking calls and no LLM call on this path (latency mandate; AP-11).
- Reset any stall/heartbeat watchdog progress counter **per unit of work** so the new "keep
  listening" window cannot re-introduce the BUG-032 / AP-19 stale-cross-unit-counter trap.
- Do not weaken the hangup gate for non-completion announcements (a stale preamble / progress nudge
  must still be suppressed after hangup).

### Part 2 — Speaking indicator during the report

Wrap the announcement readback playback in the same state transition a normal answer uses: set
SPEAKING at the start of the readback, then transition back to **LISTENING** if the session stays
open (Part 1, present case) or **IDLE** if it does not (hung-up case). Because the fix lives at the
Supervisor state-emission layer, it covers every avatar/mascot skin and the orb uniformly — there is
no per-skin work.

Open verification item: confirm the orb actually receives `SystemStateChanged` (the explorer noted
the launcher may forward `SystemStateChanged` to browser WS clients but the Supervisor→OverlayBridge
wiring for the orb is unverified). At minimum the frontend avatar animates; if the orb wiring is
missing, wire `emit_state` off the same transition or record it as a known follow-up.

### Part 3 — Transcript: reliable visibility + distinct attribution

- **Reliable attribution:** the afterglow recording is already implemented; ensure it is actually
  live in the running app (restart) and verify the report reaches every transcript surface the
  maintainer looks at — the per-session Sessions detail view is covered; check whether a separate
  live "Transcription" panel also needs the entry (and extend it if so).
- **Distinct colour + label:** give the mission/Jarvis-Agent readback its own visual identity in the
  transcript — a colour clearly different from a normal answer, and a label like
  "Jarvis Sub-Agent / Output".

  **Recommended approach (lower risk):** keep the backend `spoken_kind == "completion"` unchanged —
  it is load-bearing for three guards (the afterglow recording in `recorder.py`, the hangup
  punch-through in `pipeline.py`, and the heartbeat-cancel on completion). Re-style and re-label the
  `completion` category in the frontend only: a distinct colour in `TurnCard.tsx` and a new
  `SPOKEN_KIND_LABEL["completion"]` text ("Jarvis Sub-Agent / Output"). Zero five-layer churn, zero
  risk to the just-fixed post-hangup-drop guards.

  **Pre-condition to verify:** confirm `SPOKEN_KIND_COMPLETION` is emitted **only** for
  background-mission / Jarvis-Agent readbacks. If it is shared with a non-Jarvis-Agent surface that should
  not carry the "Sub-Agent" label, fall back to introducing a dedicated `spoken_kind` (e.g.
  `subagent`) through all five enum layers (`constants.py` → `models.py` → `schema.sql` payload →
  `types.ts` → `TurnCard.tsx`) **and** update the three completion-keyed guards to treat the new kind
  as completion-class (afterglow eligibility, hangup punch-through, heartbeat cancel) so no readback
  is silently dropped. Extend `tests/unit/sessions/test_spoken_kind_parity.py`.

### Part 4 — Mid-sentence cut: verify

`summarize_answers` is already boundary-safe and committed. Verify on the running system that the
experienced cut is gone after restart. Only if it still occurs, run a focused forensic
(`MissionApproved.summary_de` length in `missions.db` first — a clean truncation reads identically to
an audio cut but is DB-provable).

## 4. Components touched

| Unit | File | Change |
|---|---|---|
| Active-session loop | `jarvis/speech/pipeline.py` `_active_session` (≈3752–3839) | Re-arm a fresh LISTENING window after a completion readback when not hung up; reset idle deadline; reset watchdog counter per unit |
| Announcement handler | `jarvis/speech/pipeline.py` `_on_announcement` (≈2223–2255) | Emit SPEAKING around readback playback; on finish transition to LISTENING (open) or IDLE (hung up) |
| Supervisor | `jarvis/state/supervisor.py` (≈47–63) | Reuse `set_state` — no change expected unless a new transition helper is cleaner |
| Transcript styling | `jarvis/ui/web/frontend/src/components/sessions/TurnCard.tsx` (≈28–45, 209–248) | Distinct colour for the mission readback block; relabel `completion` → "Jarvis Sub-Agent / Output" |
| (Conditional) spoken_kind enum | `constants.py`, `models.py`, `schema.sql`, `types.ts`, `TurnCard.tsx` + parity test | Only if `completion` proves shared with non-Jarvis-Agent surfaces |
| Orb wiring (verify) | `jarvis/ui/web/launcher.py`, `jarvis/overlay/bridge.py` | Confirm/append `emit_state` off the speaking transition |

## 5. Testing (TDD, repo convention — fakes not mocks)

- **Part 1:** unit test on `_active_session` continuation — assert the session stays open and the
  idle window is reset after a completion readback when `_hangup_event` is not set, and that it does
  **not** re-open when `_hangup_event` is set. Assert on elapsed/loop state, not return value (the
  broad `except (CancelledError, Exception)` around awaited tasks can swallow signals — see the
  thinking-interrupt-wedge lesson). Guard against the BUG-032 watchdog re-trigger.
- **Part 2:** test that the announcement path publishes `SystemStateChanged(SPEAKING)` then the
  correct follow-up state, mirroring the normal-utterance assertion.
- **Part 3:** frontend test (vitest) for the new label/colour; if the dedicated-kind fallback is
  taken, extend `tests/unit/sessions/test_spoken_kind_parity.py` and the three guard tests.
- **Part 4:** rely on existing `tests/missions/test_stream_evidence.py` boundary tests; add a live
  verification step (not a unit test).
- Run the existing voice/speech, sessions, and missions buckets to confirm no regression
  (`pytest tests/unit/speech/ tests/unit/sessions/ tests/missions/`).

## 6. Anti-patterns / invariants to respect

- AP-9: nothing on this path may touch awareness/wiki or any read-heavy work — it is the voice
  critical path.
- AP-11: no LLM call inside the spoken path; scrub stays regex-only.
- AP-19 / BUG-032: reset stall/heartbeat watchdog counters per unit of work; the new listening
  window must not be measured against a stale cross-unit counter.
- AD-OE5 / AD-OE6: zero silent drops — the completion readback still punches through after hangup;
  only the *mic re-open* is gated on "user still present."
- Runtime output language doctrine: any new spoken phrase resolves through the one language resolver
  (de/en/es), never a per-layer hardcoded default. (No new spoken phrase is expected here, but the
  state/label work must not introduce one without routing it.)
- Shared working tree: stage hunk-isolated; do not sweep other sessions' in-flight edits.

## 7. Out of scope

- Changing whether the report is spoken at all after hangup (kept — AD-OE6).
- Routing long informational answers to the chat UI instead of speaking them (explicitly declined
  earlier; not revisited here).
- Any change to mission execution, the critic loop, or the worker harness — this is purely the
  readback-delivery and transcript-presentation surface.
