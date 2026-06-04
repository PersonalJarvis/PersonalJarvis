---
schema_version: "1"
name: memory-save
version: "1.1.0"
description: |
  DEPRECATED in B5 (2026-05-13). Triggers removed so 'merk dir' phrases fall
  through to the normal brain pipeline and land in the new wiki via the
  Awareness layer + WikiCurator. Kept in tree as a reference; will be deleted
  in Phase B4 cleanup.
category: memory
tags: [memory, notes, recall, deprecated]
author: builtin
license: MIT
state: disabled
triggers: []
requires_tools:
  - remember
risk_policy:
  default_tier: safe
  per_tool_overrides:
    remember: safe
config:
  namespace: "user-facts"
  max_length_chars: 2000
  auto_tag_language: true
token_budget_estimate: 1500
---

> **Note (B5, 2026-05-13):** This skill is disabled. The empty `triggers:` block
> means it no longer matches any voice phrase. Long-term-memory writes now go
> through the B5 pipeline (Awareness → SessionRollupWorker → WikiCurator →
> Obsidian vault). The full deletion is queued for Phase B4 Cleanup.

# Memory Save

A very short, very frequent skill. The user states a fact — Jarvis
saves it. No big processing, no LLM analysis (for now): the
user input is stored directly, tagged with a timestamp and language.

## Workflow

### 1. Extract the content

The `TriggerMatcher` delivers the capture group of the regex as `match.groups[1]`.
That is the text to save. Example:

Utterance: "merk dir: mein Hemd ist in der Waesche"
-> content = "mein Hemd ist in der Waesche"

Utterance: "remember this milk is in the fridge"
-> content = "milk is in the fridge"

If `len(content) > config.max_length_chars`, truncate + warning:
"Nachricht gekuerzt auf {max} Zeichen." (message truncated to {max} characters)

### 2. Determine the language (optional)

If `config.auto_tag_language == true`:
- First trigger token "merk" -> `de`
- First trigger token "remember" -> `en`
- Fallback: `unknown`

### 3. Persist

We use the built-in `remember` tool — it writes into
`data/core_memory.json`, which is injected directly into the system prompt.
The next brain call therefore sees the fact immediately.

TOOL: remember {"fact": "{{content or utterance}}", "category": "{{detected_language or 'general'}}"}

### 5. Confirm

Short TTS output — deliberately minimal so that a frequent "merk dir X" does not annoy:

- DE: "Gemerkt."
- EN: "Got it."

No echoing the content back (privacy: the user knows what they said).
On error: "Konnte nicht speichern: {reason}." (could not save: {reason})

## Fallbacks

- memory-mcp down: local fallback (step 4).
- Empty content (trigger word only): "Was soll ich merken?" (what should I remember?) — re-prompt.
- Duplicate detection: _not_ in the skill — that is the job of the memory-mcp layer.

## Do not do

- No LLM summary of the content (too slow, too expensive for high frequency).
- No automatic categorization (privacy + cost).
- No "do you really want to save this" — tier `safe`, no confirmation.

## Trace

Skill execution traces `SkillStarted` -> `SkillStepExecuted` (memory-mcp/put) ->
`SkillCompleted`. The content itself is NOT stored in the flight recorder
(privacy); only length, language, success/failure. The user can retrieve the note via
`jarvis --skills recall` (v1.1) or directly by voice: "was weisst du
ueber die Waesche" (what do you know about the laundry) (Memory-Recall skill, separate).
