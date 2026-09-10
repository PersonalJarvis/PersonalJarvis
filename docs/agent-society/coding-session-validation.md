# Society coding-session validation

Tier: T3 shared capability. The implementation reuses the existing IDE, CLI
catalog, account selection, PTY lifecycle, delivery verification and transcript
readers. There is no separate terminal runner.

## Automated evidence

- Final focused run: 179 passed. Covers the new contracts, IDE session and
  restoration tests, prompt delivery, society surface, supervisor gateway and
  subscription harness.
- Independent output-filter, turn-language and hangup-parity guards: 265 passed.
- Broader IDE, society, routing, gateway and CLI run: 2,687 passed, four skipped,
  16 failed. All 16 failures reproduced on untouched base commit `56e4f0aba`:
  composer timing history (two), pane movement ordering (three), the host shell
  path assertion (one), society checkpoint/world-feed expectations (four), CLI
  index parity (one), and retired local-model assistant commands (five).
- Targeted Ruff and mypy checks passed. The CLI coverage gate passed; regenerating
  the CLI reference produced no content changes.
- Final scoped MCP/account-discovery follow-up: 28 passed. Account discovery
  distinguishes installed CLIs from authenticated accounts without exposing
  credentials. Factory wiring remains lazy and plan-mode access is refused.
- Isolated boot-budget check passed: window 1,206 ms, voice usability 10,550 ms,
  app interactive 10,644 ms, all below their existing budgets. The user's running
  desktop was not restarted or stopped.
- The wider CLI/scripts Ruff check reports an existing `S310` in
  `scripts/ci/update_contributors.py`; the changed Python files pass.

The contracts exercise real IDE registry methods using fake PTYs. They cover
headless startup with Claude Code, Codex and OpenCode, a newly registered coding
CLI, correct project cwd, duplicate and interrupted request receipts, distinct
concurrent sends, slow startup, busy panes, two projects with identical names,
tab switches and renaming during delivery, closure, persisted restoration,
invalid account refusal, missing transcripts, bounded recorded context, newly
created society agents, grants/denies, subscription-seat catalog isolation and
the executor approval boundary. The REST composition test switches the active
workspace and renames the target while composition is awaited.

## Composer follow-up

Tier: T2 existing chat/IDE surface; native backends and shared wire schemas are unchanged.

The card composer now offers a separate coding-agent group through `@` and the
add button. It loads registered CLIs on demand, retains the chosen CLI in the
draft, and offers the existing project-folder picker or a folder named in the
message. The lead's typed chat receives the controller without switching its
brain provider or entering the global worker catalog.

Frontend checks cover explicit and message-supplied project paths, delayed
folder selection and assignment handoff; the locale parity and TypeScript
checks pass. A Chrome test using the real components and fixture API responses
verified German light/dark rendering, folder-dialog selection and the submitted
assignment with no page errors. This fixture test does not claim live CLI
execution in the user's running app.

Follow-up verification: 20 frontend tests passed, 63 targeted Python tests
passed, and the final type-guard adjustment passed 34 affected tests. Targeted
mypy and Ruff passed. The production frontend build completed successfully.

## Supervision follow-up

Tier: T3 shared autonomous workflow capability. `assign` structures the initial
brief and establishes durable ownership. The application's IDE activity events
wake the same owner chat; a local jittered fallback covers missed notifications.
Replies are bound to the current process, input token and update receipt. The
supervisor never writes directly to a terminal or treats terminal output as user
authorization. Normal scoped follow-ups and gated dialogs remain distinct.

Validation includes 146 focused regression tests, followed by 43 final coding
contracts covering structured assignment, end-to-end fake question/reply/result
flow, busy owners, exclusive ownership, crash/restart deduplication, stale input,
dialog gating, kill switch, budget/deadline limits and no-progress recovery.
Fifteen related frontend tests passed. Targeted Ruff and mypy passed, and the
production frontend build succeeded. The isolated boot-budget check passed:
window 1,227 ms, voice usability 16,222 ms, app interactive 15,583 ms.

Native menu keystrokes are not synthesized. Unsupported interactions, unavailable
transcripts, credentials or additional authorization require an explicit blocker.
The event-driven path reacts after the existing IDE detector observes a change;
it does not promise zero latency or interruption of an already-busy owner chat.

## Remaining live acceptance

This worktree has not been landed in the running app. No desktop restart, quit,
push or release was performed.

Native Windows, macOS and Linux CLI sessions and a fresh install with one
arbitrary supported key still need live acceptance. The cross-platform shared
path is covered with fake PTYs; that is not a claim of native OS verification.

After integration, exercise a native-brain society chat and the Codex test
agent's subscription chat: discover connected CLIs, approve opening a temporary
project, approve a small coding assignment, inspect recorded context, then send
a follow-up. Switch the visible IDE tab during delivery and confirm that work
stays in the original project. Verify an approval card and a denied capability,
and run the same request from an owner-bound routine. Use the CLI's existing
account and model selection. Do not enter credentials into a chat.

An accepted delivery only proves submission. A pending/uncertain receipt is
never automatically replayed; inspect the existing IDE sessions before deciding
whether a new request is appropriate. Transcript context exposes only what the
provider recorded, including available reasoning notes or summaries.
