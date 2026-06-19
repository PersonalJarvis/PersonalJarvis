# App-Control Tools — Design Spec

**Date:** 2026-05-31
**Status:** Approved (autonomous build per `/goal` directive)
**Author:** Claude (Opus 4.8)

## 1. Problem

The user wants Personal Jarvis (the brain, via voice/chat) to have a **complete
overview of the Desktop App** and be able to **change its configuration across
all surfaces** by speaking — concretely:

- "What are my current settings / which provider am I on / which MCPs are
  connected?" (overview)
- "Switch the API key from Grok to Gemini" (switch the active provider)
- "Update the MCP JSON with a new MCP server" (add/remove/toggle an MCP server)

## 2. Key finding — the plumbing already exists, brain-side access does not

Read-only exploration (2026-05-31) established that every target surface is
already production-ready, but reachable **only via the web UI**, never via a
brain-callable tool:

| Surface | Existing plumbing | Brain access today |
|---|---|---|
| Provider switching | `jarvis/ui/web/provider_routes.py` — `/api/brain/switch` (live, no restart), `/api/tts/switch`, `/api/stt/switch`, `/api/secrets/{key}` POST/DELETE, `/api/providers` GET | **None** (UI only) |
| MCP servers | `jarvis/mcp/state.py` (`load_config`, `save_config`, `upsert_server`, `remove_server`, `set_enabled`, `get_enabled_names`), `mcp.json`, `mcp_routes.py`, `McpsView.tsx` | **None** (UI only) |
| Settings | `jarvis/ui/web/settings_routes.py` — wake-word, ptt-hotkey, autostart, assistant-name, reply-language | **None** (UI only) |
| Self-mod config | `jarvis/core/self_mod/` + `set_config_value` tool (9-path allowlist incl. `brain.primary`) | **Partial** — but `mcp_server.*` and `*_api_key`/`*_token`/`*_secret` are in `FORBIDDEN_PATTERNS` (registry.py:21-31) and stay forbidden |

So the work is **not** a new subsystem. It is three thin **router-tier tools**
that expose the already-secured logic to the brain.

## 3. Why router-tier tools, not a SKILL.md

The user said "a skill that triggers". In this codebase the established pattern
for "a brain-callable capability that fires when the user expresses an intent"
is a **router-tier tool** registered in `ROUTER_TOOLS`
(`jarvis/brain/factory.py:40`). The `SKILL.md` system
(`jarvis/skills/`) is for *user-authored automations* (Jinja2 + YAML steps,
draft→activate lifecycle). Config mutation is a first-class capability, not a
user automation — so it follows the `set_config_value` / `wiki-ingest` /
`update-profile` precedent: a deterministic router tool, never a spawn, never in
a worker set (AP-5/AP-14).

## 4. The three tools

### 4.1 `describe-app-settings` (safe, read-only)

The "complete overview". Composes a single structured snapshot:

- **Providers**: for brain/tts/stt/subagent — the active provider name, the list
  of available providers, and for each whether its credential is configured
  (`configured: bool`) and (for CLI providers) `cli_installed: bool`. Reuses the
  read logic behind `GET /api/providers`.
- **Settings**: wake word (phrase, engine, sensitivity), assistant name
  (explicit + resolved), autostart (enabled/supported), reply language, ui theme,
  tts voice_de/voice_en/speed, computer_use step budget.
- **MCP servers**: list of `{name, enabled, description, transport}` from
  `mcp.state.load_config()`.

Returns a `ToolResult` whose payload is a JSON object the brain can read back in
prose. **No secret values are ever included** — only booleans
(`configured: true/false`). Risk tier `safe` (no confirmation; read-only).

Schema: empty object, no parameters (mirrors `list_mutable_settings`).

### 4.2 `switch-provider` (ask, echo-confirm)

Switches the **active** provider for a tier. Arguments:

```json
{
  "tier": "brain | tts | stt | subagent",   // required
  "provider": "gemini | grok | ...",          // required
  "reason": "string"                          // required (audit + echo)
}
```

Behaviour:

1. Validate `tier` and `provider` against the known provider registry.
2. Check the target provider's credential is present (the same
   `configured` check `/api/providers` uses). **If missing → return a clean
   error** ("Gemini is not configured — the API key is missing. You can add it
   in the Settings tab.") and make **no** change.
3. Apply via the **same service logic** `/api/brain/switch` (and tts/stt) uses —
   3-layer persist (TOML via `config_writer` + in-memory `cfg` + live runtime
   re-init where supported). Brain and TTS switch live (no restart); STT and
   subagent report `requires_restart: true`.
4. Return `{tier, old_provider, new_provider, persisted, requires_restart,
   applied_live}` — honest outcome flags (AD-OE6: no silent drops).

**Security boundary (binding):** this tool switches the *active provider*. It
**never sets a raw API-key value** — that path stays UI-only
(`/api/secrets/{key}`), per AP-2 (STT log leak = credential exfiltration) and
the self-mod `FORBIDDEN_PATTERNS` doctrine. The user's phrase "switch the API
key from Grok to Gemini" is interpreted as "switch the active provider to
Gemini", which is the correct reading and requires the Gemini key to already
exist.

Risk tier `ask` → the brain echoes "switching brain provider from grok to
gemini — confirm?" before applying (end-focus echo pattern, per self_mod.md §2).

### 4.3 `manage-mcp-server` (ask, echo-confirm)

Add / remove / enable / disable an MCP server in `mcp.json`. Arguments:

```json
{
  "action": "add | remove | enable | disable",   // required
  "name": "string",                                // required
  "command": "string",                             // add only
  "args": ["string"],                              // add only, optional
  "transport": "stdio | http | sse",               // add only, default stdio
  "url": "string",                                 // add only, http/sse
  "description": "string",                          // add only, optional
  "reason": "string"                                // required (audit + echo)
}
```

Behaviour:

- `add` → `mcp.state.upsert_server(name, spec)` with `enabled=false` by default
  (a newly added server is **not auto-started**; the user enables it after
  review — mirrors the skill draft→activate safety stance, AP-15 spirit).
- `remove` → `mcp.state.remove_server(name)`.
- `enable`/`disable` → `mcp.state.set_enabled(name, bool)`.
- On success, attempt to refresh the live registry if one is reachable
  (`app.state.mcp_registry`); if not reachable (e.g. headless brain build before
  server bootstrap), report `requires_restart: true` honestly.
- Return `{action, name, applied, requires_restart}`.

**Security boundary (binding):** any credential an MCP server needs is referenced
as a `$SECRET_NAME` placeholder in `env`/`headers`; the tool **never accepts a
raw secret value** as an argument (AP-2). Adding a server is arbitrary command
execution, so it is `ask` tier and the added server starts **disabled**.

## 5. Wiring

- Add `describe-app-settings`, `switch-provider`, `manage-mcp-server` to the
  `ROUTER_TOOLS` frozenset (`jarvis/brain/factory.py:40`).
- Register entry-points in `pyproject.toml` under
  `[project.entry-points."jarvis.tool"]`, then `pip install -e . --no-deps`.
- The three tools need DI (config path, mcp state module, a provider-switch
  service). Like the self-mod tools and `spawn-worker`, they resolve their
  dependencies lazily / via the shared `cfg` so a brain built before the server
  bootstrap still loads them (they return an honest "not yet ready" otherwise).
- Routing: extend the smalltalk/action heuristics so utterances like
  "aktualisiere die Settings", "welche Provider hab ich", "switch to Gemini",
  "füge einen MCP-Server hinzu" route to these tools and are **not**
  force-spawned to a worker (`BrainManager._should_force_openclaw`). Add a short
  note to `router.py` SYSTEM_PROMPT so the brain knows the overview tool exists.

## 6. Tests

- `tests/unit/plugins/tool/test_app_control_tools.py` — per-tool: schema,
  happy path, missing-credential path (switch-provider), forbidden raw-secret
  rejection, mcp add/remove/enable/disable, honest outcome flags.
- Extend `tests/unit/brain/test_routing.py` — assert the three tool names are in
  `ROUTER_TOOLS` and that the routing heuristic does not force-spawn the example
  utterances.
- Reuse fakes from `tests/fakes/` (no `unittest.mock`).

## 7. Anti-patterns explicitly avoided

- **AP-2** (secrets via voice): no raw key/secret value is ever a tool argument.
- **AP-3** (bypass ToolExecutor): all three go through the normal tool-execute
  path with risk-tier gating.
- **AP-5/AP-14** (spawn tool in worker set): these are direct safe/ask actions,
  never spawns; they stay router-tier only.
- **AP-7** (TOML write hygiene): provider switch persists via `config_writer`
  (lock + tempfile + BOM-safe); MCP writes via `mcp.state.save_config` (atomic).
- **AP-6** (hardcode Claude/Anthropic): provider names come from config, never
  hardcoded.

## 8. Out of scope (YAGNI)

- Setting raw API-key *values* by voice (stays UI-only, security boundary).
- A new React view (the existing `McpsView`/`SettingsView`/provider tabs already
  render the state; live-reload events keep them in sync).
- Extending the self-mod allowlist beyond what these tools cover.
- General "change any arbitrary setting" — the three tools + existing 9-path
  `set_config_value` cover the user's concrete examples; further paths are an
  incremental allowlist edit later if asked.
