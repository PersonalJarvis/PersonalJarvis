# Phase 6 — Worker-Layer (Prompt 2)

**Date:** 2026-04-26
**ADR:** [`docs/adr/0009-self-healing-worker-critic.md`](adr/0009-self-healing-worker-critic.md)
**Research doc:** `SubAgentenSt/Unbenanntes Dokument (4).md` §B (Claude-CLI), §C (Job Objects), §E (worktree layout)
**Branch:** `phase6-self-healing`
**Status:** **BUILT** — `jarvis/missions/isolation/` (T1) + `jarvis/missions/workers/` (T2) are live, smoke tests green.

## What is wired up here?

An **out-of-process worker subprocess** (`openclaw agent` or `codex exec --json`) runs in its own `git worktree`, embedded in a Windows Job Object with `JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE`. The NDJSON stdout pipe is parsed into typed Pydantic events and streamed to the orchestrator as an `AsyncIterator`.

Phase 6 is **additive** to Phase 5 — the Phase-5 `SubJarvisManager` (`jarvis/brain/sub_jarvis.py`) stays untouched. The new mission pipeline (Prompt 1+) only dispatches to the worker layer for ask/multi-step/repair/refactor.

```
              ┌─────────────────────────────────────────────────────────┐
              │ MissionManager (Prompt 1, jarvis/missions/manager.py)   │
              │  state: PENDING → RUNNING → CRITIQUING → APPROVED|FAIL  │
              └────────────────┬────────────────────────────────────────┘
                               │ dispatch
                               ▼
        ┌──────────────────────────────────────────────────────┐
        │ Spawn phase                                          │
        │   1. WorktreeManager.create(mission, task)           │
        │      → sub-agents-outputs/<run>/tasks/<NN>/workspace │
        │   2. async with WindowsJobObject('mission-id') as J  │
        │   3. OpenClawWorker().spawn(prompt, ...)           │
        │      → asyncio.create_subprocess_exec (no shell, no  │
        │        PTY) with CREATE_BREAKAWAY_FROM_JOB           │
        │   4. J.assign(proc.pid)  ← critical moment           │
        └──────────────────────┬───────────────────────────────┘
                               │
            ┌──────────────────▼──────────────────┐
            │ Job Object (KILL_ON_JOB_CLOSE)      │
            │  ┌─────────────────────────────┐    │
            │  │ Worker-Subprocess (claude)  │    │
            │  │   ├─ MCP-Server (node)      │    │
            │  │   ├─ Bash-Tool (cmd.exe)    │    │
            │  │   └─ pip/git/...            │    │
            │  └─────────────────────────────┘    │
            └──────────────────┬──────────────────┘
                               │ stdout NDJSON
                               ▼
        ┌──────────────────────────────────────────────────────┐
        │ Stream-Consumer (jarvis/missions/workers/            │
        │   stream_consumer.py)                                │
        │   read_ndjson_stream(stdout, parser=…, tee=stream.   │
        │   jsonl) → ClaudeStreamEvent (Pydantic v2)           │
        └──────────────────────┬───────────────────────────────┘
                               │ AsyncIterator yield
                               ▼
        ┌──────────────────────────────────────────────────────┐
        │ Orchestrator-Subscriber → EventBus → SQLite WAL      │
        │   (Action/Observation invariant, ADR-0009 §1)        │
        └──────────────────────────────────────────────────────┘
```

## Subdir structure

### `jarvis/missions/isolation/` (T1)

| File | Purpose |
|---|---|
| `worktree.py` | `WorktreeManager` — `git worktree add -b agent/<task>` with a path-length cap (200 chars) and an idempotent `remove(force=True)`. |
| `job_object.py` | `WindowsJobObject` — factory returns a real Win32 wrapper (pywin32 lazy import) on Win32, otherwise `_NoOpJobObject`. async context manager, `assign(pid)` API, `KILL_ON_JOB_CLOSE | BREAKAWAY_OK` flags. |
| `env.py` | `build_worker_env(run_dir, ...)` — strict allowlist (`PATH`, `SystemRoot`, `TEMP`, `USERPROFILE`, `LOCALAPPDATA`) plus FIX defaults (`NO_COLOR=1`, `PYTHONIOENCODING=utf-8`, `CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1`, `CODEX_HOME=<run>/.codex`). NO `os.environ` inherit. |

### `jarvis/missions/workers/` (T2)

| File | Purpose |
|---|---|
| `base.py` | `WorkerProtocol` (runtime_checkable) + `SpawnedWorker` (frozen dataclass). Structural contracts like the Phase-0 plugins. |
| `openclaw_worker.py` | `OpenClawWorker` — wraps `openclaw agent ... --output-format stream-json --bare`. Spawns with `CREATE_NO_WINDOW | CREATE_NEW_PROCESS_GROUP | CREATE_BREAKAWAY_FROM_JOB`, immediately calls `job.assign(pid)`. |
| `codex_worker.py` | `CodexWorker` — wraps `codex exec --json --sandbox workspace-write --ask-for-approval never`. Per-worker `CODEX_HOME=<run>/.codex`. |
| `stream_consumer.py` | `read_ndjson_stream()` — async line-buffered reader with tee to `<log_dir>/stream.jsonl`. Parsers `parse_claude_stream_json` and `parse_codex_stream_json` return Pydantic v2 events. |
| `supervisor.py` | `WorkerSupervisor` — done/stuck/waiting detection with 5 signals (process-exit, `result` event, `api_retry` event, 90 s idle timeout, 900 s hard cap). |

## Concrete command lines

**Jarvis-Agent Worker** (see `_build_claude_cmd` in `openclaw_worker.py:63`):

```bash
openclaw agent "<prompt>" \
  --output-format stream-json \
  --verbose \
  --include-partial-messages \
  --allowedTools Read,Edit,Write,Bash,Grep,Glob \
  --permission-mode dontAsk \
  --max-turns 20 \
  --model sonnet \
  --bare \
  [--resume <session_id>]
```

`--max-turns` is cost guardrail #1 — the smoke sets it to `3`, because "Erstelle hello.txt" ("Create hello.txt") is trivial.

**Codex-Worker** (see `codex_worker.py`):

```bash
codex exec --json \
  --sandbox workspace-write \
  --ask-for-approval never \
  --model gpt-4o \
  "<prompt>"
```

`CODEX_HOME=<run>/.codex` via ENV — prevents cross-talk between parallel missions.

## Gotchas

- **`claude.cmd` shim on Windows:** `shutil.which("claude")` returns the shim path on Windows (`%USERPROFILE%/.local/bin/claude.cmd` or `claude` without the `.cmd` suffix). `asyncio.create_subprocess_exec` must receive the **exact** executable name — if the PATH lookup returns a `.cmd` wrapper, we are protected against Windows process-resolver quirks because `create_subprocess_exec` itself does a PATH lookup.
- **Long-path cap (200 chars):** worktrees live under `<repo_parent>/sub-agents-outputs/<YYYYMMDDTHHMMSS>__<slug>__<uuid8>/tasks/<NN>__<task>/workspace/`. That fits just under `MAX_PATH=260` with ~60 chars of headroom for file paths. `WorktreeManager.create()` raises `ValueError` on violation.
- **Codex-Auth:** the Codex CLI needs `codex login` once, manually, before the first worker spawn. The worker itself reads `CODEX_HOME/auth.json` — we do not copy or symlink the user auth automatically (privacy tradeoff). If `codex` is missing from PATH, the mission plan falls back to Claude-only (TODO Prompt 3).
- **No PTY:** `openclaw agent` and `codex exec --json` work over pipes — no need for `pywinpty`. Saves 3 MB of Rust dep + a class of bugs (research doc decision point #1).
- **`CREATE_BREAKAWAY_FROM_JOB` is mandatory** on Win32, otherwise `AssignProcessToJobObject` is rejected with `ERROR_ACCESS_DENIED`. The job inheritance from the orchestrator (which is itself not a job member) would otherwise kick in.

## Smoke Tests

| Script | What it checks | SKIP condition |
|---|---|---|
| `scripts/smoke_phase6_p2.py` | End-to-end: worktree → job → Claude-Worker → "Erstelle hello.txt" → file verification → worker dead via `psutil.pid_exists`. | `claude` not in PATH OR `psutil` not installed OR `result.result` contains `"Not logged in"` (claude CLI in the subprocess not authenticated — file verification is skipped, but spawn/stream/reaping were still validated). |
| `scripts/smoke_phase6_p2_jobkill.py` | `WindowsJobObject.close()` kills child + grandchild atomically, `psutil.pid_exists(pid)` == False post-close. | Non-Windows OR `psutil` not installed. |

Both exit 0 in the SKIP case.

## References

- **ADR-0009 §1** (Action/Observation invariant): the `result` event is the only authoritative observation, no LLM narrative.
- **ADR-0009 §3** (Worktree + Job Object): rationale for out-of-process instead of in-process (the Phase-5 `SubJarvisManager` is in-process).
- **ADR-0009 §4** (Cost-Discipline): per-mission $5 + daily $50 hard caps. `MAX_CRITIC_LOOPS=3` is hardcoded, NOT configurable in `[phase6]`.
- **Research-Doc §B** (Claude-CLI): exact argv order.
- **Research-Doc §C** (Windows Job Objects): `KILL_ON_JOB_CLOSE | BREAKAWAY_OK` flags.
- **Research-Doc §E** (worktree layout): `<repo_parent>/sub-agents-outputs/<run>/tasks/<NN>/workspace/`.
- **`jarvis.toml` `[phase6.*]`**: orchestrator caps, budget, models, isolation.

## Open Items for Prompt 3+

- Critic-Loop (Prompt 3): consumes the stream events + `<log_dir>/stream.jsonl` as the evidence source.
- `port_allocator.py`: not included in T2 — comes with Prompt 4 (UI/API), when workers want to expose their own HTTP endpoints (e.g. a Vite dev server for frontend tasks).
- Codex-Worker live test: a smoke script for Codex is still missing — follows once `codex login` is wired up as a setup-wizard step.
- Cleanup policy: currently `cleanup_period_days = 14` (in the Phase-6 plan, not yet exposed in `[phase6.isolation]`). On MissionFailed: keep for forensics, automatic prune after 7 days (proposal, ADR-0009 §"Open").
