"""Shared mission worker-runtime helpers (legacy package name, now generic
mission-runtime infra).

The old subprocess execution path was removed (the maintainer migrated
to the direct Opus worker, ``ClaudeDirectWorker``). What remains here is the
provider-agnostic scaffolding the live worker + critic still depend on:

- `provider_map` — Personal-Jarvis provider-slug + ENV-var mapping. Pure data
  module, no IO. Consumed by the critic + setup wizard.
- `workspace`   — Mission-isolated workspace profile + ``materialize_worker_contract``
  (AGENTS.md materialisation). Pure file IO. Consumed by the Kontrollierer
  orchestrator for every mission worktree.

Both modules still carry a handful of legacy ``openclaw`` literals (the
external worker binary's on-disk paths and a read-time backcompat provider
alias) for historical reasons; they are otherwise generic mission-runtime
infrastructure.
"""
from __future__ import annotations

__all__ = [
    "provider_map",
    "workspace",
]
