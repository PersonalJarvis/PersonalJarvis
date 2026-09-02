# jarvis/society — Agent Society backend

The substrate of the agent society (`docs/agent-society/MASTERPLAN.md` §3,
`docs/agent-society/agent-definition.md`). M1 (substrate) and the M2 backend
(canonical chats, approvals, routines) are built; the world (M3), rooms going
live under the scheduler and the voice router tool (M4) are not.

| Module | Role |
|---|---|
| `events.py` | the typed envelope and every enum that crosses layers (parity-tested against `society_schema.sql` and `src/lib/societyApi.ts`) |
| `failure_reasons.py` | the typed vocabulary every refusal speaks, with retry hints |
| `society_schema.sql` / `store.py` | `data/society.db`: roster, append-only board, rooms, knowledge staging, approvals, meta — persist-before-publish |
| `bus.py` | observer fan-out of stored envelopes; a wedged observer can never block an append |
| `roster.py` | agent records: adopt-before-mint by name, one lead (Jarvis), typed validation, NO chat-session column (`society:<agent_id>` is a pure function) |
| `capabilities.py` | ONE catalog over plugins, CLIs, MCP servers, active skills and core tools; dispatch and app-control tools are never granted |
| `focus.py` | deterministic focus + approval-rule derivation from title/description — no model call |
| `rooms.py` | bounded discussions: 2–6 members, ≤3 rounds, ≤10 messages, silence allowed, restart-safe |
| `scheduler.py` | the only path from an ASSIGN to work: kill switch, tier wall, depth ≤ 2, target, budgets, caps; refusals are VETO envelopes |
| `bridge.py` | mission envelopes → board (attributed to the owning agent, else the lead) |
| `approvals.py` | the unattended ask-queue: require > always-allow > tier vs ceiling; expiry parks, never drops |
| `routines.py` | per-agent routines as tagged Automations tasks |
| `surface.py` | the `society` chat surface: per-session hands (grant/focus/deny), the briefing, the ecosystem card |
| `agent_tools.py` | `society_message_agent` (one typed envelope to ONE teammate), `society_wiki_note` (writes only under `society/<agent>/`), `society_shell` (the agent's own contained shell) |
| `shell.py` | the shell backend seam: `LocalBackend` now (local by decision), path containment, caps |
| `browser/` | the agent's browser: one-click venv install of browser-use, the in-venv runner, per-agent persistent profiles + login sessions, `society_browser` |
| `learning.py` | automatic learning: turn digest → skill in the agent's own namespace, `society_run_skill`, promotion to the global registry as a draft |
| `seeds.py` | the starter team (Scout + Archivist) and proposals from connected capabilities |
| `chat_binding.py` | roster row → canonical `agent_chat` session; delivers board envelopes as framed turns |
| `runtime.py` | the lazily built singleton wiring all of it; `current_runtime()` for the surface |

REST: `jarvis/ui/web/society_routes.py` (`/api/society/…`, dynamic CLI `jarvis api society …`).
Contract test: `tests/contract/test_society_substrate.py`. Unit tests: `tests/unit/society/`.

Rules that bind everything here (see CLAUDE.md):

- Nothing initializes on the boot critical path (AP-26): the runtime is built on the first
  REST call or chat binding.
- All writes to the board go through `ToolExecutor.execute()`-gated actions (AP-3); the
  scheduler — trusted Python, not an LLM — is the only component that turns `ASSIGN` events
  into worker spawns, and no spawn tool ever enters an agent tool set (AP-5/AP-14).
- Enums crossing Python ↔ SQL ↔ Pydantic ↔ TS ↔ UI use the five-layer pattern plus a parity
  test (AP-4): add a member in `events.py` first, then follow the parity test.
- Hard no-harm rule: no capability in this package may message, probe, or act on
  non-consenting external parties at scale.

Frontend seam: `src/components/society/data.ts` swaps its sample roster for
`GET /api/society/agents` (rows are `SocietyAgentRow` in `src/lib/societyApi.ts`, snake_case).
