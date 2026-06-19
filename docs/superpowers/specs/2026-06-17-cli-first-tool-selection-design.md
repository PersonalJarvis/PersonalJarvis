# CLI-First Tool Selection — Design

- **Date:** 2026-06-17
- **Status:** Approved (design), pending implementation
- **Supersedes / amends:** AD-CLI6 in `docs/superpowers/specs/2026-06-10-cli-first-class-capabilities-design.md` (the evidence-gate "non-CLI capability wins" preference is **inverted** by this design — see Component 2).

## Motivation

Connected CLIs (gcloud, gh, stripe, …) must be used **implicitly, reliably, and
preferentially**, without the user naming the CLI and without hand-curated
per-command guidance. Four maintainer requirements:

1. **Implicit CLI usage** — infer the right CLI from the request ("schau nach,
   was in meiner Google Cloud los ist") and trigger it directly; no "use the
   Google Cloud CLI to …" required.
2. **Generic coverage** — works for *all* connected CLIs, not just gcloud.
3. **Self-service documentation** — the system discovers how to run a command
   from the CLI's own `--help`, not from manual tagging.
4. **CLI over plugin** — when both a CLI and a marketplace plugin exist for a
   service, the CLI always wins; the plugin is a fallback only (CLIs run a
   local subprocess and are cheaper than a plugin's MCP/HTTP/API round-trip).

## Current state (evidence-anchored)

- The **only deterministic forcing path** to a `cli_<name>` tool is the
  **evidence gate** (`jarvis/brain/evidence_gate.py:check_evidence_domain`,
  wired in `jarvis/brain/manager.py:_run_evidence_gate` → called at the
  `generate()` turn). It emits a `require_tool` directive that the system
  prompt turns into a mandatory tool call. The legacy
  `CapabilityRegistry.resolve_intent` path only *suppresses refusals/force-spawn*;
  it never triggers a CLI tool.
- The gate's machinery is **fully generic**, but its trigger vocabulary comes
  from a **hand-curated** keyword list, `[brain.evidence_domains].domains` in
  `jarvis/core/config.py`, which is **decoupled** from the CLIs' own catalog
  `objects`. gcloud (`cloud`) and gh (`repos`) trigger implicitly only because
  those domains happen to be curated there. **Stripe breaks**: it declares
  domain `payments` with objects (`stripe`, `umsatz`, `payment`, `invoice`),
  and `connected_domain_tool_map` would yield `payments → cli_stripe`, but there
  is no `payments` entry in the config → no match → silent fall-through.
- The **only** place a CLI-vs-plugin preference exists is the gate's AD-CLI6
  block (`evidence_gate.py`, the loop that `continue`s past `source == "cli"`
  capabilities and returns PASS when a non-CLI capability covers the domain).
  This favors **plugin over CLI** — the inverse of requirement 4. Pinned today
  by `tests/unit/brain/test_evidence_gate.py::test_non_cli_capability_wins_and_passes`.
- A CLI and a marketplace plugin for the same service (e.g. `cli_gh` + the
  `github` MCP plugin's ~37 tools; `cli_vercel` + the native `vercel` REST tool;
  `cli_stripe` + the `stripe` plugin) **both surface in the tool dict at once**.
  There is no dedup. `plugin_relevance.filter_plugin_tools` only prunes plugin
  tools by keyword once they exceed 12; it never compares against CLIs.
- There is **no help/introspection** anywhere. `tool_schema_examples` are
  hand-curated in `seed_catalog.json`. The model never sees `<cli> --help`.

## Design

Four components, each with one clear purpose and a well-bounded interface.

### Component 1 — Derive implicit-trigger vocabulary from connected CLIs (req 1+2)

**New:** `connected_domain_keyword_map(cli_registry) -> dict[str, list[str]]` in
`jarvis/clis/capability_provider.py`, next to `connected_domain_tool_map`
(both walk the same catalog + active-tool set). For every *usable* CLI, union
its `CliCapabilityDecl.objects` per declared `domain`, normalized via
`_normalize`, minus an ambiguous-bare-noun **denylist**.

**Denylist (starting set, tunable):** generic cost/price nouns that would
hijack unrelated questions — `{"kosten", "cost", "costs", "preis", "preise",
"price", "geld", "money"}`. Applied **only to derived objects**, never to
curated config keywords (so a deliberately-curated word survives).

**Wiring:** in `manager._run_evidence_gate`, build
`domains = merge(derived, cfg.domains)` where, per domain, the keyword set is
`(derived_objects − denylist) ∪ config_keywords`. Config thus becomes an
**override/augment layer**, not the sole source:
- Config-only domains with no backing CLI (calendar/email without a CLI) keep working.
- Curated keywords always win/extend (e.g. the deliberate `cloud` list).
- A newly connected CLI's domain becomes implicitly triggerable from its own
  vocabulary with **zero config edits** (Stripe `payments` now forces `cli_stripe`).

**Degradation:** any fault in derivation returns `{}` → the gate runs on
`cfg.domains` exactly as today.

### Component 2 — Invert the gate preference to CLI-over-plugin (req 4, core)

In `evidence_gate.py`, the AD-CLI6 block is inverted: when an utterance matches
a domain and `domain_tool_map` (CLI-only, from `connected_domain_tool_map`) has
a tool for it, emit `require_tool` for the `cli_<name>` **before** considering
non-CLI capabilities. Only when **no usable CLI** covers the domain does the
gate fall through to the existing non-CLI-capability PASS (plugin/skill owns it)
or to the honest refusal. `domain_tool_map` is already supplied by
`_run_evidence_gate`, so the CLI name is in hand.

**Test change:** `test_non_cli_capability_wins_and_passes` → replaced by
`test_cli_capability_wins_over_plugin` (require_tool for the CLI even when a
plugin/skill also covers the domain).

### Component 3 — Plugin as a true fallback: dedup overlapping plugin tools (req 4, hard)

**New explicit map** `PLUGIN_CLI_OVERLAP: dict[str, str]` (plugin/native-tool
identity → CLI name that supersedes it), e.g.:

```
"github":   "gh",        # github/* MCP tools
"vercel":   "vercel",    # native vercel REST tool + any vercel/* tools
"stripe":   "stripe",    # stripe/* MCP tools
"supabase": "supabase",  # supabase/* MCP tools
"gmail":    "gam",       # native gmail REST tool -> cli_gam
```

**New pure helper** `suppress_plugin_tools_covered_by_cli(tools,
active_cli_names) -> tools`: for each overlap entry whose `cli_<cliname>` is in
`active_cli_names`, drop the matching plugin tools — namespaced `<plugin_id>/*`
and the exact native tool name. Applied during tool assembly in
`BrainManager.generate()` (near `_apply_plugin_relevance`). The plugin is thus
invisible whenever its CLI is connected; it reappears only as a fallback when
the CLI is absent.

**Anti-drift (mandatory):** a parity test asserting every `PLUGIN_CLI_OVERLAP`
key is a real plugin id / native tool name (marketplace catalog) and every
value is a real CLI name (seed catalog). This is the multi-layer-drift class the
project guards against (`docs/anti-drift-three-layer.md`); the map must not
silently rot when a catalog entry is renamed.

**Degradation:** any fault returns the tools unchanged (no dedup) — never blocks
the turn.

### Component 4 — Self-documentation via `<cli> --help` + CLI-first wording (req 3)

Prompt-only (Option A — chosen). Extend the "CONNECTED CLIS" section
(`jarvis/clis/prompt_section.py`) with two instructions:

1. **CLI-first:** "Prefer these CLIs over any equivalent plugin — they are
   faster and cheaper. Use a plugin only when no CLI covers the task."
   (reinforces Components 2/3 for the model's free choice outside the gate.)
2. **Self-discovery:** "If you are unsure of the exact command or flags, first
   run `<cli> --help` or `<cli> <group> --help` (read-only) to discover them,
   then issue the real command."

No new execution code: `cli_<name>` already accepts an arbitrary `command`
string; `<cli> --help` passes the binary-guard, runs with `stdin=DEVNULL` + the
non-interactive env, and is bounded by the existing 60s timeout and 4000-char
stdout truncation (group-level `--help` is the targeted, non-truncating form).
The curated `tool_schema_examples` remain as an optional fast-path hint but are
no longer required for correctness.

## Data flow (the implicit billing/stripe example)

"wie sind meine aktuellen Stripe-Umsätze?" <!-- i18n-allow: example user voice query -->
→ `_run_evidence_gate` builds `domains = merge(connected_domain_keyword_map(reg),
cfg.domains)`; the derived map contributes `payments: {stripe, umsatz, payment,
invoice, …}` (denylist removes none here)
→ `check_evidence_domain` matches lookup-shape + `umsatz`/`stripe` → domain
`payments`; `domain_tool_map[payments] = cli_stripe`
→ Component 2: CLI wins → `require_tool(cli_stripe)` directive injected
→ Component 4: if the model is unsure of the subcommand, it runs
`stripe --help` first, then the real read command
→ Component 3 already removed the `stripe/*` plugin tools from the dict, so the
plugin is never a competing choice.

## Error handling & degradation

- Gate already degrades to PASS on any fault (unchanged).
- `connected_domain_keyword_map` → `{}` on fault (gate runs on config only).
- `suppress_plugin_tools_covered_by_cli` → returns tools unchanged on fault.
- `<cli> --help` is best-effort and model-driven; a failed help call surfaces
  honestly via the existing CLI failure narration
  (`_cli_failure_reason`, 2026-06-17) and the model proceeds or asks.

## Testing strategy (all TDD RED→GREEN)

- **C1:** Stripe/payments forces `cli_stripe` from a natural phrasing using the
  *derived* map; denylist blocks "was kostet ein Tesla"; curated config still
  augments; derivation degrades to config-only on fault.
- **C2:** gate prefers the CLI over a covering plugin/skill (inverted test);
  falls through to non-CLI only when no CLI covers the domain.
- **C3:** `suppress_plugin_tools_covered_by_cli` drops `github/*` when `cli_gh`
  is active, keeps them when it is not; native `gmail`/`vercel` defer to
  `cli_gam`/`cli_vercel`; **parity test** for `PLUGIN_CLI_OVERLAP`.
- **C4:** prompt section contains the CLI-first + `--help` self-discovery
  instructions; `<cli> --help` passes the binary-guard (already covered).

## Files touched

- `jarvis/clis/capability_provider.py` — `connected_domain_keyword_map`,
  `PLUGIN_CLI_OVERLAP`, `suppress_plugin_tools_covered_by_cli` (+ denylist).
- `jarvis/brain/evidence_gate.py` — invert AD-CLI6; consume derived domains.
- `jarvis/brain/manager.py` — `_run_evidence_gate` merge; dedup step in tool
  assembly.
- `jarvis/clis/prompt_section.py` — CLI-first + `--help` wording.
- Tests: `tests/unit/brain/test_evidence_gate.py`,
  `tests/unit/clis/test_capability_provider.py`,
  `tests/unit/core/test_evidence_domains_config.py` (override semantics),
  new dedup + parity tests.

## Risks & mitigations

- **Over-triggering (C1):** mitigated by the denylist + the gate's lookup-shape
  pre-filter + targeted tests; config can always re-curate.
- **Dedup map drift (C3):** mitigated by the mandatory parity test.
- **`--help` latency/truncation (C4):** only on model uncertainty; group-level
  help is targeted; failures narrate honestly.
- **CLI lacks a plugin-only feature:** accepted per requirement 4 (CLI-first is
  the maintainer's explicit choice); complex work still routes to a worker.

## Out of scope

- Caching help digests at connect time (Option B — rejected for prompt bloat).
- The 112k-token fast-tier context bloat (separate investigation).
- Rewriting the legacy `resolve_intent` path (it is orthogonal; untouched).
