"""Conductor — a better canvas for agentic workflows.

Conductor is a **standalone open-source tool** that originated in this
monorepo but is not tied to Jarvis:

- Standalone mode: ``python -m conductor serve`` starts its own
  FastAPI server on port 7777 with SQLite in ``~/.conductor/``.
- Embedded mode: Jarvis imports the package, mounts the router in
  its own FastAPI server, and shows a dashboard view.

It is deliberately not an n8n clone — no drag-and-drop nodes, no graph.
Instead:

- **Jobs are YAML** — git-friendly, copy-pasteable, diffable.
- **Timeline view** — all runs chronological instead of spatial.
- **Three job types**: shell, http, agent. That covers 95% of all
  scheduled-task + agentic-workflow use cases.
- **Built-in observability** — every run has live logs, duration,
  exit code, tokens, cost (if agent) — none of it needs to be
  activated separately.

Public API:
  ``ConductorStore``, ``Runner``, ``Scheduler``, ``Job``, ``Run``, and
  the three ``JobHandler`` implementations.
"""
from __future__ import annotations

__version__ = "0.1.0"

from .core.runner import Runner
from .core.scheduler import Scheduler
from .core.schema import (
    AgentJobSpec,
    CronSchedule,
    HttpJobSpec,
    IntervalSchedule,
    Job,
    JobSpec,
    ManualSchedule,
    Run,
    RunStep,
    Schedule,
    ShellJobSpec,
    WebhookSchedule,
)
from .core.store import ConductorStore
from .core.seed import ensure_seed_jobs, SEED_YAML_DIR

__all__ = [
    "__version__",
    "AgentJobSpec",
    "ConductorStore",
    "CronSchedule",
    "HttpJobSpec",
    "IntervalSchedule",
    "Job",
    "JobSpec",
    "ManualSchedule",
    "Run",
    "RunStep",
    "Runner",
    "Schedule",
    "Scheduler",
    "ShellJobSpec",
    "WebhookSchedule",
    "ensure_seed_jobs",
    "SEED_YAML_DIR",
]
