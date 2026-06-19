---
title: "ADR-022: Web-search skill Phase 2 — grounding, citations, router wiring"
slug: adr-022-web-search-phase-2
diataxis: adr
status: draft
date: 2026-05-27
owner: harald
phase: skills
audience: developer
supersedes: none
amends: ADR-021
y_statement: >
  In the context of the Phase-1 web-search scaffold (ADR-021, PR #44) being
  a deliberately defensive structural shell that does not yet perform real
  grounded search,
  facing the gap between the scaffold's promise ("routes through Gemini
  Flash, returns search hits") and what the code actually does
  (``model.generate_content(prompt)`` with no ``tools=[google_search]``,
  no ``grounding_metadata`` parsing, not wired into ``ROUTER_TOOLS``),
  we propose to close the gap in a single Phase-2 wave that adds (a) real
  grounding via the native ``google_search`` tool, (b) a
  ``grounding_chunks → SearchHit`` citation pipeline, (c) ``ROUTER_TOOLS``
  + ``pyproject.toml`` entry-point registration, (d) the matching
  ``jarvis/safety/risk_tier.py`` policy table entry, and (e) one live
  integration test gated behind a `pytest -m gemini_live` marker,
  to achieve a skill that the router-tier brain can actually dispatch
  from a real voice turn and return cited results from,
  accepting the higher review surface (one ADR + multiple touched
  subsystems) and the operational risk that the live integration test
  consumes Gemini API budget on every CI run that opts in.
---

# ADR-022 — Web-search skill Phase 2 (grounding + citations + router wiring)

**Status:** Proposed (2026-05-27)
**Phase:** Skills — Phase 2 of the web-search skill
**Pairs with:** ADR-021 (Phase 1 scaffold), ADR-0011 (Router pure dispatcher), ADR-0010 (Output filter pattern-based)
**Builds on:** PR #44 (`feat/web-search-skill`, commit `31e54e1`)

## Context

ADR-021 shipped a deliberately defensive Phase-1 scaffold. The scaffold has
production-grade sanitisation, voice-override discipline, single-source
risk-tier classification, and 50 tests including a Hypothesis property
test on the sanitiser and a wall-clock latency test against a
deterministic fake client. What it does **not** have, by design:

1. **No real grounded search.** `DefaultGeminiClient.search()` calls
   `model.generate_content(prompt)` with a prompt that asks Gemini to
   "search the web and return JSON-shaped results". Without
   `tools=[google_search]` the model is in pure-completion mode and will
   confabulate URLs.
2. **No citation pipeline.** `SearchHit(title, url, snippet)` is defined
   but never populated — `SearchResponse.hits` is always `()`.
3. **No router integration.** The skill is a standalone artifact under
   `src/skills/web_search/`. It is not in `ROUTER_TOOLS` (jarvis/brain/
   factory.py), has no `pyproject.toml` entry-point, and is not
   reachable from voice / chat / REST paths.
4. **No policy-table entry.** The hardcoded `RISK_TIER = "monitor"`
   constant exists in the package but has no corresponding row in
   `jarvis/safety/risk_tier.py`'s tier table — `ToolExecutor` could not
   safely dispatch the skill today even if it were wired in.
5. **No live test.** The latency test bounds wall-clock against a
   `FakeGeminiClient` with 5 ms simulated round-trip; the suite has zero
   coverage against a real Gemini API call.

Phase 2 exists to close these five gaps in one coherent wave so the
skill becomes router-dispatchable.

## Decision

Phase 2 ships as a single PR with five orthogonal additions:

### 1. Native `google_search` grounding

`DefaultGeminiClient.search()` is rewritten to attach the native search
tool:

```python
import google.generativeai as genai
model = self._genai.GenerativeModel(
    self._model_name,
    tools=[genai.protos.Tool(google_search=genai.protos.GoogleSearch())],
)
raw = model.generate_content(prompt)
```

The prompt becomes shorter and more conversational ("Answer the user's
question with grounded web search.") because the tool surface, not the
prompt, drives the search.

### 2. `grounding_chunks → SearchHit` citation pipeline

A new private helper `_extract_hits(raw) -> tuple[SearchHit, ...]`
parses `raw.candidates[0].grounding_metadata.grounding_chunks`. Each
chunk's `web.uri` becomes `SearchHit.url`, `web.title` becomes
`.title`, and the corresponding `grounding_supports` entry's covered
text becomes `.snippet`. The helper is total — a response with no
grounding metadata returns `()` and the skill continues with an
empty `hits` tuple.

`SearchResponse` gains an optional `web_search_queries: tuple[str, ...]`
field populated from `grounding_metadata.web_search_queries`, used for
telemetry / policy tuning (see §4 below).

### 3. `ROUTER_TOOLS` + entry-point registration

Two registration touch-points, both load-bearing:

- `jarvis/brain/factory.py::ROUTER_TOOLS` gains `"web-search"`. This is
  the only place where the router-tier brain's tool surface is
  declared (ADR-0011 amended).
- `pyproject.toml::[project.entry-points."jarvis.tool"]` gains
  `web-search = "skills.web_search:WebSearchSkill"`. `pip install -e .
  --no-deps` is required after editing (AP-8).

A wrapper module `jarvis/plugins/tool/web_search.py` translates the
router-tier Tool protocol (`Tool.execute(args: dict, ctx: ToolContext)`)
into a `WebSearchSkill.run(...)` call, marshals `SkillResult` into the
Tool's `ToolResult`, and forwards the `voice` flag from the context.

### 4. `risk_tier` policy-table entry

`jarvis/safety/risk_tier.py` gains a `"web-search": RiskTier.MONITOR`
row in the `TOOL_DEFAULT_TIERS` mapping. The existing `risk_tier`
constant in `src/skills/web_search/__init__.py` (`RISK_TIER = "monitor"`)
remains the authoritative source — the policy-table entry is added with
a comment cross-referencing it and a `tests/unit/safety/test_risk_tier_parity.py`
guard that asserts the two are equal (anti-drift, mirroring the
BUG-008 five-layer pattern).

### 5. Live integration test (`-m gemini_live`)

A new test file `tests/integration/test_web_search_gemini_live.py`
contains exactly two tests, both decorated with
`@pytest.mark.gemini_live`:

- `test_real_search_returns_grounded_hits` — drives `DefaultGeminiClient`
  against the real API, asserts `len(hits) > 0` and at least one
  `web_search_queries` entry.
- `test_real_search_p50_latency_below_3s` — runs the same query five
  times, asserts median wall-clock < 3 s (cloud-first VPS doctrine: the
  voice path can tolerate at most ~2.5 s for the search portion before
  TTS noticeably lags).

`pyproject.toml::[tool.pytest.ini_options].markers` gains `gemini_live`
with the standard "skipped unless explicitly requested" semantics. CI
does **not** opt in by default. Local invocation:
`pytest -m gemini_live tests/integration/`.

## Consequences

**Positive**

- The skill becomes a real router-dispatchable tool, closing the
  scaffold→production gap in one reviewable PR.
- Citation pipeline gives the UI real `SearchHit` rows; voice path
  continues to receive only `spoken_summary` (URLs already stripped by
  `scrub_for_speech`, no regression).
- Anti-drift parity test pins the `RISK_TIER` constant to the
  policy-table entry — adding the BUG-008 defence preemptively rather
  than discovering it on the fifth recurrence.
- One live test gives manual-trigger coverage without burning CI budget.

**Negative**

- Single-PR review surface is larger than Phase 1 (touches `pyproject.toml`,
  `jarvis/brain/factory.py`, `jarvis/safety/risk_tier.py`,
  `jarvis/plugins/tool/web_search.py`, plus the skill modules and a new
  live test). Reviewers must check all five layers in one pass.
- `pyproject.toml` entry-point edit requires `pip install -e . --no-deps`
  on every developer machine — easy to forget (BUG-006 territory).
- Live test consumes a paid Gemini API call when invoked. The
  `gemini_live` marker keeps CI out by default, but the cost surface
  exists.

**Neutral**

- The skill remains under `src/skills/` rather than being migrated into
  `jarvis/`. Phase 3 (not in scope here) can decide whether to
  consolidate into the in-tree layout if other skills follow the same
  pattern.

## Alternatives considered

1. **Split Phase 2 into five sub-PRs** (one per addition) — rejected.
   The pieces are coupled: `ROUTER_TOOLS` without the policy-table entry
   would let `ToolExecutor` dispatch a skill it can't classify; the live
   test without grounding has nothing real to test. Sequential merging
   would leave main in a half-wired state between PRs.
2. **Defer the live test to Phase 3** — rejected. Without a live test,
   the grounding pipeline is unverified — `_extract_hits` is parsing a
   real API response shape from documentation, not from a fixture, and
   the documentation may have drifted.
3. **Use a generic search SDK (SerpAPI, Brave Search)** instead of
   Gemini's native `google_search` tool — rejected. The maintainer
   already pays for Gemini 3.5 Flash (User Mandate, jarvis.toml:187);
   adding a second vendor surface contradicts the cloud-first single-
   vendor preference. Future ADR may revisit.

## Verification (when implemented)

- `pytest -q tests/skills/test_web_search.py` → existing 50 tests still
  pass after the grounding refactor (Hypothesis property test on
  sanitiser unchanged; latency test still uses `FakeGeminiClient`).
- `pytest -m gemini_live tests/integration/` (manual, opt-in) → 2 tests
  pass against the real API.
- `python -c "from jarvis.brain.factory import ROUTER_TOOLS; assert 'web-search' in ROUTER_TOOLS"` → passes after entry-point install.
- `grep -rn "web-search\|web_search" jarvis/safety/risk_tier.py` → row present.
- `tests/unit/safety/test_risk_tier_parity.py` → green (anti-drift guard).

## Open questions for review

1. **Voice-bridge announcement format.** When the router dispatches
   `web-search` from a voice turn, should the spoken `summary` be
   prefixed with a fixed phrase ("Hier was ich gefunden habe: …") or
   read raw? Phase-1's `scrub_for_speech` returns the trimmed text
   directly; a prefix would need a new constant.
2. **Cost-meter accounting.** Should `web-search` API spend land in
   the existing `gemini-3.5-flash` bucket of `jarvis/brain/cost.py`, or
   does it warrant its own `web-search` cost bucket for budget
   visibility?
3. **`grounding_supports` snippet selection.** Each `grounding_chunk`
   may be referenced by multiple `grounding_supports` entries with
   different `segment.start_index` / `end_index`. Pick the longest
   covered text, the first one, or concatenate? Affects the UI density.
