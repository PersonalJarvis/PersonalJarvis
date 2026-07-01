# Live Plugin Hands — Autonomous Plugin Tool-Calling

- **Date:** 2026-06-01
- **Status:** Design approved, pending spec review → implementation plan
- **Topic owner:** maintainer
- **Related:** ADR-0011 (router discipline), `docs/jarvis-agents-bridge.md` (AD-OE1..OE6),
  CLI-Integration spec (`docs/superpowers/specs/2026-05-24-cli-integration-design.md`),
  `docs/anti-drift-three-layer.md`.

---

## 1. Problem

The "Plugins" tab implies that connecting a service (GitHub, Notion, Slack, and
soon Google Calendar) gives Jarvis the ability to *use* it. It does not — not for
the conversational brain. The codebase has **two parallel, unevenly wired
connector systems**:

| | "Plugins" tab (Marketplace) | "MCP Servers" tab |
|---|---|---|
| Code | `jarvis/marketplace/` | `jarvis/mcp/` (`data/mcp.json`) |
| Connect stores | OAuth/PAT token in keyring (`token_store.py`) | server spec in `mcp.json` |
| Wired into the **live brain**? | **No** | Yes (`desktop_app.py:892`, `register_mcp_tools_in_registry`) |
| Wired into the **background worker**? | Yes (`missions/init.py:444`, only on mission spawn) | Yes |

A connected Marketplace plugin reaches **only** the heavy `claude-cli`
`ClaudeDirectWorker` subprocess, assembled at mission-spawn time by
`_assemble_worker_mcp_servers()` (`missions/init.py:81`). The router-brain that
actually holds the conversation never sees the plugin as a callable tool. And the
only way the router reaches the worker — the force-spawn heuristic — runs in
`strict` mode (`manager.py:1552-1554`), firing only on explicit heavy-work
keywords ("spawn", "deep dive", "OpenClaw", "gründliche Recherche") and only when <!-- i18n-allow: German input vocabulary -->
`brain.primary` is `claude-api`/`gemini`. So a casual "was habe ich heute für <!-- i18n-allow: quoted German voice-input example -->
Termine?" produces **nothing**.

**Symptom in the user's words:** "he doesn't actively call these things, and when
he does, he calls them wrong."

**Root cause:** an LLM can only choose a tool that is present in the `tools=`
array of the call it is reasoning in. The plugin tools are not in the talker's
turn; they are in a subprocess woken by a regex.

## 2. Goals / Non-Goals

**Goals**
- A connected Marketplace plugin becomes a first-class, callable tool of the
  **live** router-brain, on both the **voice and chat** paths, with no restart.
- Jarvis decides *autonomously* when a plugin is relevant (model tool-choice, not
  a keyword regex) and calls it *correctly* (right tool, right args).
- Light reads answer **inline in the same turn** (grounded answer in one breath);
  heavy/multi-step jobs still delegate to the worker — the model makes that call.
- Adding a new plugin = catalog entry + MCP server spec + a usage card. Nothing
  else.
- Stay inside the cloud-first / €5-VPS doctrine and the voice-latency budget.

**Non-Goals**
- A full agentic tool-router with semantic tool retrieval and a dedicated
  selection sub-step (Weg C). Premature for ~6 plugins; revisit at 50+.
- Confirmation prompts for writes (the user chose full autonomy).
- Migrating the legacy worker MCP path away — it stays for heavy missions.
- Reworking the "MCP Servers" tab (it already reaches the live brain).

## 3. Decisions captured (from brainstorming)

1. **Two-speed experience** — fast inline reads, worker for heavy actions.
2. **Full write autonomy** — no confirmation prompts; safety via non-blocking
   audit log + reversibility-where-the-API-allows, not nagging.
3. **Architecture = Weg B** — MCP tools (the hands) + a thin per-plugin usage
   card (the knowledge), relevance-gated so the surface stays small.
4. **Voice + chat from Wave 1** — both paths share one `BrainManager`, so "both"
   costs essentially nothing extra.
5. **Usage cards = one `.md` per plugin** under `jarvis/marketplace/usage_cards/`.
6. **Pilot plugin = Google Calendar** — the user's own example, read-heavy,
   ideal for the fast inline path.

## 4. North Star (what "perfect tool calling" means here)

Derived from how professional harnesses (Claude Code, Codex) achieve reliable
tool use. The design is measured against these six properties:

1. Tools live **inline in the turn**; the model decides.
2. The surface is **small & relevant** per turn (progressive disclosure).
3. Each tool is **described for the decision** (when to use, not just what).
4. A **reliability layer per integration** (usage card) teaches correct use.
5. **Two speeds**, but the decision is the model's, not a regex.
6. Tool results **flow back into the same turn** — answers from real data, no
   hallucination.

## 5. Architecture — "Plugins work exactly like CLIs already do"

The anchor: the `cli-tools` virtual-loader (`factory.py:78-87`,
`jarvis/clis/`) already solves this exact shape for command-line tools — only
*connected* CLIs become tools, the surface stays small (1-5), and connecting a
CLI re-expands the **live** brain via `BrainToolsChanged` with no restart
(`factory.py` `manager.attach_to_bus`). We **mirror that pattern** for MCP
plugins instead of inventing new machinery.

### 5.1 `plugin-tools` virtual-loader (the hands)
A new router-tier tool modeled on `cli-tools`. On `expand()` it reads the
Marketplace catalog (`catalog_data.load_catalog`) + keyring (`token_store`), and
for each **connected** plugin:
- constructs an in-process `MCPClient` (`jarvis/mcp/client.py`) against the
  plugin's MCP server, resolving the keyring token into the transport (reusing
  the token→server-spec logic already in `marketplace/mcp_bridge.py`);
- lists the server's tools and wraps each in an `MCPToolAdapter`
  (`jarvis/mcp/adapter.py` — already does risk-tier flow + `CapabilityRegistry`
  registration);
- yields them namespaced as `google-calendar/list_events`, `notion/search`, …
- re-expands the live brain on connect/disconnect via the **same**
  `BrainToolsChanged` live-reload path `cli-tools` uses.

This is a **direct, safe/risk-gated action tool, never a spawn** — it must never
enter any worker tool-set (AP-5/AP-14) and never trigger D9 recursion.

### 5.2 Usage cards (the knowledge — the "MCP + Skill" answer)
One small markdown card per plugin at
`jarvis/marketplace/usage_cards/<plugin_id>.md` (~10-20 lines): when to use, key
tools, gotchas (timezone, pagination, which of N tools for which intent), 1-2
few-shot examples. Loaded and injected into the router system prompt **only when**
that plugin's tools are active in the turn (progressive disclosure → small,
relevant prompt). MCP supplies the hands; the card supplies how to use them well.
This is the lever for "calls it *right*", not just "calls it".

### 5.3 Relevance gate (keep the surface small)
~6 plugins × ~8 tools would explode the surface and *degrade* selection. A cheap
per-turn filter decides which **plugins** are plausibly relevant and injects only
those plugins' tools + cards. **Keyword/capability match only — no LLM call on
the path (AP-9).** It reuses the `verbs`/`objects` the `MCPToolAdapter` already
writes into the `CapabilityRegistry` (`adapter.py:91-103`,
`jarvis/core/capabilities.py`): "Termine heute" → matches the `calendar`/`events`
capability → only Calendar tools enter the turn. Fallback to the full connected
set when nothing matches (better to over-offer than to miss).

### 5.4 Two-speed routing (the "mixed" choice)
Read-ish tools (`list/get/search/read`) are tagged `fast` and execute inline in
the turn → grounded answer in one breath. The model recognizes multi-step or
heavy intents itself and routes them to `spawn-worker` (unchanged). The decision
is the model's, shaped by tool descriptions + usage cards — **no regex**. Because
plugin tools are now inline, the strict force-spawn heuristic that currently
blocks everything is simply bypassed for plugin reads.

### 5.5 Safety & audit (full-autonomy choice)
Every plugin tool runs through `ToolExecutor` + risk-tier (the adapter already
wires this; AP-3 satisfied). No confirmation prompts. Each call emits
`ActionExecuted` → audit log. Reads default `safe`, writes `monitor` (logged).
Tokens stay in the keyring, injected at client construction, **never** in tool
args or descriptions (AP-2). Where the API supports it, deletes/writes are
reversible (e.g. Calendar event recovery) — surfaced, not prompted.

## 6. Data flow — "was habe ich heute für Termine?" <!-- i18n-allow: quoted German voice-input example -->

```
Utterance → router turn
  → Relevance-Gate: "Termine/Kalender" matches the google-calendar capability
  → inject google-calendar/* tools + usage card ("for 'today': user timezone, day range")
  → model calls google-calendar/list_events INLINE
  → MCPToolAdapter → in-process MCPClient → real calendar data back into the turn
  → Jarvis answers from real data, same breath (→ scrub_for_voice → TTS)
```

## 7. Cloud-first / transport handling (doctrine)

Transport-aware and graceful:
- **Remote / HTTP / SSE MCP servers** (e.g. a hosted Calendar MCP) are
  first-class on a headless €5-VPS.
- **stdio / Docker servers** (e.g. the GitHub Docker image) run only when the
  binary is present → gated behind a capability probe, graceful logged no-op
  otherwise (AD-6 pattern).
- The base `pip install` still boots headless on `python:3.11-slim`; the
  `plugin-tools` loader yields nothing when no plugin is connected/usable.

## 8. Adding a plugin becomes trivial (Calendar pilot)

After this build, **add a plugin = catalog entry + MCP server spec + usage
card.** Everything else (live bridge, relevance, routing, audit, voice+chat)
carries automatically. Google Calendar is the first end-to-end proof: a catalog
entry with its (remote, VPS-friendly) MCP server spec + auth mode, plus
`usage_cards/google-calendar.md`.

## 9. Build sequence (4 waves, backward-planned)

**Wave 1 — Live bridge.** `plugin-tools` virtual-loader; in-process
`MCPClient`/`MCPToolAdapter` reuse; token→transport resolution; live-reload on
connect/disconnect; voice + chat (shared `BrainManager`).
*Acceptance:* a connected plugin's tools appear in the live router surface
without restart; a manual call returns real data inline. No worker spawn needed.

**Wave 2 — Relevance gate.** Capability-based per-turn plugin filter (no LLM);
full-set fallback; surface stays small (assert ≤ N injected per turn).
*Acceptance:* with 3+ plugins connected, an unrelated utterance injects 0 plugin
tools; a calendar utterance injects only Calendar.

**Wave 3 — Usage cards.** Card schema + loader + relevance-gated prompt
injection; author cards for the existing catalog plugins + Calendar.
*Acceptance:* the Calendar card is present in the prompt only on calendar turns;
a "today" query uses the correct day-range + timezone.

**Wave 4 — Two-speed + polish.** Fast-read tagging; router-prompt framing for the
inline-vs-delegate decision; audit visibility; Plugins-tab UI badge
("live-callable" vs "worker-only / transport-gated") so the UI stops over-
promising.
*Acceptance:* a read answers inline; a heavy multi-step request delegates to the
worker; the Plugins tab honestly reflects callability.

**Google Calendar plugin** rides on Wave 1 (catalog + spec) and Wave 3 (card).

## 10. Anti-patterns to respect

- **AP-2** — no secrets in the tool surface; tokens stay in keyring.
- **AP-3** — only `ToolExecutor.execute()` (the adapter already enforces this).
- **AP-5 / AP-14** — `plugin-tools` is a direct safe-gated tool, never a spawn;
  it must not enter any worker tool-set or resurrect a sub-tier.
- **AP-9** — the relevance gate is keyword/capability only; no LLM/IO on the
  voice path.
- **Five-layer enum pattern** (`docs/anti-drift-three-layer.md`) for any new
  wire-format vocabulary (e.g. a plugin-tool tier/kind) + parity test.
- **Cloud-first** — transport-gated; headless base install still boots.

## 11. Risks / open questions for the implementation plan

- **Exact merge point** into the live `BrainManager._tools`: pin that
  `plugin-tools` rides the *same* `BrainToolsChanged`/`refresh_tools` path
  `cli-tools` uses (the `app.state.tool_registry`/dispatcher path used for
  `mcp.json` is a separate, murkier route — do not rely on it).
- **In-process MCP client lifecycle**: starting/stopping clients on
  connect/disconnect without leaking subprocesses; reuse `MCPRegistry`
  lifecycle where possible.
- **Calendar MCP server choice**: confirm a remote/HTTP (VPS-friendly) Google
  Calendar MCP server + its auth mode for the catalog entry.
- **Relevance precision**: tune the capability verbs/objects so "calendar"
  intents match without false negatives; document the full-set fallback.

## 12. Testing strategy

- Unit: `plugin-tools` expansion (connected → tools, none → empty); relevance
  gate (match/no-match/fallback); usage-card loader + injection gating.
- Contract: each `MCPToolAdapter`-wrapped plugin tool passes the Tool contract.
- Integration: connect a fake plugin → tool appears in the live brain surface →
  call returns data inline; disconnect → tool gone, no restart.
- E2E (pilot): "was habe ich heute für Termine?" → Calendar read inline, on both <!-- i18n-allow: quoted German voice-input example -->
  voice and chat paths, through `scrub_for_voice`.
- Regression: parity test for any new wire enum; assert `plugin-tools` never
  enters a worker tool-set (AP-5/AP-14 guard).
