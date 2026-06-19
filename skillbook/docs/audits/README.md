# Skillbook audits

Historical record of the two self-audits run against the skillbook layer.
Both were written observation-only and motivated subsequent rework.

- **`AUDIT.md`** (2026-05-26) — first self-audit. Classified the original
  skillbook delivery as **(C) Mock-shaped skeleton**: `MockLLM`,
  `MockSymconActor`, and `InProcessTransport` lived in `src/`, and
  `AgentInstance.build()` instantiated `MockLLM()` as the factory default.
  The fakes were extracted into `tests/fakes/` in the
  `refactor/extract-fakes-clean` work (ADR-0010, commits `69a4e76b6..e7cdefeca`).

- **`FORENSICS.md`** (2026-05-26, reconstructed 2026-05-27 from session
  transcript) — second-pass forensic inspection. Quantified the remaining
  gaps: zero MQTT code in `src/`, no concrete `Transport` implementation,
  `lats.py` was try/except + counter, Bloom code never wired into
  `engine.py`, knowledge-graph tables permanently empty in every run,
  Anthropic SDK path unexercised. Closed in the
  `refactor/skillbook-real-adapters-clean` work
  (commits `e7ecebd..994bf6c`).

These documents pre-date their own fixes — when reading, treat each finding
as a snapshot of the codebase at that date; the closure commits are noted
parenthetically inside `FORENSICS.md`.
