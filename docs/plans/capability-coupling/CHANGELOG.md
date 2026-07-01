# Capability Coupling — CHANGELOG

## 2026-05-20

Agent A done: CapabilityRegistry API frozen at 3dcdbf8e4.

Agent B done.

Agent C done.
- `jarvis/brain/manager.py`: replaced hardcoded `NUTZE: search_web` block with
  dynamic `registry.render_for_prompt(lang)` call; graceful fallback to
  hardcoded block when `jarvis.core.capabilities` not yet deployed.  Both paths
  now append the bilingual "do not invent tools" hard rule.
- `jarvis/brain/manager.py`: added `_check_unsupported_intent()` sibling check
  before `_force_spawn_openclaw`; emits deterministic DE/EN refusal when
  `has_action_intent=True and resolve_intent=None and not smalltalk`.
  No LLM call (AP-11 compliant).
- `jarvis/brain/ack_brain/persona_prompt.py`: extended forbidden-vocabulary list
  with action-promise patterns (DE: "mache ich", "wird erledigt", "ist  <!-- i18n-allow -->
  gesendet", "ist eingetragen", "kümmere mich"; EN: "I'll do that", "will be  <!-- i18n-allow -->
  sent", "will be scheduled", "consider it done").  Added explicit positive rule:
  Ack-Brain may only emit (a) acoustic ack, (b) context-restating questions,
  (c) silence on uncertainty.
- `tests/unit/brain/test_routing.py`: 9 new test cases covering graceful no-op
  when module absent, smalltalk passthrough, unregistered-action refusal,
  registered-capability passthrough, system-prompt hard rule presence, and
  forbidden ack-brain phrases (DE × 5 + EN × 4).

Agent E done.
- `docs/adr/0017-capability-coupling.md`: full ADR authored — Context (phantom
  confirmation structural gap, AD-9, PHILOSOPHY.md graceful-no-op, search_web
  prompt-claim drift), Decision (CapabilityRegistry, pre-gen gate, capability-
  aware prompts, Critic honesty gate, extensibility contract), Consequences,
  Alternatives considered (post-gen regex scrub rejected, LLM classifier
  rejected, manual approval rejected).
- `tests/integration/test_capability_coupling_e2e.py`: integration test suite —
  all 5 hard-negative utterances (UNSUPPORTED + zero phantom TTS), 4 hard-
  positive cases (no false negatives), search-web prompt-claim drift test,
  Critic regression (requires_evidence + empty diff → success=False), dynamic
  render_for_prompt checks. Marked `@pytest.mark.skip(reason="awaiting agents
  A-D")` — authored now, skip guard removed once A-D merge.
- `docs/plans/capability-coupling/EXTENSIBILITY.md`: 1-page contributor guide —
  five steps to add a new capability (tool, MCP, harness), with code snippet,
  verification commands, and explicit list of files that do NOT need touching.
- `docs/anti-drift-three-layer.md`: appended cross-reference section linking
  the capability-coupling pattern to the existing anti-drift and visible-
  feedback patterns; includes the search_web drift analogy to BUG-008 and
  pointers to the specific regression tests.
- `docs/BUGS.md`: appended BUG-028 Capability Hallucination — symptom, root
  cause (3 decoupled layers), defenses (5 layers), regression test reference.

Agent D done.
- `jarvis/missions/critic/runner.py`: added `CapabilityHonestyCheck` dataclass,
  `_extract_tool_call_evidence()` (parses Jarvis-Agents stream.jsonl ``tool_use``
  frames + ``[TOOL_USE]`` CLI markers + ``dispatch-result`` events),
  `_resolve_capability_requires_evidence()` (uses `CapabilityRegistry` when
  available, falls back to action-verb + external-system-noun heuristic),
  `enforce_capability_honesty()` (overrides approve → revise when
  `requires_evidence=True` capability + no tool-call evidence). Wired into
  `CriticRunner.run()` as a post-LLM gate.
- `jarvis/missions/critic/summary.py`: new module with
  `summarise_from_tool_calls(calls) -> str` — deterministic German one-liner
  from real tool-call evidence; no LLM (AP-11 compliant).
- `jarvis/missions/critic/prompts.py`: tightened CRITIC_SYSTEM_PROMPT with
  CAPABILITY-HONESTY RULE section — worker text claims are hearsay, only
  ``tool_use`` records count as evidence.
- `jarvis/missions/voice/readback.py`: `render_approved` now accepts optional
  ``honesty_check: CapabilityHonestyCheck`` — renders failure readback when
  `honesty_overridden=True` (last-resort TTS guard).
- `tests/missions/critic/test_runner_dryrun.py`: 12 new test cases covering
  evidence extraction (stream.jsonl / CLI markers / dedup / empty), unit tests
  for `enforce_capability_honesty` (email/calendar hard-negatives, happy path,
  smalltalk passthrough), E2E `CriticRunner` hard-negative (LLM says approve,
  gate fires → revise), and `render_approved` honesty-guard tests.
