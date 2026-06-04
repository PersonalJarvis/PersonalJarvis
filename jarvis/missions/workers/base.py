"""WorkerProtocol + SpawnedWorker — structural contracts.

Follows the Phase-0 plugin pattern (`jarvis/core/protocols.py`):
runtime_checkable Protocols instead of inheritance, frozen dataclasses for
data containers. Every `WorkerProtocol` implementation is checked structurally,
not via MRO — this allows third-party workers without importing Jarvis.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, AsyncIterator, Literal, Protocol, runtime_checkable

CliKind = Literal["claude", "codex", "python", "browser"]


@dataclass(frozen=True)
class SpawnedWorker:
    """Read-only snapshot of a spawned worker subprocess.

    Returned (or emitted via callback) once `spawn()` has started the
    process and the first stream event (typically `system/init`) has
    delivered the `session_id`. Before `system/init`, `session_id=None`
    is valid — the consumer may supplement the value afterwards by
    reconstructing the dataclass via `dataclasses.replace`.
    """

    worker_id: str
    pid: int
    cli: CliKind
    model: str
    worktree: Path
    session_id: str | None
    log_path: Path


@runtime_checkable
class WorkerProtocol(Protocol):
    """Structural contract for Phase-6 workers.

    Implementations must provide `spawn()` as an async-iterator-yielding
    coroutine. `cli` is a string constant; `worker_id` is set by the
    caller (e.g. `<mission_id>::<step_index>`).
    """

    cli: CliKind

    async def spawn(
        self,
        prompt: str,
        *,
        worktree: Path,
        env: dict[str, str],
        job: Any,
        worker_id: str,
        log_dir: Path,
        **kwargs: Any,
    ) -> AsyncIterator[Any]:
        """Spawn the worker subprocess and yield NDJSON events.

        Args:
            prompt: User prompt for the worker.
            worktree: cwd of the subprocess (T1 worktree path).
            env: complete environment (output of T1 `build_worker_env`).
            job: T1 `WindowsJobObject` instance — `.assign(pid)` is called.
            worker_id: orchestrator-unique ID, included in `WorkerSpawned` event.
            log_dir: directory for tee NDJSON (`stream.jsonl`).
            **kwargs: CLI-specific options (model, max_turns, ...).

        Yields:
            Typed Pydantic events (`ClaudeStreamEvent`/`CodexStreamEvent`).

        Note: The generator closes as soon as the subprocess ends *or*
        a terminal event (`result`/`turn.completed`/`error`) is seen.
        """
        # Required yield so the body is typed as an async generator;
        # at runtime, implementations override the method completely.
        if False:  # pragma: no cover — Protocol body is abstract
            yield None
