# Subagent / Mission Report Readback — Conversation Continuity, Speaking Indicator, Transcript Attribution — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make a background mission's spoken report a graceful part of the conversation — keep listening when the user is present, animate the mascot/orb while the report plays, and give the report a distinct, attributed transcript entry ("Jarvis Sub-Agent / Output").

**Architecture:** Surgical changes on the existing voice pipeline (`jarvis/speech/pipeline.py`), the Supervisor-state-driven UI animation, the sessions transcript enum + frontend, and the readback emit sites. No new subsystems. Respects the voice critical-path invariants (no LLM/blocking on the hot path; per-unit watchdog reset; AD-OE6 zero-silent-drop).

**Tech Stack:** Python 3.11 (asyncio), pytest (fakes, asyncio_mode=auto), React/TypeScript + vitest frontend.

**Spec:** `docs/superpowers/specs/2026-06-19-subagent-report-readback-conversation-continuity-design.md`

**Working tree note:** Execute in the MAIN working tree, NOT a fresh worktree — the live app's editable install imports from this tree, and a worktree would not be loaded on restart (BUG-006/014/015 restore-trap). Commit hunk-isolated (contested shared tree). Test python: `C:\Program Files\Python311\python.exe`.

---

## Current reality (verified 2026-06-19, before any change)

- **Part 1 (keep listening) — mostly already built.** `_active_session` (`pipeline.py:3767`) keeps the session open while `_background_mission_in_flight()` is true (`:3825`) and for one full idle window after any spoken announcement via `_post_readback_grace_s` (`:3843-3854`), because `_on_announcement` stamps `_last_announcement_spoken_monotonic` before playback (`:2386`). The only gap: the OpenClaw-background DIRECT path (`_on_background_completed`, plays at `:2657-2668`) does NOT stamp that timestamp, so a readback delivered through it does not re-arm the grace window. The user's "hangs up after report" is most likely the pre-restart state; this plan closes the one real gap and verifies live.
- **Part 2 (mascot speaking) — confirmed gap.** `_on_announcement` (`:2238`) never calls `_transition("SPEAKING")`, so no `SystemStateChanged` is published and the avatar/orb stay in their previous state during the readback. The normal path animates via `_set_turn_state` → `_transition` (`:1609/1599`). Supervisor `set_state` is idempotent (`jarvis/state/supervisor.py:47`).
- **Part 3 (transcript) — afterglow already records it; needs a distinct kind.** `recorder.py` afterglow attaches a post-hangup `spoken_kind==completion` readback to the originating session (`:650-671`). BUT `completion` is overloaded — it also tags a normal buffered inline reply (`pipeline.py:5552`) and a CU readback (`computer_use_tool.py:233`), so relabeling `completion` would mislabel normal answers. → introduce a dedicated `subagent` kind.
- **Part 4 (mid-sentence cut) — already fixed + committed** (`e60f6fd4`, `stream_evidence.py::summarize_answers` boundary truncation). Verify only.

`spoken_kind` is a five-layer enum (`docs/anti-drift-three-layer.md`): `jarvis/sessions/constants.py` → `models.py` (`KNOWN_SPOKEN_KINDS = frozenset(SPOKEN_KINDS)`) → `schema.sql` (untyped JSON payload) → `frontend/.../sessions/types.ts` → `frontend/.../sessions/TurnCard.tsx` (`SPOKEN_KIND_LABEL`). Parity test: `tests/unit/sessions/test_spoken_kind_parity.py`.

`AnnouncementRequested.kind` is a separate Literal `["preamble","completion","info","progress"]` (`jarvis/core/events.py:410`); the pipeline keys the hangup punch-through + heartbeat-cancel on `kind=="completion"` (`pipeline.py:2264/2274`) and maps it to a spoken_kind via `_announcement_spoken_kind` (`pipeline.py:220`).

---

## Task 1 — Part 3a: add the `subagent` spoken-kind through all five layers

**Files:**
- Modify: `jarvis/sessions/constants.py` (add constant + tuple entry + `__all__`)
- Modify: `jarvis/ui/web/frontend/src/components/sessions/types.ts` (`KNOWN_SPOKEN_KINDS`)
- Modify: `jarvis/ui/web/frontend/src/components/sessions/TurnCard.tsx` (`SPOKEN_KIND_LABEL`)
- Test: `tests/unit/sessions/test_spoken_kind_parity.py` (already enforces parity — extend its expected set if it hardcodes one)

- [ ] **Step 1: Run the parity test to see the current green baseline**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/sessions/test_spoken_kind_parity.py -v`
Expected: PASS (baseline before adding the new kind).

- [ ] **Step 2: Add the Python constant**

In `jarvis/sessions/constants.py`, after `SPOKEN_KIND_COMPLETION` (`:104`):
```python
SPOKEN_KIND_SUBAGENT: Final[str] = "subagent"
```
Append `SPOKEN_KIND_SUBAGENT` to the `SPOKEN_KINDS` tuple (after `SPOKEN_KIND_COMPLETION`) and to `__all__`.

- [ ] **Step 3: Mirror in the two TS layers**

In `types.ts` `KNOWN_SPOKEN_KINDS`, add `"subagent",` (after `"completion"`).
In `TurnCard.tsx` `SPOKEN_KIND_LABEL`, add `subagent: "Jarvis Sub-Agent / Output",`.

- [ ] **Step 4: Run parity test — make it pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/sessions/test_spoken_kind_parity.py -v`
Expected: PASS. If the test hardcodes an expected list, add `"subagent"` there. `KNOWN_SPOKEN_KINDS` in `models.py` is `frozenset(SPOKEN_KINDS)` so it updates automatically.

- [ ] **Step 5: Commit** (`git add` the 4 files only)

---

## Task 2 — Part 3b: route the readback into the `subagent` kind (and keep every guard intact)

**Files:**
- Modify: `jarvis/speech/pipeline.py` — `_announcement_spoken_kind` (`:220`), punch-through + heartbeat guards (`:2264/2274`), OpenClaw-bg direct emit (`:2657`) + deferred (`:2646`)
- Modify: `jarvis/missions/voice/announcer.py:153` (mission readback)
- Modify: `jarvis/brain/manager.py:3467` (worker readback)
- Modify: `jarvis/tasks/runner.py:400` (scheduled task readback)
- Modify: `jarvis/sessions/recorder.py:662` (afterglow accepts the new kind)
- Test: `tests/unit/speech/` (punch-through), `tests/unit/sessions/test_recorder_posthangup_readback.py`

- [ ] **Step 1: Write the failing tests FIRST**
  - A `kind="subagent"` `AnnouncementRequested` still punches through the hangup gate (`_on_announcement` does not early-return when `_hangup_event` is set). This directly guards AD-OE6 zero-silent-drop against the kind change.
  - `_announcement_spoken_kind("subagent") == SPOKEN_KIND_SUBAGENT`.
  - The afterglow records a post-hangup `spoken_kind=="subagent"` readback to the just-ended session (extend `test_recorder_posthangup_readback.py`).

- [ ] **Step 2: Run them — verify they fail**

- [ ] **Step 3: Generalize the readback guards (no literal drift)**

In `pipeline.py`, add a module-level set near the other constants:
```python
# AnnouncementRequested.kind values that deliver an answer the user is owed
# (a finished background mission / sub-agent / worker / CU result). These punch
# through the hangup gate (AD-OE5/OE6 zero-silent-drop) and cancel pending
# "still on it" heartbeats. "subagent" is the attributed sibling of "completion".
_READBACK_KINDS: frozenset[str] = frozenset({"completion", "subagent"})
```
Replace `is_completion = getattr(event, "kind", None) == "completion"` (`:2264`) with
`is_readback = getattr(event, "kind", None) in _READBACK_KINDS` and use `is_readback` at `:2265` and `:2274`.
In `_announcement_spoken_kind` (`:228`), add `SPOKEN_KIND_SUBAGENT` to the pass-through tuple so `"subagent"` maps to itself (import it at `:85`).

- [ ] **Step 4: Point the real sub-agent emit sites at the new kind**
  - `announcer.py:153`: `kind="completion"` → `kind="subagent"`.
  - `manager.py:3467`: `kind="completion"` → `kind="subagent"`.
  - `tasks/runner.py:400`: `kind="completion"` → `kind="subagent"`.
  - `pipeline.py:2646` (deferred OpenClaw-bg `AnnouncementRequested`): `kind="completion"` → `kind="subagent"`.
  - `pipeline.py:2657` (direct OpenClaw-bg emit): `SPOKEN_KIND_COMPLETION` → `SPOKEN_KIND_SUBAGENT`.
  - LEAVE unchanged: `pipeline.py:5552` (normal buffered inline reply) and `computer_use_tool.py:233` (foreground CU action readback) — these are NOT sub-agent/mission output.
  - Add `AnnouncementRequested.kind` Literal value `"subagent"` in `jarvis/core/events.py:410`.

- [ ] **Step 5: Widen the afterglow guard**

In `recorder.py:662`, change the guard to accept both kinds:
```python
if getattr(event, "spoken_kind", "") not in (SPOKEN_KIND_COMPLETION, SPOKEN_KIND_SUBAGENT):
    return
```
Import `SPOKEN_KIND_SUBAGENT` alongside `SPOKEN_KIND_COMPLETION` (`:64`).

- [ ] **Step 6: Run the new + existing speech/sessions tests — all green**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/speech/ tests/unit/sessions/ -q`

- [ ] **Step 7: Commit** (hunk-isolated; the brain/tasks files only the single-line kind change)

---

## Task 3 — Part 2: animate the mascot/orb during the readback

**Files:**
- Modify: `jarvis/speech/pipeline.py` — `_on_announcement` playback block (`:2398-2410`)
- Test: `tests/unit/speech/` (new test: announcement publishes `SystemStateChanged(SPEAKING)` then restores)

- [ ] **Step 1: Write the failing test**

With a fake supervisor/bus, drive `_on_announcement(AnnouncementRequested(kind="subagent", ...))` and assert a `SystemStateChanged` to `SPEAKING` is published before playback and the state is restored afterward (to the entry state, or IDLE when `_hangup_event` is set).

- [ ] **Step 2: Run it — verify it fails** (no SPEAKING today).

- [ ] **Step 3: Wrap the playback in a SPEAKING transition**

Right where the code commits to speaking (after the empty-scrub guard, around `:2386` where `_last_announcement_spoken_monotonic` is set), capture the prior UI state and transition to SPEAKING; restore in a `finally` around the existing playback try/except:
```python
prev_state = self._supervisor.state if self._supervisor is not None else None
await self._transition("SPEAKING")
try:
    ... existing synthesize + play_chunks ...
finally:
    hungup = (hangup is not None and hangup.is_set())
    await self._transition("IDLE" if hungup else (prev_state or "LISTENING"))
```
Defensive: `_transition` already swallows supervisor errors; never let a restore failure crash the handler.

- [ ] **Step 4: Run the test — pass.** Also run the full speech bucket for regressions.

- [ ] **Step 5: Commit.**

---

## Task 4 — Part 1: close the OpenClaw-background grace gap

**Files:**
- Modify: `jarvis/speech/pipeline.py` — `_on_background_completed` direct-play path (`:2657`, before `play_chunks` at `:2668`)
- Test: `tests/unit/speech/` (the direct background readback stamps `_last_announcement_spoken_monotonic`)

- [ ] **Step 1: Write the failing test** — after `_on_background_completed` plays a readback, `_last_announcement_spoken_monotonic` is set (not None), so `_active_session`'s grace branch will keep the session open.

- [ ] **Step 2: Run it — verify it fails.**

- [ ] **Step 3: Stamp the grace timestamp** in `_on_background_completed`, right before the player call (mirror `_on_announcement:2386`):
```python
self._last_announcement_spoken_monotonic = time.monotonic()
```

- [ ] **Step 4: Run the test — pass.**

- [ ] **Step 5: Commit.**

---

## Task 5 — Frontend: distinct colour for the sub-agent readback block

**Files:**
- Modify: `jarvis/ui/web/frontend/src/components/sessions/TurnCard.tsx` (`:209-248` spoken-output block)
- Test: `jarvis/ui/web/frontend` vitest (a `spoken_kind="subagent"` line renders the "Jarvis Sub-Agent / Output" label and the distinct colour class)

- [ ] **Step 1: Write the failing vitest** asserting a rendered `subagent` line carries the label and a colour class distinct from the sky-blue `completion` block.

- [ ] **Step 2: Run it — fail.** (`npm run test` in `jarvis/ui/web/frontend`)

- [ ] **Step 3: Style the subagent line.** In the spoken-line map, pick the per-line colour by `spoken_kind`: keep sky for the others, use a clearly different hue (violet/purple — reads as "agent") for `subagent`. E.g. a small `const isSubagent = s.spoken_kind === "subagent"` and conditional border/bg classes (`border-violet-400/30 bg-violet-400/10` vs the existing sky classes), plus the badge label from `SPOKEN_KIND_LABEL`.

- [ ] **Step 4: Run vitest — pass. Then `npm run build`** to confirm the production bundle compiles.

- [ ] **Step 5: Commit.**

---

## Task 6 — Verify live (Parts 1 & 4) + full regression

- [ ] **Step 1: Confirm the live import points at this tree.**
Run: `& "C:\Program Files\Python311\python.exe" -c "import jarvis, pathlib; print(pathlib.Path(jarvis.__file__))"` — must be under `C:\Users\Administrator\Desktop\Personal Jarvis`.

- [ ] **Step 2: Run the affected buckets.**
Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/speech/ tests/unit/sessions/ tests/missions/ -q` and `ruff check` on the touched files.

- [ ] **Step 3: Restart the app** via `POST /api/settings/restart-app` so the editable-install code goes live.

- [ ] **Step 4: Live-verify** a voice (or `/ws` headless) sub-agent mission: the report plays in full (no mid-word cut — Part 4), the avatar animates "speaking" during it (Part 2), the session keeps listening afterward when present (Part 1), and the transcript shows the report attributed to the start session with the "Jarvis Sub-Agent / Output" label in its own colour (Part 3). Headless `/ws` harness: see `reference_headless_voice_qa_via_ws`.

- [ ] **Step 5: Code review** via the `code-reviewer` agent against the CLAUDE.md anti-patterns (AP-9/11/19, AD-OE5/OE6, five-layer parity, shared-tree discipline). Fix findings.

---

## Self-review (plan vs. spec)

- Spec Part A (mid-sentence) → Task 6 verify (already fixed). ✓
- Spec Part B (keep listening, present-only) → Task 4 (close OpenClaw-bg gap) + existing grace logic; hung-up case unchanged by design. ✓
- Spec Part C (transcript attribution + distinct colour/label) → Tasks 1, 2, 5; afterglow attribution already live, widened to the new kind in Task 2 Step 5. ✓
- Spec Part D (speaking indicator) → Task 3. ✓
- Invariants: punch-through preserved via `_READBACK_KINDS` + explicit test (Task 2 Step 1); no LLM/blocking added; no watchdog counter introduced (Part 1 reuses the existing bounded grace); language doctrine unaffected (no new spoken phrase). ✓
- No placeholders; emit-site list explicit; `subagent` name consistent across all layers and tasks. ✓
