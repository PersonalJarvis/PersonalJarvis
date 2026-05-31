# Skills-Brain-Integration — Assumptions

## A-1: Plan is based on the phase-8-review-pipeline snapshot

The master plan cites file paths and line numbers from the state of `phase-8-review-pipeline` (e.g. `factory.py:39`, `manager.py:546-635`, `pipeline.py:1283`). The current branch, however, forks from `main` (an older state). **Before editing code per phase: verify the file paths + lines against the main state.**

## A-2: Another agent commits later

A parallel agent is working on `latency-sprint-2-caching` on tool adapters (`jarvis/plugins/tool/email_list_unread.py`, `calendar_list_today.py`, modified SKILL.md of `morning-routine`, `deep-work-mode`, `memory-save`). This work is secured in a stash (`stash@{0}` named `wip: parallel-agent-work-on-latency-sprint-2-caching`). We must coordinate when merging later.

## A-3: Awareness-Layer spec is not final

The Awareness-Layer (A0–A5) continuous-context plan has not been started yet. The bus event schema is, however, settled: `Event` with `trace_id: UUID` + `timestamp_ns`. New events of the Skills-Brain-Integration use the `activation_path: Literal["voice_direct", "hotkey", "cron", "brain", "confirmed"]` discriminator as a forward-compatible field.

## A-4: Phase 7 self-mod tools are a separate feature

`jarvis/brain/tools/skill_authoring.py` (`spawn_skill_author` tool) is Phase 7.5 — it writes new skills, it does not run them. `run_skill` (Skills-3) runs existing skills. Disjoint tool descriptions prevent LLM confusion. **Do not develop both on the same branch** — if Phase 7 is not yet merged: conflict resolution later.

## A-5: Token-budget realism

5 builtin skills × ~50 tokens (name + description) = ~250 tokens of system-prompt overhead. Plus the `run_skill` tool definition ~120 tokens. Total ~370 tokens. At 20 skills it gets tight (~1500 tokens) → activate Backlog-B1 (RAG retrieval).

## A-6: Pre-Brain hook breaks no existing tests

`_handle_utterance` is covered by extensive tests. The Pre-Brain hook inserts an early-return path but does not change the default path. Even so: all 26 routing tests + 40 output-filter tests must stay green after Skills-1.

## A-7: Skill-body injection is the latency bomb (Skills-3)

Body injection must arrive as a **separate ephemeral system message** — NOT in the cached router prefix. Otherwise the Haiku cache breaks after every skill invocation. The latency test `test_run_skill_does_not_invalidate_router_cache` is a mandatory gate for the Skills-3 merge.

## A-8: Conflict rule is deterministic

Pre-Brain match → skill runs, the brain is **not** called at all. Brain tool call → skill runs, the trigger match is blocked by the idempotency window. Never double-activation.

## A-9: Default-branch origin is main

The plan forks from `origin/main`. `git pull origin main` before switching branches was not necessary (Already up to date). Should a later merge-back to `main` be necessary: PR review by the user before squash-merge.
