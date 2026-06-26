# CLI Answer-Quality Hardening — Design

**Date:** 2026-06-26
**Status:** Approved (brainstorming → spec)
**Scope:** Make the brain produce natural answers (not "NPC" / raw-dump answers)
when it drives the `jarvisctl` control CLI natively via the `cli_jarvisctl` tool.

---

## 1. Problem

When a user talks to Jarvis and the brain invokes a connected CLI tool
(`cli_*`, e.g. `cli_jarvisctl`), the tool runs `jarvisctl <command>` as a
subprocess and returns its stdout/stderr to the brain. Two gaps make the spoken
or written answer come out robotic:

1. **Rich tables instead of JSON.** `jarvisctl` renders a human-friendly Rich
   table (Unicode box-drawing) for any list-of-dicts payload unless the global
   `--json` flag is passed. The brain rarely passes it, so it receives a table
   it has to parse character-by-character and reads back clumsily.
   (`jarvis/cli_ctl/render.py::emit`, list-of-dicts branch.)

2. **No interpretation rule.** The brain is told *when* to use a CLI
   (`jarvis/clis/prompt_section.py`), but never *how* to read a `cli_*` tool
   result. The result is the structured dict
   `{success, output:{exit_code, stdout, stderr, duration_ms}, error}`
   (`jarvis/clis/tool.py`, ToolResult construction). With no guidance, a weaker
   model may read the JSON envelope aloud, narrate `exit 0`, or fail to extract
   the meaningful `stdout`.

**Explicitly NOT a problem (verified in code):**

- The tool-use loop *does* run a second brain turn after a tool call
  (`jarvis/brain/tool_use_loop.py`, the `while True:` loop), so result
  interpretation already happens — the issue is its *quality*, not its absence.
- The Meta-Debug short-circuit only fires for `spawn_worker`
  (`elif tool_name == "spawn_worker" and _is_meta_debug_intent(...)`), never
  for `cli_*` tools. It is out of scope for this change.

---

## 2. Goals / Non-Goals

**Goals**

- The brain (and any non-interactive consumer: pipes, scripts) receives clean,
  parsable JSON from `jarvisctl` by default.
- A human running `jarvisctl` in a real terminal still gets the Rich table.
- The brain has an explicit, short, language-neutral rule for reading `cli_*`
  results and answering naturally.

**Non-Goals (YAGNI)**

- No second narration LLM turn (the follow-up turn already exists).
- No change to the stdout/stderr truncation caps.
- No change to the `spawn_worker` Meta-Debug block.
- No new curated CLI command groups (separate coverage workstream).

---

## 3. Design

### 3.1 Component 1 — JSON when output is not a TTY

**File:** `jarvis/cli_ctl/render.py`

`emit(payload, *, as_json)` computes an effective flag:

```
effective_json = as_json or (not sys.stdout.isatty())
```

- When `effective_json` is true → the existing JSON path
  (`json.dumps(..., ensure_ascii=False)`).
- When false → the existing Rich table / `print_json` / scalar paths, unchanged.

Rationale: the `cli_jarvisctl` tool spawns `jarvisctl` with piped stdout
(`asyncio.create_subprocess_exec`, `stdout=PIPE`), so `isatty()` is false and
the brain automatically gets JSON. Pipes and scripts get JSON for free
(industry-standard behavior: gh, docker, npm). An explicit `--json` still forces
JSON, and a real interactive terminal (`isatty()` true) keeps the Rich table.

`isatty()` must be read defensively — some stdio wrappers lack the attribute or
raise; treat any failure as "not a TTY" (prefer JSON), since the only consumers
without a real `isatty` are non-interactive.

### 3.2 Component 2 — `cli_*` result interpretation rule

**File:** `jarvis/clis/prompt_section.py`

Extend the existing `_FOOTER` of the CONNECTED CLIS section with a short,
language-neutral block describing the result shape and the reading rule:

- Result shape: `{success, output:{exit_code, stdout, stderr, duration_ms}, error}`.
- `exit_code == 0` means success; `stdout` is the actual result (often JSON) —
  interpret it and summarize in natural language; never read the raw JSON
  envelope or the table characters aloud.
- On `exit_code != 0`, briefly explain the cause from `stderr`; never quote the
  error dict.

This is a pure prompt addition: no new LLM call, no latency, and
`scrub_for_voice` (`jarvis/brain/output_filter.py`) remains the regex safety net
behind it.

---

## 4. Data Flow (after change)

```
user → brain → cli_jarvisctl tool
                 └─ subprocess: jarvisctl <cmd>   (stdout piped → not a TTY)
                       └─ render.emit → JSON (effective_json = true)
                 └─ ToolResult{output:{exit_code, stdout(JSON), stderr}}
           → brain follow-up turn
                 └─ guided by prompt_section rule: read exit_code, parse stdout,
                    answer naturally
           → scrub_for_voice → TTS / chat
```

---

## 5. Testing

- `tests/unit/cli_ctl/test_render.py`: `emit` with a faked `sys.stdout.isatty`
  → `True` yields a Rich table for list-of-dicts; `False` yields JSON; an
  explicit `as_json=True` yields JSON regardless of `isatty`; a faked stdout
  with no `isatty` attribute falls back to JSON.
- Existing `cli_ctl` command tests that assert on Rich output: confirm they run
  under Typer's `CliRunner` (non-TTY) and update the expected output to the JSON
  path, or pin `isatty` true where the test specifically wants the table. (Audit
  all `tests/unit/cli_ctl/` assertions during implementation.)
- `tests/unit/clis/test_prompt_section.py` (new or extended): the rendered
  CONNECTED CLIS section contains the result-shape description and the
  "interpret stdout, do not read the envelope" rule when at least one CLI is
  connected; still returns `""` when none are.

---

## 6. Risks

- **Test churn (primary).** Flipping the non-TTY default to JSON changes the
  output every existing `cli_ctl` command test sees under `CliRunner`. Mitigation:
  audit and update assertions as part of the implementation, not after.
- **A consumer that parsed the Rich table.** None known — the brain is the only
  programmatic consumer and benefits from JSON; humans keep the table via TTY.

---

## 7. Out of Scope (tracked elsewhere)

- Ergonomic coverage gap: curated CLI groups missing for `settings`, `profile`,
  `self-mod`, `chats`, etc. (Coverage workstream — Anliegen 2.)
- Hardening `check_cli_coverage.py` to enforce a curated group per tag.
- Refining the `spawn_worker` Meta-Debug block.
