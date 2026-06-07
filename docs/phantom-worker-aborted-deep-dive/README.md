---
title: "Deep-Dive: 'Der Worker ist abgebrochen' announced for a mission the user never knowingly spawned"
date: 2026-06-07
status: LAYER A FIXED (2026-06-07, TDD, tests green) — Layers B/C/D still open
fix_commit: uncommitted working tree (restart required for live voice)
scope: Voice failure announcement · Mission-Manager outcome aggregation · Computer-Use force-spawn
investigators: 2 parallel sub-agents (runtime forensics + static code-trace) + main-thread verification
incident_mission_id: 019ea1da-4552-7ec4-8a88-ffba1773521e
---

# Phantom "Der Worker ist abgebrochen" — Deep-Dive

> **Layer A is now FIXED (2026-06-07).** §2–§7 below located the bug; §8 planned
> the fix. The implementation of Layer A and its proof are recorded in
> **§8.A-IMPLEMENTED** at the end of §8. Layers B/C/D remain open as planned.

## 0a. What changed (Layer A — the spoken phrase)

A worker that runs out of time on its final attempt is now failed with the
honest reason **`attempts_timed_out`** → voice says **"Das Zeitlimit wurde
überschritten."** instead of the false, alarming **"Der Worker ist
abgebrochen."** Implemented test-first; see **§8.A-IMPLEMENTED**.

- `jarvis/missions/kontrollierer/orchestrator.py` — new `TaskOutcome.TIMED_OUT`
  (mirrors the `SETUP_FAILED` precedent); the terminal worker-error branch
  returns it when `is_timeout`; a new aggregation branch maps it to
  `attempts_timed_out` (ranked below the more-specific reasons, above the
  generic `task_error` fallback).
- `jarvis/missions/voice/readback.py` — `FAILURE_REASON_PHRASES` gains
  `attempts_timed_out` in de + en (parity-pinned).
- Tests: `tests/missions/kontrollierer/test_loop.py`,
  `tests/missions/test_voice_readback.py`, `tests/missions/test_voice_announcer.py`.
- Verification: full `tests/missions/` suite **751 passed, 2 skipped**; 0 new
  ruff findings; code-review APPROVE_WITH_NITS (nits applied).

---

## 1. TL;DR (one paragraph)

The user heard Jarvis say **"Die Mission ist fehlgeschlagen. Der Worker ist
abgebrochen."** and reported they had *never spawned a sub-agent*. The forensic
ground truth says otherwise but **proves the user's perception is the real
bug**: a normal-sounding voice command — *"…Explorer öffnen mit Computer-User"*
— was silently turned into a full background **Worker-Critic mission** by the
force-spawn heuristic. That mission then ran for **~18 minutes across three
worker iterations**, the first two failing Critic review (empty diffs — the
Computer-Use worker could not produce verifiable evidence for "open Explorer"),
and the **third iteration hit the 630-second wall-clock cap (a TIMEOUT)**. The
timeout-on-the-last-iteration-with-an-empty-diff was bucketed into the generic
failure code **`task_error`**, whose voice phrase is the alarming and *factually
wrong* **"Der Worker ist abgebrochen."** There are **four stacked defects**, not
one: (A) a real timeout is mislabeled as a worker abort; (B) the failure
announcement is not softened/back-referenced the way `crash_recovery` already is,
so it feels like it "came from nowhere"; (C) a quick desktop action was force-
spawned into the heavyweight background-mission machinery without the user
perceiving a spawn; (D) the Computer-Use worker has no verifiable-evidence
channel, so a diff-less desktop action is doomed to burn three iterations and
time out. **The user's literal complaint maps to A + C.**

---

## 2. The symptom (what the user heard)

```
Die Mission ist fehlgeschlagen. Der Worker ist abgebrochen.
(EN: The mission failed. The worker aborted.)
```

Spoken once, at `priority="normal"` (queued to the next turn boundary, not a
barge-in), in German, synthesized by Gemini Flash TTS and actually played
(`AudioOutFirst` published).

The German string has **exactly one source** in the codebase:

- `jarvis/missions/voice/readback.py:181` — `FAILURE_REASON_PHRASES["de"]["task_error"] = "Der Worker ist abgebrochen."`
- The `"Die Mission ist fehlgeschlagen. …"` wrapper is `jarvis/missions/voice/announcer.py:202`.

So the phrase is a deterministic function of **one failure reason code:
`task_error`**, emitted on a **voice-triggered** (`source_actor == "hauptjarvis"`)
mission.

---

## 3. Forensic ground truth (the "last transcription")

Source: live event store `data/missions.db` (read via Python `sqlite3`, WAL
included) + live log `data/jarvis_desktop.log`. Both timestamp-consistent.

| Field | Value |
|---|---|
| `mission_id` | `019ea1da-4552-7ec4-8a88-ffba1773521e` |
| Dispatched | 2026-06-07 **13:31:36.914** by `source_actor = hauptjarvis` (voice), `language=de` |
| Originating utterance | ContinuationBuffer join of two fragments: **"Kannst du für mich bitte meinen… Man nen Explorer öffnen mit Computer-User."** |
| Spawn trigger | `Force-Spawn OpenClaw` (log line 30558) — action verb *"öffnen"* + "Computer-User" marker |
| Worker iterations | **3 real subprocesses** (`ClaudeDirectWorker`, `model=claude-opus-4-8`) |
| iter0 | exit=0, wall=30.3 s, `tool_use_seen=True` → Critic **revise** (used raw `explorer.exe` shell / prose self-report, **empty diff**) |
| iter1 | exit=0, wall=377.6 s, `tool_use_seen=True` → Critic **revise** (**empty diff**, only prose self-report, no tool-call evidence) |
| iter2 | exit=1, wall=**630.7 s**, `tool_use_seen=False` → **`WorkerKilled reason=timeout`** (630 s wall-clock cap) |
| Terminal | `MissionStateChanged CRITIQUING → FAILED` **reason=`task_error`** @ 13:49:52.348 |
| Spoken | 13:49:52.349 — `📢 Announcement: 'Die Mission ist fehlgeschlagen. Der Worker ist abgebrochen.'` |

**Boot context:** the app booted at 12:53:52 (single `MissionAnnouncer:
bus-subscribe registered`); `MissionVoiceListener` was explicitly **disabled**
("Phase-6 voice listener disabled (no tts_speak_fn provided)"). The whole
dispatch → 3 workers → kill → fail → speak chain happened **live in one process**
between 13:31 and 13:49.

### Ruled out by the runtime evidence
- **NOT a recovery sweep / stale prior-session mission** — same session; no
  `startup_recover` / `crash_recovery` around the incident.
- **NOT a phantom fast-fail (no OAuth/401/403/binary-missing crash)** — the
  worktree was created, three real workers ran with tool use for ~18 minutes.
- **NOT a double-announce** — only the announcer path was active; spoken once.

---

## 4. Symptom → source chain (verified `file:line`)

```
"Der Worker ist abgebrochen."
        │  readback.py:181  FAILURE_REASON_PHRASES["de"]["task_error"]
        ▼
MissionAnnouncer._render(MissionFailed, reason="task_error")
        │  announcer.py:169-205   (no suppression for task_error; crash_recovery IS suppressed at :189)
        ▼  announcer.py:144  publish AnnouncementRequested(priority="normal")
SpeechPipeline._on_announcement → scrub_for_voice → TTS
        ▲
        │  the MissionFailed(reason="task_error") came from:
_fail_mission(mission_id, "task_error")            orchestrator.py:624-627  (generic else-clause)
        ▲
task_outcomes == [TaskOutcome.ERROR]               orchestrator.py:587-628  (aggregation)
        ▲
return TaskOutcome.ERROR                            orchestrator.py:918-924  (else branch)
        ▲
iter2: worker_error contains "timeout"             orchestrator.py:858-860  (is_timeout = True)
   AND diff is empty (_real_diff_is_empty)         → :889 grade-with-critic branch SKIPPED
   AND iteration == MAX_CRITIC_LOOPS-1 (last)       → :911 retry branch SKIPPED
        ▲
WorkerKilled(reason="timeout")                      orchestrator.py:919-923
        ▲
iter2 hit 630 s wall-clock cap                      ClaudeDirectWorker subprocess wait_for timeout
```

### The load-bearing code (orchestrator.py:858–924, paraphrased)

```python
if spawn_result.worker_error:
    is_timeout = "timeout" in err_lower
    ...
    if is_timeout and not _real_diff_is_empty(diff_text):
        ...  # grade partial work with critic  — NOT taken (diff empty)
    elif is_timeout and iteration < MAX_CRITIC_LOOPS - 1:
        continue  # retry on fresh spawn       — NOT taken (last iteration)
    else:
        await self._publish_worker_killed(reason=kill_reason)  # reason="timeout"
        return TaskOutcome.ERROR                                # <-- collapses to task_error
```

And the aggregation (orchestrator.py:587–627): `TaskOutcome.ERROR` matches none
of `BUDGET_EXCEEDED / CRITIC_UNAVAILABLE / REJECTED / EXHAUSTED / SETUP_FAILED`,
so it falls through to the generic `else:` → `_fail_mission(..., "task_error")`.

> **Key code-level finding:** the kill reason `"timeout"` is *known* at line
> 919 (it is even written into the `WorkerKilled` event), but it is **discarded**
> when the outcome is collapsed to the reason-less `TaskOutcome.ERROR`. The voice
> layer therefore never learns it was a timeout and speaks the generic
> "worker aborted" phrase.

---

## 5. Root cause — four stacked layers

The single sentence the user heard is the visible tip of a four-layer stack.
Each layer is independently fixable; the user's complaint is satisfied by fixing
**A + C**, but **D** is what made the failure happen at all.

### Layer A — Reason-code dishonesty (the wrong *word*)
A worker **timeout** on the final iteration with an empty diff is bucketed into
`task_error` → *"Der Worker ist abgebrochen."* There is already an honest,
non-alarming `timeout` template (`readback.py:79-87` / `render_timeout`,
"Die Aufgabe ist in einen Timeout gelaufen") and a `MissionTimedOut` event type
— but a *per-iteration worker* timeout that lands on the last critic loop never
reaches them. The reason code is a lie: nothing "aborted"; the task ran out of
time after three honest attempts.
**Files:** `orchestrator.py:858-924` (collapse point), `orchestrator.py:624-627`
(aggregation), `readback.py:177-202`, `announcer.py:177-205`.

### Layer B — Announcement-gate not generalized (the "from nowhere" *feel*)
The 2026-05-29 hardening softened/suppressed exactly **one** reason —
`crash_recovery` — in both voice paths (`announcer.py:189-190`,
`readback.py:297-298`) because a boot-time sweep barging in with
"Mission fehlgeschlagen" felt random. **`task_error` got no such treatment.**
Combined with the silent spawn-ACK (AD-OE5/OE6), a background failure 18 minutes
after a one-line voice command lands as an unexplained intrusion. The principle
the crash_recovery fix encoded ("don't let a background failure feel random")
was never generalized.
**Files:** `announcer.py:189-205`, `readback.py:292-310`.

### Layer C — Spawn-perception gap (the user's literal complaint)
The force-spawn heuristic (`BrainManager._should_force_openclaw` /
`_should_force_spawn`, `manager.py:1736+`) deterministically turned a casual
*"open Explorer with Computer-Use"* into a heavyweight background Worker-Critic
mission. The user received a brief optimistic ACK
("Mach ich, ich kümmere mich im Hintergrund darum…", log 30575) but does **not**
mentally model that as *"I spawned a sub-agent."* So when it fails much later,
the announcement references a "Worker" the user never knowingly created.
**This is why the user says "I never spawned a sub-agent."** They are right at
the level of intent; the system spawned one on their behalf and never made that
legible.
**Files:** `manager.py:1736-1845, 2085-2086, 2494`, `jarvis/.../spawn_worker.py:479,549-565`.

### Layer D — Computer-Use has no verifiable-evidence channel (the *enabler*)
"Open Explorer" produces **nothing to git-diff**. The worker (correctly) opened
Explorer but had no way to prove it through the Critic's GROUND-TRUTH rule
(empty diff ⇒ revise). So iter0 and iter1 were rejected for empty diffs, and
iter2 escalated to opus and timed out. Without this gap, the mission would have
completed in 30 s instead of failing after 18 min. This is the same family as
the 2026-06-07 memory entry *"Computer-Use 'das dauert zu lange' = Voice-Stall-
Guard köpft arbeitenden CU-Loop bei 30 s"* — Computer-Use work is opaque to the
diff/stall machinery that assumes file output.
**Files:** Critic GROUND-TRUTH rule in `jarvis/missions/critic/*`,
`orchestrator.py:811-834` (diff capture + augmentation), Computer-Use harness.

---

## 6. Reconciliation of the two investigators

> Per the parallel-agents discipline: agents can make systematic errors — spot
> check. They diverged; the runtime ground truth wins.

| Question | Code-trace agent (static) | Forensics agent (logs/DB) | Verdict |
|---|---|---|---|
| Mechanism: how does `task_error` reach voice? | Correct: `TaskOutcome.ERROR` → generic `else` → `_fail_mission("task_error")`; not suppressed in either voice path | n/a (confirmed by spoken-line log) | **Both agree; verified in §4** |
| Most likely *trigger* | **H-B: phantom force-spawn that fails FAST (OAuth/binary/auth)** | **A real force-spawn that ran 18 min and TIMED OUT on iter2** | **Forensics wins.** Code-trace's ranking was an educated guess; the real trigger is a timeout cascade, not a fast-fail |
| Recovery-sweep (H-A) | Ruled out (recovery emits `crash_recovery`, which is suppressed) | Ruled out (no sweep in session) | **Agree: ruled out** |
| Catch-all dispatch except (H-C) | Ruled out (no broad except maps to task_error) | n/a | **Ruled out** |
| Replay / double-announce (H-D) | Ruled out (in-memory bus, announcer subscribes after recovery; listener disabled) | Confirmed only announcer active | **Ruled out** |

The code-trace's value was **enumerating every `task_error` emission path** and
**proving the announcement-gate gap**; the forensics' value was **collapsing the
hypothesis space to the one true timeline**. Neither alone was sufficient.

---

## 7. Why the user's framing is correct (and important)

"I never spawned a sub-agent" is **literally false** (they said
"…Explorer öffnen mit Computer-User", which force-spawned) but **experientially
true**: the system never made the spawn legible, ran it for 18 minutes off-
transcript, mislabeled the timeout as an abort, and announced it un-softened.
Treating the complaint as "user is mistaken" would miss the bug. The bug is the
**gap between the user's intent model and the system's behavior**, made worse by
a dishonest reason code and an un-generalized announcement gate.

---

## 8. Fix plan (NOT IMPLEMENTED — options + tradeoffs)

Ordered smallest/most-contained first. Each enum change MUST follow the
five-layer anti-drift pattern (`docs/anti-drift-three-layer.md`) + a parity test.

### Fix A — Honest reason code for last-iteration timeout *(recommended first)*
- **A1 (minimal):** when the collapse at `orchestrator.py:918-924` is a timeout
  (`is_timeout`), fail the mission with `reason="timeout"` (or a new
  `attempts_timed_out`) instead of `task_error`. The honest voice phrase becomes
  "Die Aufgabe ist in einen Timeout gelaufen." (already exists).
- **A2 (cleaner):** introduce `TaskOutcome.TIMED_OUT`, carry it through the
  aggregation (`orchestrator.py:587-627`), map it to a timeout reason.
- **Tradeoff:** A2 is the correct shape but touches the outcome enum (five-layer
  + `tests/...` parity). A1 is one branch but overloads `timeout` semantics.
- **Guard:** unit test on the aggregation: `[TaskOutcome.ERROR-from-timeout]`
  ⇒ reason ∈ {timeout, attempts_timed_out}, never `task_error`.

### Fix B — Generalize the non-alarming announcement
- **B1:** soften the `task_error` phrase itself (less "abort", more "I couldn't
  finish it in time").
- **B2 (better):** back-reference the request so a background failure never feels
  random: e.g. *"Deine Anfrage, den Explorer zu öffnen, konnte ich nicht
  abschließen — Zeitlimit erreicht."* This requires threading the original
  utterance/intent (already in `MissionDispatched`) into the announcement.
- **Tradeoff:** B2 needs the announcer to read the dispatched intent; B1 is a
  one-line phrase edit. Aligns with AD-OE5/OE6 (no silent drops, turn-boundary,
  non-alarming).

### Fix C — Make the spawn legible (UX)
- **C1:** when force-spawning a *desktop/Computer-Use* action, make the ACK and
  the failure both name what is running in the background.
- **C2 (architectural):** route quick desktop actions ("open X") through a
  lighter direct-action path instead of the 3-iteration Worker-Critic mission.
  Largest lever, largest change — out of scope for a contained fix.

### Fix D — Computer-Use evidence channel *(deepest; stops the 18-min burn)*
- **D1:** give the Critic a Computer-Use-aware evidence source
  (screenshots / action-observation log) so a diff-less desktop action can be
  judged on what it actually did, not on an (always-empty) git diff. Sibling to
  the 2026-06-07 CU stall-guard fix.
- **Tradeoff:** biggest correctness win, biggest surface; likely its own plan.

### Recommended sequencing
**A → B → C1 → D.** A alone makes the spoken sentence *true*; A+B make it *kind*;
C1 makes the spawn *legible*; D makes the failure *not happen*. Do not bundle —
each is independently testable and the repo's anti-pattern register punishes
multi-fix commits.

### §8.A-IMPLEMENTED (2026-06-07)

Layer A shipped via **A2** (a dedicated `TaskOutcome`, the cleaner of the two
options) because it follows the existing `SETUP_FAILED` precedent exactly and
keeps the timeout truth flowing from the worker loop to the aggregation without
overloading the `timeout` semantics elsewhere.

| Step | Change | File:line |
|---|---|---|
| 1 | `TaskOutcome.TIMED_OUT = "timed_out"` | `orchestrator.py` (TaskOutcome class) |
| 2 | terminal worker-error branch: `return TaskOutcome.TIMED_OUT if is_timeout else TaskOutcome.ERROR` | `orchestrator.py` `_run_task_with_critic_loop` |
| 3 | aggregation: `elif TaskOutcome.TIMED_OUT → _fail_mission("attempts_timed_out")` (after SETUP_FAILED, before generic `task_error`) | `orchestrator.py` `run_mission` |
| 4 | `FAILURE_REASON_PHRASES` de "Das Zeitlimit wurde überschritten." + en "The time limit was reached." | `readback.py` |

**TDD record:** 4 tests written first, watched fail for the right reason
(`expected attempts_timed_out, got 'task_error'` + raw-token leaks), then made
green:
- `test_loop.py::test_worker_timeout_every_iteration_fails_with_timeout_reason`
  (extended to assert `MissionFailed.reason == "attempts_timed_out"`)
- `test_voice_readback.py::test_render_failed_maps_attempts_timed_out_to_honest_timeout_phrase` + `_en`
- `test_voice_announcer.py::test_failed_attempts_timed_out_speaks_honest_timeout`
- parity guard pinned: `attempts_timed_out` present in both de+en.

**Proof:** `tests/missions/` → 751 passed, 2 skipped. 0 new ruff. No TS/SQL/Pydantic
layer renders the reason (free `str`), so no five-layer UI change was required.
ADR-0009 upheld (static phrase, no LLM narrative). **Live voice requires an app
restart** (pywebview RAM bundle) to take effect.

**Scope note:** non-timeout worker crashes (auth/billing/spawn-exception) still
map to `task_error` ("Der Worker ist abgebrochen.") — correct, a real abort.
Only the wall-clock-timeout path was relabelled.

---

## 9. Regression guards to add when fixing (not yet written)
- Aggregation unit test: a timeout-killed last iteration with empty diff does
  **not** produce `reason="task_error"`.
- Voice parity test: every reason in `FAILURE_REASON_PHRASES` has de+en parity
  (extend existing) and a timeout reason renders the timeout template.
- Announcer test: a background `MissionFailed` is announced at `priority="normal"`
  with a back-reference (if Fix B2) and never the bare "Der Worker ist
  abgebrochen." for a timeout.
- (If Fix D) Critic test: a Computer-Use mission with an empty git diff but a
  valid action-observation log is gradable, not auto-revised.

---

## 10. Open questions / honest gaps
- **Why did iter2 hang the full 630 s?** No per-iteration `stderr.log` survived
  (the worktree workspace was empty and partially undeletable — "Permission
  denied" on remove). We know streaming started and `tool_use_seen=False`, but
  not what opus was doing. A separate instrumentation pass would be needed.
- **Did the user actually hear it?** TTS audio was generated and `AudioOutFirst`
  published, so playback began; not provable beyond that from logs.
- **Is there a *second*, different incident the user means?** The "last
  transcription" is unambiguously this timeout cascade. If the user has *also*
  seen this with **no utterance at all**, that would be the recovery-sweep
  vector — already suppressed for `crash_recovery`, so it should not recur; worth
  a quick confirm.

---

## 11. Appendix — primary sources
- Phrase origin: `jarvis/missions/voice/readback.py:177-202`
- Voice paths: `jarvis/missions/voice/announcer.py:115-232`,
  `jarvis/missions/voice/listener.py:78-185`
- Collapse point: `jarvis/missions/kontrollierer/orchestrator.py:858-924`
- Aggregation: `jarvis/missions/kontrollierer/orchestrator.py:587-628`
- Recovery (ruled out): `jarvis/missions/recovery.py:166-171`
- Force-spawn: `jarvis/brain/manager.py` (`_should_force_openclaw` / `_should_force_spawn`)
- Bootstrap wiring: `jarvis/missions/init.py` (listener/announcer mode resolve)
- Runtime evidence: `data/missions.db` (mission `019ea1da…`, event seqs
  3734/3738/3745/3752/3753/3754/3755), `data/jarvis_desktop.log` (lines
  ~30476–31220, 2026-06-07 13:31:26 → 13:49:54)
- Related memory: *Computer-Use "das dauert zu lange" = Voice-Stall-Guard köpft
  arbeitenden CU-Loop bei 30 s* (2026-06-07); *Outputs ~88% FAILED = vier
  Buckets* (task_error timeout bucket); *"took too long, sag nochmal"*.
