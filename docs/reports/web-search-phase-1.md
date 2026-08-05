# Web-search skill — Phase-1 report

**Date:** 2026-05-27
**Branch:** feat/cartesia-tts
**ADR:** [`ADR-021-web-search.md`](../adr/ADR-021-web-search.md)
**Scope:** Foundation wave — skill package, sanitiser, voice-override hook, client protocol, and full test surface.

## What landed

Five small modules under `src/skills/web_search/` form a self-contained,
greppable skill. The package is dependency-injected throughout: the only
public surface that touches a vendor SDK is `DefaultGeminiClient`, and
its `google.generativeai` import is deferred to call time so the test
suite stays import-clean even when the SDK is absent. A frozen
`FakeGeminiClient` test double ships in the same module — it records
calls, can return canned hits, and exposes a `latency_ms` knob so the
mandatory latency-budget test in `tests/skills/test_web_search.py` can
bound wall-clock cost without sleeping for real seconds.

The sanitiser (`_sanitize.py`) is the strictest layer. It NFKC-normalises
input, strips ASCII control chars, collapses whitespace runs, enforces a
512-character cap, and rejects ten known prompt-injection tokens
case-insensitively. The Hypothesis property test runs 200 examples per
invocation and asserts every post-sanitise invariant the rest of the
skill relies on. The voice-override module (`_voice_override.py`) is a
pure tightening function — same input yields same output, no I/O — and
the regex-only `scrub_for_speech` honours the parent repo's
`scrub_for_voice` discipline (no LLM round-trip on the spoken path).

## Acceptance verification

The exact command from the goal —
`pytest -q tests/skills/test_web_search*.py` — collects 50 tests and
all 50 pass in roughly two seconds. The risk tier is hardcoded as the
module-level `Final[str] = "monitor"` constant in `skill.py` and
re-exported from `__init__.py`; a single `grep -r "risk_tier"` against
the skill directory surfaces every site.

## What is not in scope

The skill is intentionally *not* wired into Jarvis's `ROUTER_TOOLS`
frozenset, the `pyproject.toml` entry-points, or the
`jarvis/safety/risk_tier.py` policy table. Phase-2 will publish a
follow-up ADR covering router integration, cost-meter wiring, and the
voice-bridge path for spoken summaries. Until then, the package is a
standalone artifact that can be imported, tested, and reviewed in
isolation.
