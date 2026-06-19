# Sub-Agents "all messages failing" — root-cause review & completion plan

**Date:** 2026-06-16
**Status:** root cause confirmed; fix already present in the working tree (parallel
session), verified green by tests; live deployment + two confirmatory checks remain.
**Reporter symptom:** "In the Sub-Agents section, all sub-agent messages are failing."

---

## 1. What is actually happening (evidence, not assumption)

Queried `data/missions.db` directly. Findings:

- The last **APPROVED** mission was **2026-06-15 16:45**. Every mission after it
  is **FAILED** or **CANCELLED** (0 approvals since).
- Every recent failure dies at **iteration 0–2** with
  `MissionFailed reason=critic_loop_exhausted`. The worker runs the full
  Worker→Critic loop 3× and each pass produces `diff: ''` (empty),
  `tokens_used: 0`, `cost_usd: 0.0`. The Critic's **deterministic empty-diff
  veto** then rejects all three iterations.
- The failing "missions" are **not work tasks**. Their prompts are
  **drag-drop recap requests**, e.g.:
  - `📎 Let's talk about the sub-agent task "Write a 200-word origin story…" — give me a short recap`
  - `In one short sentence: what was that sub-agent task I just dropped in about?`
  - `📎 I've pinned a finished task… no new work is needed — just give me a short, friendly recap.`
- Dispatch source split:
  - `019ecc70/74/78` → `source_actor=hauptjarvis` (the **brain force-spawned** the
    recap as a worker mission).
  - `019ed04e/051` → `source_actor=ui`, `parent_mission_id` set (the **"Restart"
    button re-ran** a failed recap-mission, re-dispatching the doomed prompt).
- A genuine **comparison control**: the last working mission (16:45, "write a USA
  migration research report") had the **same** `cli: codex, model: '', pid: 0,
  tokens: 0` — and produced a real `.md` diff and was APPROVED. So **the worker
  and codex are fine**; the differentiator is the *prompt*, not the model.

## 2. Root cause (the "doom loop")

Dragging a mission/output card onto the in-app dock is meant to produce a
**conversational recap** (`mission.inject` → `MessageSent(role=user)` → brain
answers in chat — no worker). Two independent defects turned that into a
self-perpetuating failure factory:

1. **Router force-spawn fired on recap text.** The recap directive embeds the
   dropped card's own title verbatim. When that title (or the user's follow-up)
   contains a force-spawn trigger word — **"sub-agent"** — or an action verb, the
   router (`BrainManager._should_force_spawn`) force-spawned a *new* worker
   mission instead of answering inline. The 2026-06-15 "when I say subagent it
   MUST spawn" mandate hoisted the trigger check to the top, so any mention of
   "sub-agent" spawned — including recaps and questions *about* a sub-agent.

2. **The new mission has no possible deliverable.** Its prompt literally says
   "no new work is needed, just give me a recap." The worker correctly writes
   nothing → empty diff → the Critic's deterministic empty-diff veto → 3 revise
   loops → `critic_loop_exhausted` → FAILED.

3. **The failure is recursive.** The failed card shows up in the Sub-Agents view;
   dragging it again (or hitting "Restart") re-injects/re-dispatches a directive
   that again contains "sub-agent" → another failed mission. Hence "*all*
   sub-agent messages are failing."

> The user's "I fixed it with Fable 5, now it's banned" intuition is a red
> herring for this failure: the worker **model** never caused the doom loop. The
> model only affects whether a *genuine* task produces good output.

## 3. The fix (already in the working tree — parallel session)

Two complementary layers, both verified by tests:

- **Router exemption (prevent the mission):**
  `jarvis/ui/web/mission_inject.py` defines
  `MISSION_INJECT_SOURCE_LAYER = "ui.web.ws.mission_inject"`; the server stamps it
  on the injected `MessageSent`; `desktop_app.py` and `launcher.py` thread
  `source_layer=evt.source_layer` into `generate()`; `BrainManager`
  (`_NON_SPAWN_SOURCE_LAYERS`, checked first in `_should_force_spawn`) returns
  `False` for that source → a recap is discussed inline, never re-dispatched,
  whatever its text contains.
- **Critic safety net (rescue a no-file answer):**
  `jarvis/missions/critic/runner.py` — when the diff is empty but
  `is_informational_request(prompt)` is true and the worker produced an answer
  (`readonly_answer`), the Critic now **approves** ("the spoken answer is the
  deliverable") instead of the deterministic empty-diff revise. The
  anti-hallucination veto stays intact for code/artefact tasks (keys off request
  shape, never the worker's claim). This also covers the **rerun** path and
  genuine **"research X for me"** informational tasks.

**Test status:** `tests/unit/brain/test_routing.py` + `test_mission_inject.py` +
`test_ws_mission_inject.py` → **210 passed**. Critic informational/empty-diff
subset → **7 passed**.

## 4. Why the user still sees it failing "right now"

The fix is (a) partly **uncommitted** and (b) **not live**. The running tray app
loaded its Python at startup, before these edits. The editable install updates
files, not the running process. **`POST /api/settings/restart-app` is required.**

## 4b. Team findings — there are TWO distinct root causes (cross-verified)

The 5-agent sweep proved the recent failures are **two unrelated bugs**, not one:

**Root cause #1 — the recap doom-loop** (Facets A/B; 6 of the recent failures,
all the `📎`/recap prompts). Confirmed by the Inject and Critic specialists +
my own trace. Fixed in the working tree (router exemption + critic informational
escape). Cutover specialist dated the trigger: the drag-drop-inject feature
landed **2026-06-15 19:47–20:05** (`71accabf`/`9353f10f`/`d99a119d`), right after
the last 16:45 success.

**Root cause #2 — the codex worker's Node.js runtime was missing** (Facet C; the
genuine "Melbourne→Sydney research" task, `019ecc5a`). **Disk-proven:** both
worktrees' `logs/stderr.log` read *"node … konnte nicht gefunden werden"* and the
diff patches are **0 bytes**. `codex.cmd` is a Node app; with `node` absent from
the worker subprocess's inherited PATH it exited code 1 with no stdout → empty
diff. The codex→claude fallback did **not** fire because the error text matches
neither `_CODEX_AUTH_EXPIRED_MARKERS` nor `_CODEX_USAGE_LIMIT_MARKERS`
(`codex_direct_worker.py:664-677`).
- **This is NOT covered by the critic informational escape** — the escape needs a
  prose *answer* to approve, and a node-crash produces none.
- It appears **transient**: today's missions (14:01/14:04) show codex running
  fine (it emits a planning narration), so node is back on PATH now. The robust
  hardening (out of this PR's scope, per maintainer) is to add
  "node"/"is not recognized"/exit-1-empty-output to the codex fallback markers so
  a broken codex runtime transparently falls back to the Claude worker.

**The "Fable 5" angle is a red herring** (Cutover specialist): no live path is
pinned to `claude-fable-5`. The worker is codex (`gpt-5-codex`, ChatGPT-OAuth);
the Anthropic fallback resolves `claude-opus-4-8`. The fable pin was reverted
2026-06-14; only narrative comments remain. The `_MODEL_UNAVAILABLE_MARKERS`
retry-without-model net exists precisely to survive a stale fable pin.

**Minor residual (Critic specialist):** German recap/summary verbs ("fasse
zusammen", "rekapituliere") are absent from `_INFO_TRIGGER_RE`
(`stream_evidence.py`), so a German-phrased recap-rerun wouldn't classify as
informational and would still be vetoed. Pure additive fix; low priority now that
the router exemption stops recaps becoming missions in the first place.

**Note on a stale agent read:** the Inject specialist reported the source_layer
exemption as "unfixed". That reflects an earlier snapshot — the parallel session
wrote the exemption *during* the investigation (the file changed mid-read).
Direct verification (git diff + `_should_force_spawn` line 2552 + 210 passing
tests) confirms the exemption IS present in the current working tree.

## 5. Remaining work

| # | Item | Owner | Status |
|---|------|-------|--------|
| 1 | Confirm the genuine "Melbourne→Sydney research" task (Facet C) — did codex truly execute, or no-op? Does the critic net now approve it? | Worker-Backend agent | confirming |
| 2 | Confirm the 16:45→failure cutover commit + any banned-model pin (the "Fable 5" angle) | Cutover-Config agent | confirming |
| 3 | Make the fix live: hunk-isolated commit of the doom-loop changes (do NOT sweep parallel sessions' unrelated hunks), then restart the app | — | pending |
| 4 | Live re-drive: drag a finished + a failed card onto the dock → expect a spoken/chat recap and **no new mission row** | — | pending |
| 5 | (Defense-in-depth, optional) Make the "Restart" button refuse to re-dispatch a mission whose stored prompt is a recap directive | — | proposed |

## 6. Verification gates (before claiming done)

- [ ] App restarted; `import jarvis; jarvis.__file__` confirms the live editable path.
- [ ] Drag a finished card → recap appears in chat, **no** new mission in `missions.db`.
- [ ] Drag a previously-FAILED recap card → recap, no new mission.
- [ ] A genuine informational mission ("research … for me") → APPROVED, not `critic_loop_exhausted`.
- [ ] Full `tests/unit/brain/` + `tests/missions/critic/` green.
