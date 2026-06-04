---
title: Anti-Drift for Multi-Layer Enums
date: 2026-05-05
scope: Any enum-like value that crosses the Python ↔ TypeScript ↔ SQL boundary
---

# Anti-Drift for Multi-Layer Enums

## Why this doc exists

BUG-008 in `docs/BUGS.md` happened twice. Both times the symptom was
identical — an empty transcription tab — and both times the root cause
was the same: the runtime path produced a string value that the
consuming Pydantic Literal had never been told about. Each time the
fix was a small code change; each time the underlying *coordination
problem* was left in place, and the bug came back.

This document captures the pattern so the next time someone introduces
an enum-like value that crosses layers, they wire it into the
prevention scaffolding instead of repeating the cleanup.

## The pattern

A value lives in **five places** at once:

1. **A producer**, usually a Python module that writes the string
   into runtime state, persistent storage, or an event payload.
2. **A persistence schema** (here: SQLite) that stores the string.
3. **A Pydantic model** that hands the string to FastAPI.
4. **A TypeScript type** that the React app uses to type-check
   the payload.
5. **A user-facing label**, typically a `switch` statement that
   maps the string to a translated string for the UI.

Drift between any two of these five surfaces is invisible until a
specific row reaches a specific consumer. The longer the gap, the
worse the diagnosis (in BUG-008's case, restoring an old commit
re-introduced the drift weeks later, not on the day of the change).

## Concrete example: `HangupReason`

The `voice_sessions.hangup_reason` column is the canonical case in
this project. Files involved:

| Layer | File | What it holds |
|------|------|---------------|
| 0 — single source of truth | `jarvis/sessions/constants.py` | `HANGUP_REASONS` tuple + one symbolic constant per value |
| 1 — producers | `jarvis/speech/pipeline.py`, `jarvis/sessions/init.py` | Imports symbols from layer 0; never writes raw strings |
| 2 — persistence | `jarvis/sessions/schema.sql` | Doc-comment in the column declaration enumerates accepted values |
| 3 — Pydantic | `jarvis/sessions/models.py` | `HangupReason = Literal[...]`, mirrored by a module-level runtime assertion against the tuple |
| 4 — TypeScript | `jarvis/ui/web/frontend/src/components/sessions/types.ts` | Union type spelling each value |
| 5 — UI label | `jarvis/ui/web/frontend/src/components/sessions/SessionList.tsx` | `hangupLabel(reason)` switch with one `case` per value |

## The defenses, in order of strength

The five defenses below are arranged from cheapest-to-implement to
most-thorough. We use *all* of them for `HangupReason`; future enums
should adopt the same pattern.

### D1. Symbolic constants at every call site

Producers import `HANGUP_TURN_COMPLETE` from
`jarvis/sessions/constants.py`. They never spell the string
themselves. A typo turns into an `ImportError` or `AttributeError`
at startup, not into a row that detonates HTTP 500 weeks later.

### D2. Import-time runtime assertion

`jarvis/sessions/models.py`:

```python
if set(get_args(HangupReason)) != set(HANGUP_REASONS):
    raise RuntimeError("HangupReason Literal drifted from HANGUP_REASONS — ...")
```

Pydantic cannot accept a tuple in `Literal[...]`, so the inline list
is duplicated. The assertion makes the duplication safe: any drift
between the tuple and the Literal raises at import, which means
pytest collection fails immediately. There is no quiet-period
between the bad edit and the symptom.

### D3. Parity test

`tests/unit/sessions/test_hangup_reason_parity.py` reads:

- `HANGUP_REASONS` (Python)
- `types.ts` union members (regex-extracted)
- `SessionList.tsx` switch cases (regex-extracted)
- `schema.sql` column-comment members

…and asserts all four equal the canonical tuple. Adding a new value
without touching one of the layers fails this test on the next CI
run.

### D4. DB compatibility test

`tests/integration/test_sessions_db_compatibility.py` queries
`SELECT DISTINCT hangup_reason FROM voice_sessions` and asserts
every value is in `HANGUP_REASONS`. This catches the case D1–D3 do
not: a value introduced by a script, an older build, a manual SQL
edit, or any path that bypasses the constants module. Skips
gracefully if the DB does not exist.

### D5. Self-defending list endpoint

`jarvis/sessions/store.py::SessionStore.list_sessions` wraps the
per-row Pydantic construction in `try/except ValidationError`. A
single bad row no longer empties the entire list — it gets a
structured `hangup_reason_drift_skipped` warning and is skipped.
The user sees a partial list (signaling something is up) instead
of a blank page.

## Adoption checklist

Use this when adding a new enum-like value that crosses layers:

- [ ] Add the constant to a `*/constants.py` module, with a
      docstring explaining when it is used.
- [ ] Update the canonical tuple (e.g. `HANGUP_REASONS`).
- [ ] Update the Pydantic `Literal` and confirm the import-time
      assertion still passes (`python -c "import jarvis.sessions.models"`).
- [ ] Update the TS union in the frontend `types.ts`.
- [ ] Update the user-facing label switch in the relevant `.tsx`.
- [ ] Update the column doc-comment in the SQL schema.
- [ ] Update the producer to import the new symbol — never spell
      the string directly.
- [ ] Run the parity test: `pytest tests/unit/sessions/test_hangup_reason_parity.py`.
- [ ] Run the DB test: `pytest tests/integration/test_sessions_db_compatibility.py`.
- [ ] Restart the running app instance — Pydantic models are
      module-loaded at import; a stale RAM image will keep producing
      500s until the process is recycled.

## Generalizing beyond `HangupReason`

Any enum-like value with this shape is a candidate:

- It has a small fixed vocabulary that grows over time.
- It is produced in Python, persisted in SQLite or JSON, validated
  in Pydantic, typed in TypeScript, and labelled in the UI.
- The vocabulary is open — new values get added when features land.

Likely candidates in this codebase that should be migrated to the
same pattern as their vocabularies grow:

- `VoiceTier` (`jarvis/sessions/models.py`).
- Mission status values in `jarvis/missions/`.
- Skill lifecycle states (`SkillLifecycleState` —
  `jarvis/skills/schema.py`).

If you migrate one of these, append a row to the table at the top
of this doc with the file paths, and reuse the parity-test recipe.

## Adopted: `MessageRole`

The `messages.role` column in the recall store was migrated to the
same five-layer pattern in 2026-05-16 as part of the F6 / BUG-019
role-CHECK fix. Files involved:

| Layer | File | What it holds |
|------|------|---------------|
| 0 — single source of truth | `jarvis/memory/constants.py` | `ALLOWED_ROLES` tuple + one `ROLE_<UPPER>` symbolic constant per value + `MessageRole` Literal mirror + runtime assertion |
| 1 — producers | `jarvis/memory/message_recorder.py` (gate), other emitters in `jarvis/ui/web/server.py`, `jarvis/brain/manager.py`, etc. | Recorder imports the frozenset mirror; non-recorder producers stay free-form because the recorder is the persistence gate |
| 2 — persistence (schema) | `jarvis/memory/schema.sql` | CHECK clause + doc-comment enumerate accepted values |
| 3 — persistence (migration) | `jarvis/memory/migrations/0003_expand_role_check.sql` | Forward migration that widens the CHECK on pre-existing user databases |
| 4 — runtime allowlist | `jarvis/memory/message_recorder.py::_RECALL_ALLOWED_ROLES` | Frozenset built from `ALLOWED_ROLES_FROZENSET`; gates the bus → SQL path |
| 5 — parity tests | `tests/unit/memory/test_role_constraint.py` | Tuple ↔ Literal ↔ schema CHECK ↔ migration CHECK ↔ doc-comment ↔ live INSERT |

Notes specific to this adoption:

- There is no TypeScript / UI label layer for `MessageRole` — the
  chat UI receives the string verbatim and renders it generically,
  so layers 4–5 of the HangupReason model collapse into the test.
- `preamble` is deliberately *not* in `ALLOWED_ROLES`. It is a UI
  affordance emitted by the desktop server's pre-ack pipeline; the
  recorder drops it silently. Adding it to the CHECK would let it
  inflate the recall index without changing user-visible behaviour.
- A minimal forward-migration runner ships alongside the constants
  (`jarvis/memory/migration_runner.py`) because the previous
  `executescript(schema.sql)` bootstrap could not widen a CHECK on
  an existing table. Future memory migrations should be added under
  `jarvis/memory/migrations/` using the next free `NNNN` integer.

## Related

- `docs/BUGS.md` — Bug-008 (first episode) and Bug-008 Episode 2
  for the full incident reports.
- BUG-006 in `docs/BUGS.md` (Restore-Three-Layer-Trap) — explains
  why a restore can re-introduce drift even when the surrounding
  code looks healthy.

## See also

- [ADR-0016 — Visible-Feedback Contract](adr/0016-visible-feedback-contract.md)
  is the geometry / visibility sibling of this pattern. Anti-drift
  catches *string-enum drift between layers* by treating the
  vocabulary as a versioned contract observable at boot
  (`set(literal) == set(constants)`). ADR-0016 catches *user-visible
  feedback drift* (orb invisible, TTS silent, toast suppressed) by
  publishing a `UserVisibleFeedback{surface, expected, observed,
  correlation_id}` event from every UI surface and comparing intent
  to outcome at runtime. The two patterns share the same philosophy
  — *make the regression observable at a seam, not at the user* —
  but use different mechanisms because string drift is detectable at
  boot while visibility drift only surfaces with live geometry / live
  audio / live windows.

---

## Cross-reference: ADR-0017 — Capability Coupling

[ADR-0017](adr/0017-capability-coupling.md) applies the same
*versioned contract* philosophy to the **executable surface** of
Jarvis — the set of things the running instance can actually do.

The parallel is direct:

| Pattern | What drifts | Observable at? | Contract object |
|---|---|---|---|
| Anti-drift (this doc) | String enum between Python ↔ SQL ↔ Pydantic ↔ TS ↔ UI | Boot (`set` comparison) | `HANGUP_REASONS` tuple in `constants.py` |
| ADR-0016 Visible-Feedback | Intent vs. actual user-visible outcome | Runtime (geometry + audio) | `UserVisibleFeedback` event |
| ADR-0017 Capability Coupling | Brain claims vs. registered executable surface | Pre-generation (gate) + Critic | `CapabilityRegistry` singleton |

Where the anti-drift pattern prevents *"the runtime writes a string value no
consuming layer knows about"*, ADR-0017 prevents *"the brain claims to perform
an action no registered tool can execute"*. The fix in both cases is the same
structural move: introduce a single source of truth that every layer reads from
at the right moment.

Concretely, the `search_web` prompt-claim drift (`manager.py:774` hardcodes
`NUTZE: search_web` even when no web-search capability is registered) is the
capability-coupling analogue of BUG-008: a vocabulary value used in one layer
that does not exist in another. ADR-0017's `render_for_prompt()` solves it the
same way D1–D3 solve BUG-008: replace the hardcoded string with a read from the
canonical source.

Regression test: `tests/integration/test_capability_coupling_e2e.py` —
specifically `test_search_web_without_registration_is_unsupported` and
`test_render_for_prompt_excludes_unregistered_capabilities`.
