# Computer-Use `fail`-gate: symmetric completion enforcement

- **Date:** 2026-06-15
- **Status:** Approved (design), implementation in progress
- **Scope:** `jarvis/harness/screenshot_only_loop.py` only (Computer-Use loop)
- **Branch:** `fix/cu-fail-gate-completion-enforcement-20260615`

## Problem (evidence-grounded)

A live Computer-Use turn was recorded giving up mid-task. Reconstructed from
`data/sessions.db` and `data/jarvis_desktop.log` (2026-06-15 18:58):

```
User:    "Could you please open my sniping tool and do a screenshot?"
Tier:    fast  -> gemini/gemini-3.5-flash
Jarvis:  "I have initiated the process to open the Snipping Tool..."   (optimistic ACK)
18:58:52 step 4 click_element 'Snipping Tool'  -> FAILED (no matching element)
18:58:54 step 5 click 'Snipping Tool icon'
18:58:58 step 7 click_element 'Neu'            -> FAILED; available labels: ['Snipping Tool Ueberlagerung'...]
18:59:04 "Das am Bildschirm hat nicht geklappt: exit 5"               (_FAIL_EXIT_CODE)
```

Exit code 5 is `_FAIL_EXIT_CODE` — the model voluntarily emitted
`{"action":"fail"}`. At step 7 the on-screen labels already included the
Snipping capture overlay (`Snipping Tool Ueberlagerung`): the goal was nearly
achieved and the model quit one action short.

This was **not** a sub-agent mission (no mission row created after 17:02). It
was the Computer-Use loop in `jarvis/harness/screenshot_only_loop.py`.

## Root cause

The loop's two terminal actions have wildly asymmetric cost:

| Action | What the loop requires | Code (pre-fix) |
|---|---|---|
| `done` (success) | Must pass a **strict completion judge** (`_verify_goal_done`) against a fresh screenshot; on rejection the loop re-plans with a "Keep working" note, up to `_MAX_DONE_REJECTS = 3` times | `screenshot_only_loop.py:2694-2765` |
| `fail` (give up) | **Nothing.** Emit `{"action":"fail","reason":"..."}` -> instant `exit_code=5` -> `success=False` | `screenshot_only_loop.py:2767-2776` |

Quitting is free; succeeding is expensive. A weak/fast model under friction
minimizes effort by taking the free exit — even when the goal is still
achievable. This is an agentic **reward-hack via an asymmetric terminal
condition** (the RL "give-up-to-stop-the-pain" pathology, in an LLM tool-loop).

The system prompt already forbids lazy `fail` ("Returning 'fail' without trying
anything is FORBIDDEN", `screenshot_only_loop.py:237-240`), but the model
satisfied the *letter* (it tried two actions) while violating the *spirit* (gave
up while achievable). A prompt nudge alone is therefore insufficient; a
structural gate is required.

## Principle

Make quitting cost exactly what succeeding costs. After the fix there is no
cheap terminal state: a `fail` must survive a strict feasibility judge that
agrees the goal is genuinely impossible/blocked *from the current screen*,
mirroring the existing `done`-gate. The only low-effort path to ending the loop
becomes actually completing the task.

A numeric "reward score" is explicitly rejected: a number the model optimizes
invites Goodhart's-law gaming. Symmetry of terminal conditions is the mechanism.

## Components (all in `jarvis/harness/screenshot_only_loop.py`)

### 1. `_FAIL_VERIFIER_SYSTEM_PROMPT`
A strict feasibility judge prompt, sibling of `_GENERIC_VERIFIER_SYSTEM_PROMPT`.
Output exactly one JSON object: `{"give_up": true|false, "reason": "<on-screen
evidence>"}`.
- `give_up:true` ONLY if the screenshot *proves* the goal cannot be reached from
  here (hard error dialog, missing capability, permission wall, a required
  element absent from anywhere reachable).
- The agent's stated reason is a CLAIM, not proof. Trust the screenshot.
- "hard" / "unclear" / "tried a couple times" is NOT impossible -> `give_up:false`.
- **When in ANY doubt, `give_up:false`.** The default is KEEP WORKING.

### 2. `_verify_fail_justified(ctx, *, observation, user_goal, claimed_reason) -> tuple[bool, str]`
Near-exact mirror of `_verify_goal_done`. Calls `_call_brain` with the fail
prompt, parses with the generalized `_parse_verdict`. **Never raises**: on any
error/timeout returns `(False, "")` = keep working. This is the core
anti-reward-hack property — a judge failure can never grant a free quit.

### 3. Generalized `_parse_verdict(raw, *, bool_key="done", text_key="proof")`
Backward-compatible: defaults preserve the existing `{"done","proof"}` contract
(all 76 current tests stay green). The fail-judge calls it with
`bool_key="give_up", text_key="reason"`.

### 4. Gated `fail` handler (replaces lines 2767-2776)
Structurally identical to the `done`-gate:
- New constant `_MAX_FAIL_REJECTS = 2`, counter `fail_rejects = 0` (beside
  `_MAX_DONE_REJECTS`/`done_rejects`).
- On `fail`, when `verify_done_enabled` is on, re-observe if the batch changed
  state, then run `_verify_fail_justified`.
  - `give_up:false` and `fail_rejects < _MAX_FAIL_REJECTS` -> increment, append a
    `FAIL REJECTED: the goal still looks achievable ... Do NOT give up. Pick the
    next concrete action...` note to `history`, and `break` to re-plan from a
    fresh screenshot (exactly like `done_rejects`).
  - `give_up:true` -> honor the fail (exit 5) with the judge's *verified* reason.
  - `fail_rejects >= _MAX_FAIL_REJECTS` -> honor the fail anyway (backstop):
    "verified-impossible after N attempts". Guarantees termination.
- When `verify_done_enabled` is off, `fail` is honored at face value (unchanged
  behavior / escape hatch), consistent with the `done`-gate's own flag check.

### 5. Prompt nudge (secondary)
Append one line to the existing `fail` bullet: a `fail` is now VERIFIED against
the screen and will be REJECTED if the goal still looks achievable — aligning the
model's expectation with the enforcement and reducing wasted judge calls.

## Termination / no-infinite-loop argument

Every rejected `fail` consumes one re-plan (an outer step), bounded by both
`_MAX_FAIL_REJECTS` and the existing `max_steps` budget. A genuinely impossible
task terminates via `give_up:true` or the `_MAX_FAIL_REJECTS` backstop. A
judge that is permanently down rejects every fail (returns `False`) only until
the backstop, then honors it — no infinite loop, and still no free early quit.

## Constraints honored

- **No happy-path latency:** the fail-judge fires *only when the model emits
  `fail`*. A successful task never emits `fail` -> zero added latency on the
  happy path and on every normal action step. Locked by test 6 below.
- **No broken features:** the impossible-task path still terminates (now with an
  honest, verified reason); same `verify_after_each_step` on/off switch as the
  done-gate; `_parse_verdict` change is backward-compatible.
- **Cross-platform:** pure control-flow + a prompt string, no new dependency, no
  OS-specific code. Runs identically on the headless VPS path.

## Tests (TDD, written first) — `tests/unit/harness/test_cu_fail_gate.py`

1. `fail` on an achievable goal (judge `give_up:false`) -> NOT terminated; loop
   re-plans; `FAIL REJECTED` note appended.
2. `fail` rejected `_MAX_FAIL_REJECTS` times -> honored (backstop terminates,
   exit 5).
3. `fail` on a proven-impossible goal (judge `give_up:true`) -> honored
   immediately, exit 5, verified reason surfaced.
4. Judge error/timeout -> treated as keep-working, NOT a free quit (core
   anti-reward-hack regression guard).
5. `verify_after_each_step=False` -> `fail` honored at face value
   (backward-compat).
6. Happy-path `done` task -> fail-judge never invoked (latency guard).

## Out of scope (YAGNI)

- The sub-agent Worker-Critic mission loop (`jarvis/missions/`) — its give-up
  paths differ; deferred per scoping decision.
- Model-tier change for the CU planner/judge — adds latency/cost, violating the
  no-slowdown constraint; the structural gate is model-agnostic. Possible
  follow-up, not part of this fix.
- Any numeric reward score (Goodhart risk).
