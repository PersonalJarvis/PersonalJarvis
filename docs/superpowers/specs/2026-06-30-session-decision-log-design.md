# Session-Decision-Log — the honest "why" in the Run Inspector

- **Status:** Approved direction (maintainer), implementation in progress
- **Date:** 2026-06-30
- **Type:** Additive — surfaces an already-captured field + a local Markdown mirror
- **Parent:** `2026-06-17-run-inspector-debug-section-design.md` (the Run Inspector itself)

---

## 1. Goal (one sentence)

The Run Inspector shows *what* Jarvis did; this adds the missing *why* — in
Jarvis's own words, captured for free, persisted honestly, and shown both in the
app and in a local Markdown "decision diary" per session.

## 2. Maintainer-approved decisions

1. **How the "why" is produced — free & honest.** Two sources, both captured
   without an extra model call, never invented:
   - **Model rationale** (`rationale_source="model"`): the brain's own
     natural-language text emitted next to a `tool_use` block. Already wired:
     `tool_use_loop.py` passes `agg.text` → `ToolExecutor.execute(rationale=…)`
     → redacted/capped via `safe_preview` → `ActionProposed.rationale` →
     persisted by the recorder into `voice_events`.
   - **Rule rationale** (`rationale_source="rule"`): a deterministic, honest
     plain-text explanation built by the analyzer from *captured facts*
     (approval source, denial reason, risk tier, provider fallback, tier
     choice) — e.g. `approved_by=whitelist` → "auto-approved — on your
     allow-list". This is NOT guessing; it is restating a recorded fact.
   - Where neither is available the UI shows "no rationale recorded" instead of
     fabricating one (honesty over guessing — CLAUDE.md §1).
2. **Where it lives — app + Markdown.** The structured truth lives in
   `voice_events` and drives the searchable Run Inspector. In addition a
   human-readable Markdown file is written per session (the promised diary),
   under the cross-platform user data dir.
3. **Scope — voice runs first.** Voice sessions (Hey-Jarvis → hangup) incl. the
   tools/missions they trigger. Typed chat is a later, low-effort add (same
   building blocks).

## 3. Pre-existing work (already committed — do NOT rebuild)

- `ActionProposed.rationale` field (`jarvis/core/events.py`).
- `ToolExecutor.execute(rationale=…)` redacts + caps + publishes it
  (`jarvis/safety/tool_executor.py`).
- The brain fills it: `tool_use_loop.py` passes `agg.text` as `rationale`.
- Recorder persists it: `rationale` is in `_payload_for`'s whitelist and
  `ActionProposed` is in `_RAW_EVENT_KINDS` (`jarvis/sessions/recorder.py`).
- `ToolCallStarted.args_preview` + `ToolCallCompleted.output_preview` are already
  whitelisted/persisted but unused by any DTO.

## 4. Remaining work (this spec) — read-only/derive + UI + mirror

### Layer 1 — DTO + enum SSOT (anti-drift, BUG-008)
- `jarvis/runs/constants.py`: add `RATIONALE_MODEL="model"`, `RATIONALE_RULE="rule"`,
  `RATIONALE_SOURCES=(…)`. Parity-tested.
- `jarvis/runs/model.py`:
  - `DecisionStep` += `rationale: str = ""`, `rationale_source: str = ""`.
  - `ToolCall` += `command: str = ""` (from args_preview/full_command),
    `output: str = ""` (from output_preview). Both plain `str`, redacted upstream.

### Layer 2 — Analyzer (pure, surfaces the captured why + builds rule why)
- `build_decision_path`: attach `ActionProposed.rationale` → step
  `rationale`/`rationale_source="model"`; build honest `rule` rationale for
  approve/deny/fallback/tier steps from captured facts.
- Surface `args_preview`/`output_preview` into `ToolCall.command`/`output`
  (from `ToolCallStarted`/`ToolCallCompleted` events; CLI `full_command` too).

### Layer 3 — Markdown diary writer (new, off-hot-path)
- `jarvis/runs/diary.py`: render a `Run` → Markdown; `write_run_diary(run)` to
  `user_data_dir()/"decision_log"/<YYYY-MM-DD>-<session_id>.md` (pathlib, UTF-8).
- Trigger: a lightweight `VoiceSessionEnded` subscriber loads the run and writes
  the file (session-end is not the hot path; AP-9 respected). Toggle + path are
  config-driven and in-app editable. Degrades to a logged no-op on failure.

### Layer 4 — Frontend (parity-guarded)
- `types.ts`: mirror new fields + `RATIONALE_SOURCES`.
- New prominent **"Why" block** per turn in `RunTurnCard.tsx` (not buried in
  Forensics): each decision's rationale, tagged model 🧠 vs rule ⚖ vs muted
  "no rationale recorded".
- `DecisionPath.tsx`: render rationale under each step.
- `ToolTable.tsx`: show command + output (already-captured, currently hidden).
- i18n keys (de/en/es), English source.

### Layer 5 — Tests + the three non-maintainer paths (CLAUDE.md §3)
- Unit: analyzer rationale wiring (model + rule), diary rendering, parity.
- Provider-agnostic: a run whose provider gives NO accompanying text still gets
  honest rule rationales (no model rationale, never a crash).
- Headless: diary path resolves + writes on a dir with no keyring; toggle off = no-op.
- Frontend vitest: Why block renders all three rationale states; parity test.

### Layer 6 — Browser verification
- `chrome-checkup-loop` skill: open Run Inspector, click a run, confirm the Why
  block + command/output render, no console/network errors, layout intact.

## 5. Privacy
The rationale rides the same redaction as voiced output (`safe_preview` at
publish). Raw args stay scrubbed. The Markdown diary contains only already-
redacted fields, so it is safe under the public-repo doctrine (it is a local,
git-ignored runtime artifact regardless).

## 6. Progress tracker (loop anchor)
- [ ] L1 constants + model + parity test
- [ ] L2 analyzer rationale (model + rule) + command/output
- [ ] L3 diary writer + session-end trigger + config toggle
- [ ] L4 frontend types + Why block + ToolTable command/output + i18n
- [ ] L5 tests incl. 3 non-maintainer paths
- [ ] L6 chrome-checkup-loop browser verification
