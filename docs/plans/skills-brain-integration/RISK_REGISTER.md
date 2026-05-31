# Skills-Brain-Integration — Risk Register

| # | Risk | Prob. | Impact | Mitigation | Owner | Status |
|---|--------|----------|--------|------------|-------|--------|
| R1 | Conflict with Phase 7 (`spawn_skill_author` vs. `run_skill`) — LLM confused on tool choice | high | med | Disjoint tool descriptions ("USE run_skill TO EXECUTE EXISTING; USE spawn_skill_author TO CREATE NEW"). Test: 6 disambiguation cases. | Skills-3 | open |
| R2 | Branch conflict — another agent is working on `latency-sprint-2-caching` | high | high | New branch off `main`. Stash `wip: parallel-agent-work-on-latency-sprint-2-caching` as a backup. | Day 0 | mitigated |
| R3 | Awareness layer A0–A5 bus-event incompatibility | med | med | Forward-compatible: `SkillExecuted(activation_path: "trigger"\|"brain"\|"scheduled"\|"confirmed")`. ADR-0012. | Skills-1 | open |
| R4 | Token-budget inflation — prompt grows linearly with the number of skills | high | med | Hard limit `max_skills_in_prompt=15`. Telemetry alert at N>15 → activate Backlog-B1. | Skills-4 | open |
| R5 | Voice-latency regression — persona mandate <800ms at risk | med | high | Skills-1 has 0ms brain impact. Skills-2 stable sort (cache hit). CI gate `test_router_first_token_latency.py` p95 ≤ 800ms. | Skills-2 | open |
| R6 | Double activation Pre-Brain + brain tool call | med | high | Idempotency window 5s in `run_skill`. Tests `test_pre_brain_match_skips_brain` and `test_brain_run_skill_blocked_if_already_fired`. | Skills-3 | open |
| R7 | User confusion from two activation paths | med | low | UI tile in `SkillsView` shows `activation_path`. Audit log contains the field. Documented in ADR-0012. | Skills-5 | open |
| R8 | Skill-body injection increases cache eviction | med | med | Validate `token_budget_estimate ≤ 4000`. Body injection as a **separate** ephemeral system message (no cache mix). | Skills-3 | open |
| R9 | Phase-8 review pipeline interferes | low | med | ADR-0011 amendment: `run_skill` is a sibling of `dispatch_with_review`, not overlapping. | Skills-4 | open |
| R10 | TriggerMatcher false positive — user asks ABOUT a skill, the skill runs anyway | med | med | Voice patterns imperative-specific (`r"starte\s+morning\s+routine"` instead of `r"morning routine"`). Audit of all 5 builtins. | Skills-1 | open |
| R11 | Two-bus bridge: skill events do not reach the UI | med | med | In `_phase2_full_brain` instantiate `SkillRunner` with `bus=server.bus`. | Skills-1 | open |

## Active Mitigations

- **R2 mitigated 2026-04-30:** Stash `wip: parallel-agent-work-on-latency-sprint-2-caching` contains 24 tracked mods (641 insertions, 198 deletions) — a safe backup. New branch `skills-brain-integration` branches cleanly off `main`.
