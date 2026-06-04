"""Phase-6 Self-Healing Mission Orchestrator.

Subsystem for Worker-Critic loops with worktree isolation, SQLite event store,
and Action/Observation invariant. All components live under
``jarvis.missions.*``. Wave-4 migration: the former ``jarvis.sub_jarvis``
Phase-5 code has been fully removed — Mission Manager + OpenClaw Bridge
are now the only heavy workers (see docs/openclaw-bridge.md §11).

Foundation decisions: docs/adr/0009-self-healing-worker-critic.md
Implementation plan:  docs/phase6-prompt-chain.md
"""
from __future__ import annotations

__version__ = "0.1.0"
