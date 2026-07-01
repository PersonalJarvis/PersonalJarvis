---
title: "ADR-0010: Output-Filter Pattern-based"
slug: adr-0010-output-filter-pattern-based
diataxis: adr
status: active
owner: sam
last_reviewed: 2026-04-29
phase: 1
audience: developer
---

# ADR-0010 — The output filter is pattern-based, not hard-cut or an LLM roundtrip

**Status:** Accepted (2026-04-25)
**Phase:** Persona-Refactor §1 — Output Filter ("Ghost in the Machine")

## Context

Brain output lands on two TTS paths:

1. `pipeline._handle_utterance` → `_speak()` → `tts.synthesize()` (main path).
2. `pipeline._on_announcement` (`AnnouncementRequested` bus handler) → `tts.synthesize()` (bus bypass for skill / Jarvis-Agent announcements).

On both paths, tool-call JSON, stack traces, Markdown remnants, engineering jargon (`Harness`/`MCP`/`Subprocess`/`Provider`), self-reference (self-identification as AI), echo paraphrase (reformulation opening) and filler openers (generic affirmations) reach the TTS synthesis unfiltered — the user hears engineering garbage read aloud. Pre-existing pre-filters (`_strip_paraphrase_prefix`, `_is_non_substantive_response`) cover only a part of it.

Three filter strategies were up for debate:

- **A. LLM roundtrip** ("Please rephrase this in voice form"). Clean output quality, but 200–800 ms of additional latency per turn. Kills any butler tone.
- **B. Hard cut** (truncate everything after the first stack-trace / JSON marker). Fast, but wrecks legitimate Jarvis-Agent replies that look "JSON-like".
- **C. Pattern-based** (regex-only, with whitelist protection for user-concept words). Fast, testable, deterministic.

## Decision

**Pattern-based filter in `jarvis/brain/output_filter.py`.** Regex-only, no LLM call, with the following order of operations:

```
1. Stack trace -> standard phrase ("Error occurred"), fallback_used=True
2. Markdown strip (**, ##, ```, leading -/*)
3. Tool-call JSON / tool-call KW args / XML tool tags (tool-name whitelist)
4. Self-reference (AI self-identification / language model claims)
5. Echo paraphrase ONLY in opener position (<= 60 characters)
6. Filler opener (generic affirmations)
7. Engineering jargon (Harness / MCP / Subprocess / Provider) with hyphen protection
8. Whitespace normalization
```

**Whitelist** (sacred, NEVER scrubbed): `Datei`, `Email`, `Browser`, `Terminal`, `Notiz`, `Termin`, `Kalender`.
Hyphen compounds (`Browser-Provider`, `Brain-Provider`) are preserved by lookbehind.

**Failure-mode-6 protection:** the echo-paraphrase filter acts ONLY on the first 60 characters. Mid-sentence echoes are preserved — otherwise the filter would swallow legitimate confirmations.

### Rationale

- **Latency requirement:** the main Jarvis must deliver the first token in <1 s. An LLM-filter roundtrip would be +200–800 ms per turn — unacceptable for smalltalk.
- **Testability:** regex patterns are encapsulable as tests (23 cases in `tests/unit/brain/test_output_filter.py`). An LLM filter would be non-deterministic.
- **Defense in depth:** the filter complements the existing pre-filters (`_strip_paraphrase_prefix`, `_is_non_substantive_response`) — they partially overlap, but that is OK. Multiple protection layers are more robust.

## Consequences

+ **Determinism:** the filter result is 1:1 reproducible for a given input.
+ **Latency:** ~50 µs per filter call (measured, dominated by the regex compile cache).
+ **Whitelist protection** separates user-concept words from engineering jargon — the user never hears "Datei" mangled.
+ **Hyphen lookbehind** (`(?<!\w-)`) must come BEFORE the alternative, not after it — Python regex checks the lookbehind at the match start, not at the match end. (A bug hit during implementation; fix documented in the `output_filter.py` comment on lines 119–123.)
- **Pattern coverage lags real brain outputs:** brains generate new tool-use markup formats (`<function_calls>[}]</function_calls>` as the Anthropic-internal format) that are not matched by the `TOOL_NAMES` whitelist. **Mitigation:** the pattern list is configurable via constants in `output_filter.py`; new formats can be added by PR.
- **Echo paraphrase opener only:** failure mode 6 is clearly documented, but the 60-character cutoff is a heuristic. A user-formulated pattern-match test (`test_echo_paraphrase_mid_sentence_is_kept`) prevents regression.

## Alternatives Considered

- **A. LLM-filter roundtrip** ("the brain produces raw, an LLM wrapper rephrases for voice"): cleanest output quality, but +200–800 ms latency kills the butler tone. **Rejected.**
- **B. Hard cut at the tool-JSON marker** (cut off everything after `dispatch_to_*`): very fast, but wrecks legitimate Jarvis-Agent replies that happen to contain JSON-like strings ("Die Datei deklariert vier Provider in JSON-Format"). **Rejected.**
- **D. Enforce a pre-filter via the brain system prompt** ("You MUST NOT output JSON"): not reliable — Gemini Flash still leaks occasionally. Defense in depth required (prompt + filter). **Implemented as a complement** (JARVIS_PERSONA.md ECHO-PARAPHRASE section + ROUTER-DISCIPLINE section), not as a replacement.

## Subsequent drift-class extension 2026-04-28

**Status:** Amendment 2026-04-28
**Trigger:** the re-probe `scripts/voice_e2e_probe.py` of 2026-04-28 (see `docs/persona-research.md` Section 1.3) revealed four new drift classes that became visible through the provider switch `gemini` → `claude-sonnet-4-6` and the absence of `persona_loader.py` on the branch. All four fit exactly into the filter-blacklist scope and are not a spec extension, but pattern extensions within the original pipeline order.

### Seven new drift classes (two waves on the same day)

**Wave 1** (commit `e73ac58c`) — four drift classes from the first re-probe:

| # | Drift | Probe scenario | Pattern (code) | Action marker |
|---|---|---|---|---|
| 1 | A1 „Sir" address | 03 + 07 (body) | `SIR_OPENER_RE`, `SIR_TAIL_RE` with `QUOTE_PROTECT_RE` (quote protection) | `removed_anrede_drift` |
| 2 | „Sub-Agent"/„Supervisor-Agent" | 03 + 07 + 13 | `JARGON_COMPOUNDS` + `JARGON_COMPOUND_RE` | `removed_engineering_jargon` |
| 3 | Tool-args YAML block (body leak) | 03 (body) | `TOOL_ARGS_YAML_KEYS` + `TOOL_ARGS_YAML_RE` | `removed_tool_json` |
| 4 | Post-scrub garbage fallback | 12 (DE+EN) | `MIN_MEANINGFUL_CHARS = 3` + `replaced_with_fallback_residue` | `replaced_with_fallback_residue` (in addition to prior actions) |

**Wave 2** (commit `c8729c07`) — three drift classes from the second re-probe **after** the wave-1 fix. The brain now visibly leaks the Anthropic-internal tool-use XML format, which previously either had not occurred or went unnoticed through the narrower filter:

| # | Drift | Probe scenario | Pattern (code) | Action marker |
|---|---|---|---|---|
| 5 | `<function_calls><invoke>` Anthropic format | 12 (DE+EN, re-probe after wave 1) | `ANTHROPIC_FUNCTION_CALLS_RE`, `ANTHROPIC_INVOKE_RE` | `removed_tool_json` |
| 6 | Generic tool-wrapper tags (`<tool_call>`, `<tool_response>`, `<tool_use>`, `<function_results>`) | 01, 06, 11 (DE) | `GENERIC_TOOL_WRAPPER_RE` (conservatively limited to known wrapper names) | `removed_tool_json` |
| 7 | Base64 image body leak (data URI + long Base64 sequences) | 08 (DE) — a complete WebP as a 1500+-char string | `BASE64_DATA_URI_RE`, `LONG_BASE64_RE` (≥ 200 chars) | `removed_tool_json` |

**Pipeline order:** wrapper blocks (function_calls/invoke/generic-wrapper/base64) are scrubbed **FIRST** in step 3, then the remaining smaller patterns (TOOL_XML_RE, TOOL_CALL_FN/INLINE/JSON/KW, YAML). This order prevents inner token patterns from matching parts of the wrapper content and leaving whitespace remnants.

### Why this extension needs no new ADR

- **Pipeline order unchanged** — the eight steps (stack trace → Markdown → tool calls → self-reference → echo → filler → jargon → whitespace) remain. Step 3 is extended with the YAML form; step 7 with compounds; a new step 7b („Sir" address) and a final step 9 (fallback trigger) are added.
- **Pattern-based + regex-only** stays — no LLM call, no behavior change.
- **Whitelist protection unchanged** — user-concept words (Datei/Email/Browser/…) stay sacred. The „Sir" protection is analogous (quotes are spared via `QUOTE_PROTECT_RE`).
- **Failure mode 6 (mid-sentence echo)** is preserved — `OPENER_BUDGET = 60` unchanged; quote protection is the analogous caution for „Sir".
- **Determinism + latency** stay — the `MIN_MEANINGFUL_CHARS` check is O(n) string iteration without regex.

### Failure modes of the extension

- **Sir false negative on a non-comma address:** if a brain produces „Sehr geehrter Sir, …", the pattern does not catch it. No known provider drift today; if it occurs, add a separate pattern or extend the filler-opener list.
- **YAML-block false positive:** if a user word accidentally contains `action:` or `target:` as a list heading, the block is scrubbed. Mitigation: `TOOL_ARGS_YAML_KEYS` is conservatively limited to known tool arguments; further user-relevant words (`task:`, `note:`, `file:`) are NOT in the list.
- **Fallback trigger too aggressive:** `MIN_MEANINGFUL_CHARS = 3` could catch very short legitimate replies („Ja.", „Ok.") if the filter was active. Mitigation: only if `actions != []`, i.e. a filter actually scrubbed something. „Ja." without a filter action passes through.

### Tests + statistics

| State | Tests in `test_output_filter.py` | Drift-specific |
|---|---|---|
| Before 2026-04-28 | 23 green | 0 (mandate-phase-1 baseline) |
| Tests-first wave 1 (red before code fix) | 25 green, 6 red | 6 (four new cases + 2 defense cases) |
| After the wave-1 filter extension | 31 green | 6 explicit drift cases |
| Tests-first wave 2 (red before code fix) | 32 green, 3 red | 3 (Anthropic tags + Base64) + 1 defense case green |
| After the wave-2 filter extension | **35 green** | 10 explicit drift cases total |

## References

- Implementation: `jarvis/brain/output_filter.py`, integration `jarvis/speech/pipeline.py` path #1 (line 1330) + #2 (line 647).
- Tests: `tests/unit/brain/test_output_filter.py` (31 cases after the 2026-04-28 extension).
- Persona mandate: `Jarvis-Behavior/persona-delegation-mandate.md` §"Phase 1 — Output-Filter".
- Research: `docs/persona-research.md` Section 1.3 (drift classification per output) + Section 3 (TTS bypass paths).
- Before/after outputs: `docs/persona-refactor-results.md` Section 1 (for the 2026-04-25 baseline) + `docs/persona-research.md` Section 1.2 (for the 2026-04-28 re-probe).
- Probe script: `scripts/voice_e2e_probe.py`.
