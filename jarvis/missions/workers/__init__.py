"""Phase-6 worker layer: spawn + NDJSON stream consumption + supervisor.

Three components:

- `WorkerProtocol` / `SpawnedWorker` — structural contracts for all workers.
- `ClaudeDirectWorker` / `CodexWorker` / `GeminiWorker` — concrete CLI
  wrappers that spawn `claude` / `codex exec --json` / `gemini` as a subprocess
  in a T1 worktree under T1 Job Object reaping. NDJSON output is parsed into
  typed Pydantic events and yielded as an AsyncIterator.
- `WorkerSupervisor` — done/stuck/waiting detection with 5 signals
  (process exit, result event, api_retry event, idle timeout, hard wall-clock).

Action/Observation invariant (ADR-0009 §1): stream events are the
*observations* — signed by the runtime, never paraphrased from LLM narrative.

Constraints (ADR-0009 §3 + Research-Doc §C):
- NO shell=True (cmd.exe would itself be a console app and destroy invisibility).
- NO PTY — worker CLIs communicate over pipes.
- pywin32 imports are lazy in the relevant branches.
"""
from __future__ import annotations

from .base import SpawnedWorker, WorkerProtocol
from .codex_worker import CodexWorker
from .stream_consumer import (
    ClaudeApiRetry,
    ClaudeAssistantMessage,
    ClaudeResult,
    ClaudeStreamDelta,
    ClaudeStreamEvent,
    ClaudeSystemInit,
    ClaudeUserMessage,
    CodexError,
    CodexItem,
    CodexStreamEvent,
    CodexThreadStarted,
    CodexTurnCompleted,
    CodexTurnFailed,
    CodexTurnStarted,
    parse_claude_stream_json,
    parse_codex_stream_json,
    read_ndjson_stream,
)
from .supervisor import WorkerState, WorkerSupervisor

__all__ = [
    # base
    "SpawnedWorker",
    "WorkerProtocol",
    # workers
    "CodexWorker",
    # stream_consumer
    "ClaudeApiRetry",
    "ClaudeAssistantMessage",
    "ClaudeResult",
    "ClaudeStreamDelta",
    "ClaudeStreamEvent",
    "ClaudeSystemInit",
    "ClaudeUserMessage",
    "CodexError",
    "CodexItem",
    "CodexStreamEvent",
    "CodexThreadStarted",
    "CodexTurnCompleted",
    "CodexTurnFailed",
    "CodexTurnStarted",
    "parse_claude_stream_json",
    "parse_codex_stream_json",
    "read_ndjson_stream",
    # supervisor
    "WorkerState",
    "WorkerSupervisor",
]
