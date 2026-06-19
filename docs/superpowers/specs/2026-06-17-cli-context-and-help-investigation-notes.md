# CLI context-bloat & `--help` truncation — investigation notes

- **Date:** 2026-06-17
- **Status:** (b) fixed; (a) documented, needs live instrumentation
- **Context:** Two items deferred from the CLI-usability work this session
  (gcloud honesty → cloud forcing → CLI-first tool selection).

## (b) `<cli> --help` truncation — FIXED

**Finding (measured on host):** `gcloud --help` is **17,736 chars**. The CLI
tool's normal `MAX_STDOUT_CHARS = 4000` cap truncates it inside the global-flags
section, **before** the GROUPS/COMMANDS list — so a model running bare
`gcloud --help` for self-discovery never sees the command groups. Group-level
help is small and unaffected (`gcloud billing --help` = 1,003 chars).

**Fix:** `MAX_HELP_STDOUT_CHARS = 16000` in `jarvis/clis/tool.py`; a help
invocation (`_is_help_command`: `--help`/`-h`/a `help` subcommand) gets the
larger cap. `--help` output now surfaces the command list for discovery, while
normal command output keeps the lean 4000-char cap (TTS/context budget). Tests:
`tests/unit/clis/test_tool.py::test_help_command_gets_larger_stdout_cap` +
`::test_non_help_command_keeps_normal_cap`. This makes the Component-4
self-documentation prompt (run `<cli> --help` first) actually usable for
top-level discovery, not only group-level.

## (a) 112k-token fast-tier context — DOCUMENTED, not yet fixed

**Symptom:** the live gcloud billing turn (2026-06-17, gemini-3.5-flash, fast
tier) reported `tokens_in = 112,412` — very large for a fast turn.

**What was ruled out (measured offline):**
- The gcloud tool output was tiny (94 + 535 bytes) — not the source.
- The persona (`JARVIS_PERSONA.md`) is ~8,086 chars (~2k tokens) — small.

**Leading hypothesis (the big variable contributors):**
1. **Live tool schemas** — especially multi-tool MCP plugins. The `github`
   marketplace plugin alone expands to ~37 namespaced tools, each with a JSON
   schema; with several plugins + every connected `cli_<name>` tool, the tool
   array dominates the prompt. **Component 3 of this session's CLI-first work
   already mitigates this**: `suppress_plugin_tools_covered_by_cli` drops a
   plugin's tools when its CLI is connected, so connecting `cli_gh` removes the
   ~37 `github/*` schemas from the turn.
2. **Accumulated voice-session history** — by the 3rd turn of a multi-tool
   session the running `_history` plus re-injected tool results add up.

**Why no code fix yet:** the precise per-turn breakdown (system prompt vs tool
schemas vs history vs awareness) only exists in the **running** app — it cannot
be measured offline. The correct next step is **live instrumentation**: log a
one-line per-turn token breakdown (prompt / tool-schemas / history) from
`BrainManager.generate()` behind a debug flag, run one gcloud turn, and read the
dominant contributor. That is a separate, app-running task; guessing a fix
without the measurement would be premature.

**Recommendation:** ship Component 3 (already done), then add the per-turn token
breakdown log and re-measure before any further trimming (e.g. fast-tier
omitting plugin usage-cards, or capping history for tool-heavy turns).
