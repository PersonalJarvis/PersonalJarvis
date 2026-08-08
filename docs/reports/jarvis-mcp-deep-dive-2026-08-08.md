# Jarvis MCP Deep Dive

**Audit date:** 2026-08-08
**Scope:** MCP client, Marketplace connectors, mission worker broker, standalone Jarvis MCP server, management surfaces, tests, documentation, and parity with the Jarvis CLI.

## Executive verdict

"Jarvis MCP" is not one product. It is four related systems with different maturity levels:

| Plane | Purpose | Current maturity | Verdict |
|---|---|---:|---|
| Custom MCP client | Connect user-defined stdio or remote MCP servers and expose their tools to the brain | Beta | Useful core with incomplete lifecycle and safety semantics |
| Marketplace MCP runtime | Turn packaged connectors and OAuth/PAT grants into live MCP tools | Production-oriented | Strongest MCP plane; bounded startup and reauthentication logic are present |
| Mission worker broker | Give isolated CLI/API workers a mission-scoped tool surface without handing them connector secrets | Production-oriented | Sound security architecture; SDK v2 migration is the immediate compatibility blocker |
| Jarvis as an MCP server | Let external MCP clients access Jarvis memory and skills | Prototype | Four tools and two resources, no CLI-level parity, no tests, and unsafe HTTP claims |

The repository has a credible MCP foundation, but it does **not** yet have a cohesive "Jarvis MCP" product comparable to the Jarvis CLI. The right end state is a thin, authenticated MCP facade over the same live control plane, policy engine, event bus, and command catalog used by the CLI. The standalone server must not continue as a second, direct writer to Jarvis data.

## 1. What exists today

### 1.1 Jarvis as an MCP client

The custom-server path begins with `mcp.json`, loaded through the portable precedence rules in [`state.py`](../../jarvis/mcp/state.py#L62). The registry intentionally ships no preconfigured servers (`BOOTSTRAP_SERVERS` is empty) and starts enabled entries in parallel ([`registry.py`](../../jarvis/mcp/registry.py#L52)).

The v1 SDK client in [`client.py`](../../jarvis/mcp/client.py#L101) supports:

- local stdio subprocesses;
- legacy HTTP+SSE;
- Streamable HTTP with protected header references;
- a 20-second default call timeout;
- a three-failure, 60-second circuit breaker;
- cached tool discovery and bounded shutdown.

Each discovered tool becomes a Jarvis tool named `<server>/<tool>`. The adapter also registers voice-language capability phrases so tools can be selected through normal brain routing ([`adapter.py`](../../jarvis/mcp/adapter.py#L207)). Desktop and headless startup both create the registry, start enabled servers, register adapters, and publish a `BrainToolsChanged` signal. A per-turn tool-surface fingerprint provides a second reconciliation path ([`tool_surface.py`](../../jarvis/brain/tool_surface.py#L25)).

This means a trusted MCP server can already become available to conversation routing and delegated missions without a Jarvis restart.

### 1.2 Marketplace-backed MCP connectors

The Marketplace has 21 seeded plugins, 14 of which currently declare an HTTP MCP server: GitHub, Supabase, Notion, Slack, Linear, Stripe, Cloudflare, Asana, Todoist, ClickUp, Dropbox, Canva, Airtable, and Cal.com ([`seed_catalog.json`](../../jarvis/marketplace/seed_catalog.json)).

[`plugin_mcp.py`](../../jarvis/marketplace/plugin_mcp.py#L17) converts a connected plugin and its protected token record into an in-process MCP client specification. [`plugin_registry.py`](../../jarvis/marketplace/plugin_registry.py#L169) adds bounded handshake/tool discovery, per-plugin refresh, auth-failure classification, token refresh and retry, `needs_reauth`, and bounded disconnect. The Marketplace also supports PAT, PKCE, device, and hosted MCP discovery/DCR flows.

This is materially more complete than arbitrary `mcp.json`: credentials have a product flow, refresh behavior, liveness state, and recovery UX.

### 1.3 MCP as the worker tool transport

Mission startup assembles the relevant connected Marketplace and custom MCP server IDs, using the same relevance concept as brain routing ([`missions/init.py`](../../jarvis/missions/init.py#L249)). A `WorkerCapabilityInventory` deliberately retains identity, not resolved connector credentials ([`capabilities.py`](../../jarvis/missions/workers/capabilities.py#L38)).

At execution time, the supervisor issues a short-lived grant to an authenticated loopback broker. CLI workers see only a local stdio MCP adapter; API workers call the same binding directly. The broker applies a tool denylist, exact/family grants, payload and TTL limits, cancellation, revocation, approval policy, and outcome-integrity reporting before execution reaches the supervisor gateway ([`worker_tool_broker.py`](../../jarvis/missions/workers/worker_tool_broker.py#L654)).

This is the correct architectural direction: credentials and policy remain in the supervisor, while MCP is only the worker-facing protocol.

### 1.4 Jarvis as an MCP server

The standalone entry point is `python -m jarvis.mcp.server`. It currently exposes:

- `memory_search(query, k)`;
- `memory_recent(limit, role)`;
- `memory_add_fact(fact, category)`;
- `skills_list()`;
- resources `jarvis://core-memory/persona` and `jarvis://core-memory/all`.

The module documentation and server instructions also advertise `skills_run`, but no such tool exists ([`server.py`](../../jarvis/mcp/server.py#L7)). It has no first-class `jarvis mcp serve` command, no dedicated product documentation, no discovery/catalog parity, and no tests.

## 2. Critical findings

### P0 — Secure or suspend standalone HTTP

[`server.py`](../../jarvis/mcp/server.py#L26) says that `JARVIS_MCP_TOKEN` requires bearer authentication, and [`MCPServerConfig`](../../jarvis/core/config.py#L1474) declares an auth-token setting. The implementation never reads or enforces it. HTTP can expose personal memory, including the mutating `memory_add_fact`, with no Jarvis authorization middleware, scope enforcement, rate limiting, or Origin validation. The CLI accepts a host override, so the risk is not limited to localhost.

The current MCP transport specification requires Origin validation, recommends localhost binding for local servers, and recommends authentication for all Streamable HTTP connections. Protected HTTP resources use OAuth 2.1 discovery, per-request bearer tokens, audience validation, and 401/403 semantics ([Streamable HTTP security](https://modelcontextprotocol.io/specification/2026-07-28/basic/transports/streamable-http), [authorization](https://modelcontextprotocol.io/specification/2026-07-28/basic/authorization)).

Until those properties exist, HTTP mode should be disabled or explicitly experimental and localhost-only. Stdio can remain the local development surface.

### P0 — Stop bypassing Jarvis ownership and safety

The standalone server opens the recall database and `CoreMemory` directly rather than calling the running Jarvis service. `CoreMemory` explicitly assumes a single process and writes its JSON file directly ([`core_memory.py`](../../jarvis/memory/core_memory.py#L36)). Running desktop Jarvis and the MCP server together can create stale reads, lost updates, or file corruption. Calls also bypass the REST boundary, `ToolExecutor`, safety tiers, audit trail, event bus, and live brain state.

The MCP server must become an adapter to the running control plane. It must never be an independent data owner.

### P0 — Apply conservative per-tool policy

All arbitrary MCP tools currently inherit one default risk tier, normally `monitor` ([`adapter.py`](../../jarvis/mcp/adapter.py#L313)). A remote server can therefore advertise `delete`, `send`, `publish`, or payment-like operations without a per-tool confirmation requirement.

Modern MCP annotations include read-only, destructive, idempotent, and open-world hints, but the specification says clients must treat them as untrusted unless the server is trusted ([tool specification](https://modelcontextprotocol.io/specification/2026-07-28/server/tools)). Jarvis should combine server trust, annotations, local name/schema heuristics, explicit user overrides, and existing blacklist/whitelist policy. Unknown or destructive-looking tools should default to `ask`, never gain authority from an annotation alone, and always execute through `ToolExecutor`.

## 3. Correctness and lifecycle gaps

1. The chat/voice `manage-mcp-server` tool calls the nonexistent `MCPRegistry.active_names()` method. Disable does not stop a live client, remove can leave a running client and registered tools behind, and enable persists state before proving the connection ([`manage_mcp_server.py`](../../jarvis/plugins/tool/manage_mcp_server.py#L250)). Existing tests cover only unknown-name responses.
2. Raw REST config reload overwrites/adds registry specs but does not reconcile removed or changed entries. Delete removes the client/spec without removing tool adapters or publishing `BrainToolsChanged`; capability entries have no deregistration path ([`mcp_routes.py`](../../jarvis/ui/web/mcp_routes.py#L472)).
3. `/config/info` reads the project-root constant while saves use the resolved active path. With `JARVIS_MCP_CONFIG`, `JARVIS_DATA_DIR`, or per-user fallback, the UI can display a different file than it edits ([`mcp_routes.py`](../../jarvis/ui/web/mcp_routes.py#L453)).
4. Start failures are represented inside a nominally successful response because the registry swallows connection failures. Fresh `check` has no route-level timeout. Headless autostart silently discards startup, adapter, and event errors ([`launcher.py`](../../jarvis/ui/web/launcher.py#L695)).
5. URL-only SSE entries are accepted by the management schema but are not reliably loaded by the registry. Legacy HTTP+SSE is deprecated; new work should target stdio and current Streamable HTTP.

These should be fixed with one idempotent registry operation: **reconcile desired config to live state**, returning per-server outcomes and one atomic tool/capability/event delta.

## 4. Protocol and SDK gap

The project pins `mcp>=1.28.1,<2` ([`pyproject.toml`](../../pyproject.toml#L190)). That was a valid security-floor stabilization, but SDK v2 is now the stable line and v1 is maintenance-only. The current protocol revision is 2026-07-28 ([Python SDK v2 changes](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/whats-new.md), [current MCP specification](https://modelcontextprotocol.io/specification/2026-07-28)).

The immediate blocker is [`broker_stdio.py`](../../jarvis/missions/workers/broker_stdio.py#L65), which uses the rebuilt low-level v1 `Server` decorator API. Migration also affects the client lifecycle, `FastMCP` rename, snake_case SDK fields, Streamable HTTP streams, validation behavior, errors, and result types.

Even before v2, Jarvis currently discards or omits important protocol data:

- only the first `tools/list` response is cached; there is no pagination or cache TTL handling;
- no `tools/list_changed` subscription refreshes the live surface;
- tool title, icons, output schema, annotations, and metadata are dropped;
- structured content, resource links, embedded resources, and output validation are flattened;
- modern `input_required` user interaction is not bridged into Jarvis approvals/forms.

The v2 migration needs a contract matrix across stdio, Streamable HTTP, Marketplace OAuth refresh, worker broker, Windows, macOS, Linux, and headless `python:3.11-slim`; it should not be a dependency-only bump.

## 5. Management UX versus Jarvis CLI

The curated CLI group currently offers `list`, `enable`, `disable`, `check`, `import-claude-desktop`, and `delete` ([`mcps.py`](../../jarvis/cli_ctl/commands/mcps.py#L12)). Remaining REST operations are technically reachable through the generated `jarvis api` group. The MCP view offers switches, Claude Desktop import, and a raw JSON editor, but it does not surface the returned tool list, tool risk, scopes, server trust, a visible check action, safe add/delete flows, or generic credential onboarding ([`McpsView.tsx`](../../jarvis/ui/web/frontend/src/views/McpsView.tsx#L22)). Import is Windows-only.

The more important parity gap is opposite-facing: the Jarvis CLI exposes the live REST/OpenAPI control plane, while the Jarvis MCP server hand-writes four memory/skill tools. It therefore has neither the breadth nor the architectural guarantees of the CLI.

### Target parity model

Build one `JarvisMCPFacade` over the same command registry and authenticated REST/service layer used by the CLI:

```text
MCP client
    -> stdio or authenticated Streamable HTTP
        -> scope-filtered MCP facade
            -> command catalog / REST application services
                -> ToolExecutor + safety + audit + EventBus
                    -> Jarvis state and integrations
```

Expose a small stable core rather than flooding every model context with hundreds of operations:

- `commands_search(query, risk, capability)`;
- `command_describe(command_id)` with exact input/output schema and danger metadata;
- `command_execute(command_id, arguments)` routed through the existing control plane;
- curated high-frequency read/status tools;
- scoped resources for documentation, health, command catalog, and explicitly authorized memory views.

Dangerous execution must return `input_required` when approval is needed. `tools/list` should be filtered by granted scope and feature availability, deterministic, paginated/cacheable, and emit `list_changed` when the live surface changes. This gives functional CLI parity without duplicating business logic or expanding every operation into the model prompt.

## 6. Recommended delivery plan

### Wave 0 — Safety containment

- Disable unauthenticated standalone HTTP; keep stdio development-only.
- Remove direct writable memory ownership from the MCP process.
- Add server auth, Origin, scope, rate-limit, audit, and unauthorized/forbidden tests before HTTP returns.
- Remove the nonexistent `skills_run` claim and label the current server experimental.

### Wave 1 — SDK v2 compatibility

- Migrate `broker_stdio.py` first, then the shared client and high-level server.
- Preserve the `mcp>=1.28.1,<2` release line until the full contract matrix passes.
- Lift the cap in the same change as cross-platform and malformed-server compatibility tests.
- Implement modern result preservation, pagination, subscriptions, and `input_required` bridging.

### Wave 2 — One live lifecycle

- Add public registry introspection and a single reconcile API; stop using private `_specs`.
- Atomically synchronize clients, tool adapters, capabilities, state, and one brain-change event.
- Fix active config-path reporting, failure HTTP semantics, timeouts, and headless logging.
- Add safe per-tool risk derivation and explicit server trust/override storage.

### Wave 3 — CLI-level Jarvis MCP facade

- Mount an authenticated MCP facade into the existing application, with stdio proxy support.
- Generate discovery/describe/execute behavior from the command/OpenAPI registry.
- Route every action through existing safety, approval, audit, language, and event ownership.
- Add CLI commands for `mcp serve/status/doctor` and protocol/version diagnostics.

### Wave 4 — Productization and cleanup

- Replace raw-JSON-first setup with add/check/edit/delete, tool previews, risk/scope review, and protected credentials.
- Make import capability-based and cross-platform or state the unsupported path honestly.
- Delete or formally retire the broken, unregistered `mcp-remote` harness, obsolete bootstrap/setup selector, stale worker-secret comments/probes, and the unreachable "First MCP Connection" achievement trigger.
- Add a single product document distinguishing custom MCPs, Marketplace MCPs, worker brokerage, and Jarvis-as-server.

## 7. Verified current state

The focused MCP suite passed **126 tests** with two deprecation warnings. The CLI coverage and danger-metadata guards passed. Coverage is strongest for the shared client, registry, adapter, Marketplace bridge, and worker broker. There are no tests for `jarvis.mcp.server`, and the live management lifecycle, config reconciliation, capability deregistration, HTTP authentication, Origin defense, and modern protocol features are not covered.

### Definition of done

Jarvis MCP reaches CLI-level maturity when:

1. no MCP process directly owns mutable Jarvis data;
2. every HTTP call is authenticated, origin-checked, scoped, rate-limited, and audited;
3. every action uses the same safety/execution boundary as UI, voice, REST, and CLI;
4. config changes cannot leave stale clients, tools, capabilities, or brain prompts;
5. the stable SDK v2 and 2026 protocol contract pass on all supported OS/headless paths;
6. external clients can discover and safely execute the live command surface without a parallel hand-maintained API.
