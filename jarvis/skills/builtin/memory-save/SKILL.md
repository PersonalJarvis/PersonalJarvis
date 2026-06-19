---
schema_version: "1"
name: memory-save
version: "2.0.0"
description: >-
  DEPRECATED since B5 (2026-05-13) and disabled. Long-term-memory writes
  go through the wiki pipeline (Awareness layer, SessionRollupWorker,
  WikiCurator). Use when nothing — this skill must not be re-enabled;
  "merk dir" phrases belong to the normal brain pipeline.
when_to_use: >-
  Use when never — kept on disk only as a reference until the B4 hard-cut
  deletes it. Re-enabling it would hijack the B5 wiki path.
category: memory
tags: [memory, notes, recall, deprecated]
author: builtin
license: MIT
state: disabled
triggers: []
requires_tools: []
risk_policy:
  default_tier: safe
config:
  namespace: "user-facts"
  max_length_chars: 2000
  auto_tag_language: true
token_budget_estimate: 1500
---

> **Note (B5, 2026-05-13):** This skill is disabled. The empty
> `triggers:` block means it never matches a voice phrase, and the
> `state: disabled` keeps both invocation paths (trigger and run-skill)
> closed. Long-term-memory writes flow through the B5 pipeline
> (Awareness → SessionRollupWorker → WikiCurator → Obsidian vault).
> Full deletion is queued for the B4 hard-cut.

# Memory Save (deprecated)

Historical behavior, for reference only: the user stated a fact
("merk dir: X" / "remember this: X"), the trigger captured the tail as
`content`, and the fact was persisted into core memory with a one-word
spoken confirmation. That entire flow now lives in the brain pipeline
plus the wiki tier (`wiki-ingest` for deterministic saves) — do not
rebuild it here.
