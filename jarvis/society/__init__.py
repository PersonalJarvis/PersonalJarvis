"""Agent Society — durable named agents that message each other under Jarvis.

The package is the substrate described in ``docs/agent-society/MASTERPLAN.md``
(§3) and ``docs/agent-society/agent-definition.md``:

* ``events``            the typed envelope and every enum that crosses layers
* ``failure_reasons``   the typed vocabulary every refusal and error speaks
* ``store``             ``society.db`` — roster, append-only event log, rooms,
                        knowledge staging, approvals, meta (persist-before-publish)
* ``bus``               in-process fan-out of stored envelopes
* ``roster``            agent records with validation (adopt-before-mint by name)
* ``capabilities``      ONE catalog over plugins, CLIs, MCP servers, skills, core
* ``focus``             deterministic "what this agent reaches for first"
* ``rooms``             bounded group discussions (2–6 members, ≤3 rounds, ≤10 msgs)
* ``scheduler``         trusted Python: tier wall, depth, budget, caps, kill switch
* ``bridge``            mission envelopes → society events (one world feed)
* ``runtime``           lazy singleton wiring the above; nothing runs at boot

Nothing in this package is imported on the boot critical path (AP-26): the
runtime is built on the first REST call or chat binding. Only
``ToolExecutor.execute()`` ever turns an agent's intent into an action (AP-3),
and no spawn-capable tool exists in any society tool set (AP-5/AP-14) — dispatch
is the scheduler's privilege.
"""

from __future__ import annotations

__all__: list[str] = []
