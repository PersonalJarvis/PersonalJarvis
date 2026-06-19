# CLI First-Class Capabilities — Design

**Date:** 2026-06-10
**Status:** Approved (maintainer delegated open decisions to the recommended defaults)
**Scope:** Make connected CLIs automatically discoverable, selectable, and honestly reported capabilities of the router brain — without changing how CLI tools execute.

---

## 1. Problem

The user asked "Was steht heute noch an?" (calendar query). Jarvis answered "nothing on your calendar" although **no calendar integration existed**. A Google-Workspace-capable CLI was available in the CLI catalog, but Jarvis neither considered it nor admitted it had no calendar access.

Two independent gaps caused this:

- **Gap A — capability knowledge.** Connected CLIs already become real router tools (`cli_<name>` via the `cli-tools` virtual loader, `jarvis/clis/loader.py`), but they are the *only* tool class that never registers in the `CapabilityRegistry` (`jarvis/core/capabilities.py`). The deterministic pre-brain gates (`resolve_intent`, `has_action_intent`, `local_action_gate`) therefore cannot know "calendar → my workspace CLI can do that", and `_build_system_prompt` never tells the brain which CLIs are connected and what they are for.
- **Gap B — honesty obligation.** The existing truth guard in the router system prompt (`jarvis/brain/router.py`, "WAHRHEITS-PFLICHT") only fires when a tool *was called* and failed. When no tool is called at all, nothing stops the LLM from inventing external data ("your calendar is empty"). Gap B exists even with zero CLIs installed.

## 2. Goals

1. Connected CLIs register their abilities in the central `CapabilityRegistry` (new `source="cli"`), live-following the connect/disconnect lifecycle.
2. The brain's system prompt lists connected CLIs with a one-line ability summary so the LLM can pick them for matching requests.
3. Questions about **evidence-required domains** (calendar, email, tasks/appointments, repos/PRs, deployments) are never answered from the model's head: either a matching capability exists (→ mandatory-tool directive for this turn) or Jarvis gives a deterministic, honest refusal that names the gap and offers to connect a matching CLI.
4. Deterministic path preference when several routes could serve a domain: **native router tool/plugin > paired skill > MCP > CLI > worker spawn**. No LLM dice-rolling.
5. Read-only CLI commands run inline in the turn under the existing risk-tier machinery; the seed catalog gains curated read-only whitelists so they execute as `safe` without confirmation fatigue.

## 3. Non-goals

- No new execution layer. `CliTool` (`jarvis/clis/tool.py`), risk integration, usage log, prober, auth, installer stay as they are.
- No per-CLI SKILL.md pairing (rejected: second source of truth next to `CliSpec` → enum/metadata drift, the project's recurring bug class #2).
- No CLI-as-harness / worker-only execution (rejected: 30–120 s latency for a read-only query; contradicts the router rule that read-only queries answer inline in the same turn).
- No PATH auto-scan for unknown CLIs (future idea, out of scope).
- No mid-execution offload of long-running CLI calls in v1 (see §10; a follow-up wave may reuse the Computer-Use offload pattern).

## 4. Architecture decisions

- **AD-CLI1** CLIs remain plain router tools. The new work is metadata + gating only: a capability declaration per catalog entry, a provider that mirrors connect/disconnect into the `CapabilityRegistry`, a system-prompt section, and an evidence gate. One source of truth: `CliSpec`.
- **AD-CLI2** `Capability.source` gains the literal `"cli"`. Capability IDs are `cli.<spec.name>` (one capability per CLI, not per subcommand).
- **AD-CLI3** Capabilities are registered only while the CLI is **usable** (installed AND auth `connected` or auth type `none`/`config_file` — same predicate as `CliToolRegistry.bootstrap`). Register/deregister rides the existing `refresh_status()` transition that already publishes `BrainToolsChanged`; no new event type.
- **AD-CLI4** The evidence gate is **regex/keyword only, pre-brain, no LLM call** (same discipline as smalltalk/local-action gates; AP-9/AP-11 compliant — it must add microseconds, not milliseconds, to the voice path).
- **AD-CLI5** Evidence-required domains start small and configurable: `calendar`, `email`, `tasks`, `repos`, `deployments` under `[brain.evidence_domains]` in `jarvis.toml`. Each domain carries DE+EN keyword lists (umlaut-normalised matching, identical to capability matching).
- **AD-CLI6** Domain resolution preference is a fixed source ordering, data not prompt: `router_tool` > `skill` (paired) > `mcp` > `cli` > `harness`. Implemented in the gate's domain→capability resolution, not by changing `resolve_intent` scoring (which other callers depend on).
- **AD-CLI7** When a domain has **no** capability, the gate returns a deterministic honest refusal through the existing `UNSUPPORTED` path style: it states the missing access, and — when the catalog shows a matching CLI as installed-but-not-connected or known-but-not-installed — proactively offers to set it up (and may suggest navigating to the CLI section). The refusal text is built from i18n-safe templates, goes through `scrub_for_voice` like every spoken path.
- **AD-CLI8** When a domain **has** a capability, the gate does not execute anything itself; it injects a mandatory-tool directive into the turn context: "This is a <domain> question. You MUST call <tool> before answering. If the call fails, say so — never invent data." Execution stays in the normal dispatcher → `ToolExecutor` path (AP-3 respected).
- **AD-CLI9** Seed catalog entries gain an optional `capabilities` block (domains, DE+EN verbs, objects, one-line description) and curated read-only `whitelist_patterns`. Entries without the block simply do not register capabilities — fully backward compatible, custom CLIs opt in via the same JSON fields.
- **AD-CLI10** Inline latency: read-only CLI calls run inline with the existing per-call timeout (default 60 s). v1 accepts this; the announced follow-up (Wave 5, optional) converts calls exceeding an `inline_budget_s` (default 8 s) into the existing completion-announcement pattern used by Computer-Use offload.

## 5. Components

### 5.1 `CliSpec.capabilities` (extend `jarvis/clis/spec.py` + `seed_catalog.json`)

```python
@dataclass(frozen=True)
class CliCapabilityDecl:
    domains: tuple[str, ...]      # e.g. ("calendar", "email") — free-form, matched against evidence domains
    verbs: tuple[str, ...]        # DE+EN action verbs, lowercase, umlaut-normalised by the registry
    objects: tuple[str, ...]      # DE+EN domain nouns
    description: str              # one English sentence for the prompt section
```

`CliSpec` gains `capabilities: tuple[CliCapabilityDecl, ...] = ()`. Pydantic load model (`CliSpecModel`) mirrors it. Seed catalog: curate the block for the relevant subset first (gh, glab, gcloud, vercel, netlify, flyctl, railway, render, heroku, supabase, firebase, stripe, docker, kubectl, aws, az, wrangler, pscale, neonctl, twilio); a Google-Workspace CLI (e.g. GAMADV/gam) can be added as a catalog entry with `domains=("calendar","email")` either as seed or custom JSON.

### 5.2 Capability provider (`jarvis/clis/capability_provider.py`, new)

- `capabilities_for(spec) -> list[Capability]`: maps each `CliCapabilityDecl` to a `Capability(id=f"cli.{spec.name}", source="cli", verbs=…, objects=…, risk_tier=spec.risk.default_tier, requires_evidence=True)`. One capability per CLI; multiple decls merge verbs/objects/domains.
- `sync_registry(cli_registry, capability_registry)`: registers capabilities for all currently usable CLIs, deregisters the rest. Idempotent.
- Wiring: called once after `CliToolRegistry.bootstrap()` and again inside the existing usable/unusable transition in `refresh_status()` (`jarvis/clis/registry.py:156-172`) — the same place that publishes `BrainToolsChanged`.
- Domain membership is carried alongside (module-level map `cli_name -> domains`) so the evidence gate can resolve domain→capability without changing the `Capability` dataclass shape beyond the new `source` literal. (If a `domains` field on `Capability` proves cleaner during implementation, that is an acceptable deviation — Python-only vocabulary, no five-layer enum needed.)

### 5.3 System-prompt section (`jarvis/brain/manager.py::_build_system_prompt`)

New section rendered next to the existing capability-registry block:

```
CONNECTED CLIS
You have direct command-line tools for these connected services. Prefer them for
matching requests instead of refusing or spawning a worker:
• cli_gh — GitHub: repos, PRs, issues (read-only: `gh pr list`, `gh issue list`)
• cli_gam — Google Workspace: calendar events, mail (read-only: `gam calendar ... print events`)
Answer ONLY from the tool result. Prefer machine-readable output flags (--json,
--format json) when available.
```

Only connected/usable CLIs appear; the section is omitted when none are. Rendering helper lives in `jarvis/clis/` (e.g. `render_connected_clis_section(registry)`), consumed by `manager.py` via the shared registry — same pattern as `render_available_skills_section`.

### 5.4 Evidence gate (`jarvis/brain/evidence_gate.py`, new)

Pure function, called from `BrainManager.generate()` after the local-action fast path and before brain dispatch:

```python
def check_evidence_domain(text, lang, domains_cfg, capability_registry, cli_status_fn)
    -> EvidenceVerdict  # frozen dataclass
```

Outcomes:
1. **No domain matched** → `PASS` (zero behavioural change for all other turns).
2. **Domain matched + capability exists** → `REQUIRE_TOOL(tool_name, domain)`: manager appends the mandatory-tool directive (AD-CLI8) to the turn context; turn proceeds normally.
3. **Domain matched + no capability** → `HONEST_REFUSAL(text)`: manager returns the deterministic refusal (AD-CLI7) without an LLM call, exactly like the existing `UNSUPPORTED` local-action outcome. The refusal consults the CLI catalog status to offer the concrete next step ("installed but not connected" / "available in the CLI catalog").

Config (`jarvis/core/config.py` + `jarvis.toml`):

```toml
[brain.evidence_domains]
enabled = true
# keyword lists are DE+EN, lowercase; defaults shipped in code, overridable here
calendar = ["kalender", "termin", "termine", "steht heute", "steht morgen", "calendar", "appointment", "schedule"]
email = ["mail", "mails", "e-mail", "email", "posteingang", "inbox", "postfach"]
tasks = ["aufgabe", "aufgaben", "todo", "todos", "task", "tasks", "erledigen"]
repos = ["pull request", "pull requests", "pr", "prs", "issue", "issues", "repo", "repository"]
deployments = ["deployment", "deployments", "deploy-status", "build status"]
```

Matching is word-boundary, umlaut-normalised, and requires a *question/lookup* shape or action verb — pure smalltalk that merely contains "mail" in passing must not trigger (hard negatives in tests). Domains for which another integration already registers a capability (e.g. paired Gmail skill for `email`) resolve via AD-CLI6 ordering, so the gate never hijacks existing working paths.

### 5.5 Read-only whitelists + JSON preference (seed catalog data)

For each curated CLI: `risk.whitelist_patterns` listing read-only commands (`gh pr list*`, `gh issue list*`, `gcloud * list*`, `vercel ls*`, `kubectl get *`, …) so they evaluate to `safe` through the existing `make_cli_patterns_fn` flattening. Destructive patterns stay/extend in `blacklist_patterns`. `tool_schema_examples` get JSON-output variants where the CLI supports them; the prompt section (§5.3) carries the generic "prefer --json" instruction. No code change in the risk layer.

## 6. Data flow (target behaviour)

**"Was steht heute noch an?" — workspace CLI connected:**
smalltalk/local gates pass → evidence gate matches `calendar` → capability `cli.gam` found (no higher-priority source) → `REQUIRE_TOOL` directive injected → brain calls `cli_gam … print events … --format json` → whitelisted read-only → `safe`, runs inline → brain answers only from the JSON result.

**Same question — nothing connected:**
evidence gate matches `calendar` → no capability for the domain → deterministic refusal: "I have no calendar access yet. Your Google Workspace CLI is installed but not connected — say the word and we'll set it up." (spoken via the normal scrubbed path). No LLM involved, no invented calendar.

**"Schick eine Mail an Christoph" — paired Gmail skill active:**
evidence gate matches `email` → AD-CLI6 ordering finds `skill.paired.gmail` before any CLI → existing behaviour unchanged.

## 7. Error handling

- CLI call fails / non-zero exit under a `REQUIRE_TOOL` directive: the directive text explicitly instructs the brain to state the failure (this is the case the existing WAHRHEITS-PFLICHT already covers, since a tool result exists).
- Capability registry unavailable / registry not bootstrapped (headless early boot): gate degrades to `PASS` with a debug log — never blocks the voice path.
- Provider sync exceptions are caught and logged (EventBus subscriber discipline, AP-18); a failed sync never propagates into the turn.
- Refusal templates are static strings (DE/EN selected by reply language), pass through `scrub_for_voice`, never name internal tool slugs aloud.

## 8. Testing

- `tests/unit/clis/test_capability_provider.py` — decl→Capability mapping, usable-only registration, deregistration on disconnect, idempotent sync.
- `tests/unit/brain/test_evidence_gate.py` — the three verdicts; hard negatives ("Danke dir", "wie geht's", utterances merely containing "mail"/"pr" in non-lookup context); DE+EN; umlaut forms; preference ordering (paired skill beats CLI); disabled-flag bypass.
- `tests/unit/clis/test_seed_catalog_capabilities.py` — every curated `capabilities` block parses, verbs/objects non-empty, domains ⊆ documented domain vocabulary (parity guard against typo-drift).
- Extend `tests/integration/test_cli_integration.py` — connect transition registers capability + prompt section appears; disconnect removes both.
- Regression: full `pytest tests/unit/brain/test_routing.py` (router discipline untouched), `tests/unit/brain/test_output_filter.py`.

## 9. Rollout / compatibility

- `enabled = false` in `[brain.evidence_domains]` reverts to today's behaviour entirely (gate returns PASS, prompt section still renders — the section alone is harmless and strictly additive).
- No ROUTER_TOOLS change, no new entry points, no DB/wire-format vocabulary (no five-layer enum obligation), no pyproject edits.
- Cloud-first: everything is plain Python + subprocess, runs identically on the €5 VPS; CLIs that aren't installed simply never register.

## 10. Follow-up (explicitly out of v1)

- **Wave 5 (optional):** inline budget offload — CLI calls exceeding `inline_budget_s` (default 8 s) finish in the background and report via the existing completion-announcement path (Computer-Use Wave-4 pattern).
- PATH auto-scan suggesting uncatalogued CLIs.
- Per-domain telemetry: count honest refusals per domain to surface which integration the user actually needs.
