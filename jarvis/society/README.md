# jarvis/society — Agent Society backend (scaffold)

Not built yet. This directory reserves the home for the agent-society substrate planned in
[`docs/agent-society/MASTERPLAN.md`](../../docs/agent-society/MASTERPLAN.md) (§3, §9): the durable
agent roster, the typed append-only society event log ("blackboard"), the dispatch scheduler with
the tier wall and master kill switch, the mission-event bridge, bounded group rooms, the knowledge
curator, and the unattended approvals queue.

Rules that bind everything landing here (see the master plan and CLAUDE.md):

- No module in this package initializes on the boot critical path (AP-26); everything is lazy.
- All writes to the society board go through `ToolExecutor.execute()`-gated actions (AP-3);
  the scheduler — trusted Python, not an LLM — is the only component that turns `ASSIGN` events
  into worker spawns, and no spawn tool ever enters an agent tool set (AP-5/AP-14).
- `society.db` follows the `missions_schema.sql` conventions: WAL, persist-before-publish,
  append-only events carrying `trace_id`.
- Enums crossing Python ↔ SQL ↔ Pydantic ↔ TS ↔ UI (`tier`, `state`, `msg_type`) use the
  five-layer pattern plus a parity test (AP-4).
- Hard no-harm rule: no capability in this package may message, probe, or act on non-consenting
  external parties at scale; the anti-harm blacklist class is part of M1, not an afterthought.
