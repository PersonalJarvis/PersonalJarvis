"""Conductor — eine bessere Canvas fuer agentic Workflows.

Conductor ist ein **eigenstaendiges Open-Source-Tool**, das in diesem
Monorepo entstanden ist, aber nicht an Jarvis gebunden ist:

- Standalone-Modus: ``python -m conductor serve`` startet einen eigenen
  FastAPI-Server auf Port 7777 mit SQLite in ``~/.conductor/``.
- Embedded-Modus: Jarvis importiert das Package, mountet den Router in
  seinen eigenen FastAPI-Server und zeigt eine Dashboard-View.

Es ist bewusst kein n8n-Clone — keine Drag-and-Drop-Nodes, kein Graph.
Stattdessen:

- **Jobs sind YAML** — git-freundlich, copy-pasteable, diffbar.
- **Timeline-View** — alle Runs chronologisch statt raeumlich.
- **Drei Job-Types**: shell, http, agent. Das deckt 95 % aller Scheduled-
  Task + Agentic-Workflow-Use-Cases ab.
- **Built-in Observability** — jeder Run hat Live-Logs, Duration,
  Exit-Code, Tokens, Cost (wenn Agent) — nichts davon muss extra
  aktiviert werden.

Public API:
  ``ConductorStore``, ``Runner``, ``Scheduler``, ``Job``, ``Run`` und
  die drei ``JobHandler``-Implementations.
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
