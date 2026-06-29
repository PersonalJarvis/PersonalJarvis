# Re-run Jarvis-Agent missions — Continue (cancelled) / Restart (failed)

**Date:** 2026-06-15
**Status:** Design approved, pending spec review
**Area:** Phase-6 Mission subsystem + Outputs view (`jarvis/ui/web/`)

## Problem

The Jarvis-Agent **Outputs** view shows each mission as a card with a status badge
(`CANCELLED`, `error`, `success`, `running`). Once a mission is cancelled by the
user or fails (critic-loop exhaustion, crash recovery, task error, timeout),
there is no way to run it again. The user must re-type the original request from
scratch. We want two one-click affordances directly on the card:

- **Continue** a `CANCELLED` mission.
- **Restart** a `FAILED` or `TIMED_OUT` mission.

## Decision summary (from brainstorming)

1. **Re-run from scratch, not true resume.** Missions are short (minutes) and the
   orchestrator already re-decomposes the prompt and runs every step on entry.
   There is no durable per-step checkpoint to resume from, and building one would
   touch the sensitive critic-loop core. Both buttons therefore re-dispatch the
   original task fresh. "Continue" and "Restart" are two labels for the *same*
   operation, gated on the source state.
2. **Keep the original card; spawn a linked re-run.** The re-run is a new mission
   (new UUID) linked to the original via `parent_mission_id`. The original
   `CANCELLED`/`FAILED` card stays as a permanent audit record; a fresh card
   appears and runs. The terminal mission is never mutated — so **no** changes to
   the state machine, the orchestrator idempotency guard, or recovery.
3. **Single click + pending state.** Re-running is constructive (it cannot
   destroy anything), so it does not need the deliberate 1.2 s hold gesture the
   destructive *abort* button uses. One click fires it; the button shows a
   `Starting…` spinner and disables until the request returns.

## Why this is low-risk: how the Outputs view is wired

The Outputs view is **not** a direct projection of the `missions` table. The
endpoint `GET /api/outputs` (`outputs_routes.py:235`) walks on-disk directories
under `sub-agents-outputs/`, keeps only the persistent `mission_<short>` dirs,
and enriches each with DB status via `WHERE id LIKE '<prefix>%'`
(`outputs_routes.py:142`). The `mission_<short>` directory is created by the
orchestrator (`orchestrator.py:231`) from `mission_id[:13]`.

Consequence: a freshly dispatched mission gets a new UUID → the orchestrator
mints a new `mission_<short2>` directory → a brand-new card appears
automatically on the next 3 s poll, while the original `mission_<short1>`
directory and its card stay untouched. The "spawn a new linked mission" approach
therefore drops straight into the existing view with no list-projection changes.

Status mapping is already correct for the gating
(`outputs_routes.py:215-232`): `FAILED` and `TIMED_OUT` both map to UI status
`"error"`; `CANCELLED` maps to `"cancelled"`. So:

- UI `status === "cancelled"` → **Continue**
- UI `status === "error"` → **Restart** (covers both `FAILED` and `TIMED_OUT`)

## Backend

### New endpoint — `POST /api/missions/{mission_id}/rerun`

Added to `jarvis/ui/web/missions_routes.py`. Mirrors the existing `dispatch` /
`cancel` route conventions (resource from `app.state`, `503` when absent,
inline Pydantic body model).

Request body (`RerunBody`):

```python
class RerunBody(BaseModel):
    confirmed: bool = False  # destructive_confirm gate, same as DispatchBody
```

Logic:

1. `view = await mgr.store.get_mission_view(mission_id)` → `(prompt, state,
   language, iteration, cost_usd)`. `404` if `None`.
2. Validate `MissionState(state) in {CANCELLED, FAILED, TIMED_OUT}`. Otherwise
   `409` with an English detail, e.g. `"Mission is not re-runnable from state
   APPROVED"`. `APPROVED` is deliberately excluded (out of scope).
3. **Destructive re-gate:** call `is_destructive(prompt)` (same helper
   `/dispatch` uses). If destructive and `not body.confirmed`, return the same
   `409 {requires_confirm: true, pattern_id, matched_text, target_hint, warning}`
   shape `/dispatch` returns. A re-run must not silently bypass the safety gate.
4. Derive the audit action from the source state:
   `CANCELLED → "continue"` (reason `"ui_continue"`),
   `FAILED`/`TIMED_OUT → "restart"` (reason `"ui_restart"`).
5. `new_id = await mgr.dispatch(prompt=prompt, language=language,
   source_actor="ui", parent_mission_id=mission_id)`.
6. If a Kontrollierer is wired:
   `background_tasks.add_task(kontrollierer.run_mission, new_id)`;
   `started = True`. Otherwise `started = False` (matches `/dispatch`).
7. Return:

```json
{
  "ok": true,
  "parent_mission_id": "<original-uuid>",
  "mission_id": "<new-uuid>",
  "action": "continue" | "restart",
  "started": true | false
}
```

### What does NOT change

- `state_machine.py` — no new transitions. The terminal mission stays terminal.
- `orchestrator.py` idempotency guard — the re-run is a fresh `PENDING` mission.
- `recovery.py` — unaffected.

Rationale: one endpoint instead of separate `/continue` + `/restart` because the
operation is byte-for-byte identical; the backend derives intent from the source
state, so the audit trail is accurate without trusting a client-supplied label,
and the API surface stays minimal.

### Language policy

All new strings (error details, the `warning` text) are **English**, per the
repo Output Language Policy and the CI `language-policy` gate. The surrounding
German strings in `missions_routes.py` are grandfathered and left untouched.

## Frontend

### `hooks/useOutputs.ts`

Add a React Query mutation mirroring `useCancelMission`:

```ts
export function useRerunMission() {
  const qc = useQueryClient();
  return useMutation({
    mutationFn: async (missionId: string) => {
      const res = await fetch(`/api/missions/${missionId}/rerun`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ confirmed: false }),
      });
      if (!res.ok) throw await res.json().catch(() => ({}));
      return res.json();
    },
    onSuccess: () => { qc.invalidateQueries({ queryKey: ["outputs"] }); },
  });
}
```

Destructive `409 {requires_confirm}` handling: caught in the mutation's error
path. The first version surfaces it via the existing dispatch-confirm affordance
if one exists; the implementation plan will confirm whether the frontend already
has a confirm dialog for `/dispatch` and reuse it, otherwise fall back to a
minimal inline confirm (no native `confirm()` — browser dialogs block the
extension; see CLAUDE.md). This is the one detail to resolve during planning.

### `views/OutputsView.tsx`

In `SessionRow` (sidebar) and `SessionDetail` (detail pane), render a
single-click action button next to the existing controls:

- `status === "cancelled" && meta.mission_id` → **Continue** button.
- `status === "error" && meta.mission_id` → **Restart** button.
- Disabled + `Starting…` spinner while `rerun.isPending`.
- Styled to match the card's existing non-destructive button (the "Open in
  Explorer" affordance), **not** `HoldToAbortButton`.

### i18n

New keys under `outputs_view` in all three locale files. **English is the
source** (`en.json`); `de.json` and `es.json` carry translations:

| key | en (source) | de | es |
|---|---|---|---|
| `continue_label` | Continue mission | Mission fortsetzen | Continuar misión |
| `restart_label` | Restart mission | Mission neu starten | Reiniciar misión |
| `rerun_starting` | Starting… | Wird gestartet… | Iniciando… |

## Testing

### Backend (pytest, `tests/unit/...` mirroring existing mission-route tests)

- Re-run from `CANCELLED` → new mission, `action == "continue"`, source row
  still `CANCELLED`, new row `PENDING`, `MissionDispatched.parent_mission_id`
  equals the source id.
- Re-run from `FAILED` and from `TIMED_OUT` → `action == "restart"`.
- Re-run from `APPROVED` → `409`.
- Re-run of unknown id → `404`.
- Destructive prompt + `confirmed=false` → `409 requires_confirm`;
  `confirmed=true` → proceeds.
- Kontrollierer absent → `200/201` with `started == false`, mission created
  `PENDING`.

### Frontend (vitest)

- Button visibility: Continue only on `cancelled`, Restart only on `error`,
  neither on `running`/`success`/`unknown` or when `mission_id` is null.
- Click fires the mutation with the correct mission id.

### Live verification

Drive the running Desktop app with the `chrome-checkup-loop` skill: confirm the
Continue button appears on the cancelled card in the screenshot, click it,
verify a new RUNNING card appears and the original card stays CANCELLED.

## Out of scope (YAGNI)

- True step-level resume / checkpointing.
- Re-running `APPROVED` missions.
- Voice-command trigger ("Jarvis, restart that mission").
- A visible lineage badge ("re-run of X"). The `parent_mission_id` link is
  persisted in the `MissionDispatched` event, so this can be added later without
  rework; it is not required for the feature to work.
