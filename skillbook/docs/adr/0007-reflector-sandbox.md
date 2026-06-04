# ADR-0007: Recursive Reflector sandbox — subprocess REPL with structured JSON return

**Status:** Accepted
**Date:** 2026-05-26

## Context

The survey emphasizes that the Recursive Reflector's distinguishing innovation is *executing Python code over execution traces* — not paraphrasing them with another LLM call. The "isolated REPL sandbox" must:

- prevent the analysis code from touching the host filesystem outside an allowed read path,
- prevent network access,
- impose a wall-clock timeout,
- return structured data back to the parent process.

## Decision

- Run the analysis as a **child Python subprocess** (`sys.executable -c "<code>"` via `asyncio.create_subprocess_exec`), with:
  - **`stdin` closed**, **`stdout`/`stderr` captured**.
  - **Working directory** set to a per-call temp dir.
  - **Environment scrubbed** to a small allowlist (`PATH`, `PYTHONPATH`, `PYTHONHASHSEED`, `SKB_TRACE_PATH`).
  - **Wall-clock timeout** (default 8 seconds) enforced by `asyncio.wait_for`; killed via `process.kill()` on timeout.
- The analysis code receives the trace as a JSON file referenced by `SKB_TRACE_PATH`. It opens, parses, computes, and writes its verdict as a single JSON line on stdout: `{"verdict": "success" | "failure", "rule": {...} | null, "evidence": "..."}`.
- The parent reads stdout, validates against a pydantic model, and either commits the rule to the skillbook (via Curator) or records the verdict for downstream TTSR.

Restricted execution is **process-level isolation, not bytecode-level**. We do not use `exec()` in-process with a `builtins` allowlist — that is well-known to be bypassable. A subprocess with limited env and no FS write access is the pragmatic security boundary for trace analysis, where the input is internally generated and the threat model is "Reflector goes haywire and tries to drop a table", not "adversarial user inputs Python".

## Consequences

- Each Reflector invocation costs subprocess startup (~50 ms on a warm interpreter). Acceptable: reflection runs out-of-band of the user-facing path.
- The sandbox can be replaced with a sturdier mechanism (Firecracker microVM, gVisor, WASM) without changing the Reflector's call signature.
- The subprocess writes only to its own temp dir, which the parent removes after the call.

## Alternatives considered

- **`exec()` with restricted builtins**: insecure (well-documented bypasses), rejected.
- **PyPy sandbox**: deprecated upstream.
- **Docker container per call**: too much overhead for the per-task reflection cadence.
