"""Phase-6 Worker Isolation: Worktree, Job Object, Worker Env.

Three pillars of the sandbox strategy from ADR-0009 §3 + Research-Doc §C/§E:

- `WorktreeManager` — git worktree per task under
  `<repo_parent>/sub-agents-outputs/<run-dir>/tasks/<NN>__<slug>/workspace/`.
- `WindowsJobObject` — async context manager, JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE,
  no-op fallback on non-Windows.
- `build_worker_env` — strict allowlist plus fixed defaults (NO_COLOR=1,
  PYTHONIOENCODING=utf-8, CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1, CODEX_HOME=...).

No pywin32 imports at module level — everything is lazy-imported in the respective
modules so Linux CI can import the package without pywin32 installed.
"""
from __future__ import annotations

from .env import build_worker_env
from .job_object import WindowsJobObject
from .worktree import SourceCheckoutUnavailableError, WorktreeManager

__all__ = [
    "WindowsJobObject",
    "SourceCheckoutUnavailableError",
    "WorktreeManager",
    "build_worker_env",
]
