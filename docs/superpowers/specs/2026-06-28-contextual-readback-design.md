# Context-aware spoken readbacks — design

**Date:** 2026-06-28
**Status:** implemented (engine + computer-use surface + mission readbacks)
**Mandate:** the maintainer's standing requirement that Jarvis must NOT speak fixed
"stock" phrases out of a lookup table for its status / outcome / acknowledgement
replies. Those replies should sound like Jarvis reacting to *this* situation.

## Context

The deterministic spoken paths read fixed strings out of de/en/es lookup tables
(`jarvis/voice/action_phrases.py` + ~13 siblings). The maintainer flagged
`action_phrases.py` (the computer-use readbacks) as exactly the kind of canned
phrasing they do not want, and asked for all spoken status sentences to be
intelligent and context-fitting. The system already had the right machinery for
this — the Ack-Brain (a bounded, breaker-guarded flash-LLM call with an instant
deterministic fallback, see `jarvis/brain/ack_brain/`). This design generalizes
that proven pattern into one reusable readback engine and routes the canned call
sites through it.

Three hard constraints govern the design (they are *why* the tables were static):

1. **Latency (AP-11 / SLO).** No LLM call may live inside `scrub_for_voice`; the
   voice path is SLO-gated. Generation is one bounded flash call with a hard
   per-call timeout and an instant canned fallback; it runs BEFORE scrub, never
   inside it.
2. **Honesty (ADR-0009 / AD-OE6).** For success/observation readbacks the LLM may
   not invent what happened. The composer is handed the deterministic ground
   truth and told to rephrase only that; an honesty guard rejects fabrication. It
   never raises and never returns empty — on any miss it returns the canned line.
3. **Language.** The turn's resolved language (de/en/es) is passed in; a de/en
   mismatch is rejected to canned, and the canned fallback covers all three.

## The engine — `jarvis/voice/contextual_readback.py`

`ReadbackComposer` mirrors `SpawnAnnouncementComposer` (brain candidate → flash
compose → validate → deterministic fallback, one failover level, never-raise).
Single entry point used by every call site:

```
render_readback(composer, *, instruction, language, canned,
                facts=None, in_progress=False, honesty_bound=False,
                latency_budget_ms=None) -> str
```

- `composer is None` (feature unwired/disabled) → returns `canned()` verbatim, so
  wiring it in is risk-free / zero behavior change.
- `instruction`: one-line English description of the situation (call-site owned).
- `facts`: deterministic ground truth the model may rephrase — the ONLY thing it
  may use. Rendered into the persona prompt as a FACTS block.
- `in_progress=True` rejects a completion claim (the dispatch ack, work not started).
- `honesty_bound=True` adds a content-word overlap guard (≥60% of the output's
  ≥4-char words must trace to the facts) for ADR-0009 success/observation readbacks.
- `latency_budget_ms`: tight on the turn-critical path (dispatch ack ~900ms),
  generous off it (background outcome/mission readbacks ~2000–2500ms).

Validation chain (any miss → canned): forbidden internal/diagnostic vocab
(exit codes, "subprocess", "provider", "API", …), sentence trim, completion-claim
(when in_progress), de/en language match, **digit-fabrication guard** (any number
not in the facts is rejected — the most dangerous hallucination), the overlap
guard (when honesty_bound), no-verbatim-repeat, then `scrub_for_voice` (regex
safety net) + ≥3 alnum. The persona is an English meta-prompt instructing the
model to answer in the user's language, so de/en/es all generate natively (it is
a NEW prompt, not the locked 2026-05-11 flash-brain persona).

Wiring: `jarvis/brain/factory.build_readback_composer()` builds it from the
ack-brain provider stack (own provider + breaker + separate-provider failover),
fallback-only when `[ack_brain]` is off.

## What became context-aware

- **Computer-use (the pasted table):** outcome readbacks (success/failure/
  exit-code/timeout/crash), the dispatch ack, budget guards, tool-failed,
  leak-recovery — all in `BrainManager` (`_run_local_action_fast_path`,
  `_run_computer_use_background`). Success is honesty_bound (rephrases the
  verifier's forwarded observation faithfully).
- **Mission readbacks (constantly heard):** approved / failed / timeout /
  cancelled / budget / killed / iteration — routed in BOTH the production path
  (`MissionAnnouncer`) and the fallback path (`MissionVoiceListener`). Approved is
  honesty_bound (rephrases the Kontrollierer-signed `summary_de/en`); see the
  ADR-0009 amendment 2026-06-28.

## Deliberately left canned (documented exceptions)

- **Engine-down / in-scrub (Tier-3, by design):** provider-down, brain-unavailable,
  brain-timeout, STT-unavailable, and `output_filter.FALLBACK_PHRASES`. You cannot
  ask a dead/timed-out brain to phrase its own outage, and the in-scrub fallback is
  forbidden an LLM call (AP-11).
- **`config_readback`** (voice config-change confirmation): honesty-critical
  ("don't confirm something that wasn't done"), already names the concrete setting
  + value (barely a "stock phrase"), and sits on the turn-critical tool loop. Kept
  deterministic; revisit if desired (threading a composer through the dispatcher).
- **`open_app` DIRECT ack** ("Gestartet: Chrome"): the deliberately fastest path
  (no-LLM by design) that already names the concrete app. Kept fast.
- **Harness-internal pause prompts** (`cu_awaiting_elevation`/`cu_awaiting_human`):
  produced inside the harness subprocess (cannot reach the in-process composer),
  and clarity beats variety when asking the user to confirm a security prompt.

## Verification

- `tests/unit/voice/test_contextual_readback.py` (16): fallback-only, generated-used,
  es generation, timeout/error/empty/wrong-language → canned, fabricated-number
  rejected, number-from-facts allowed, honesty overlap reject/accept, in-progress
  completion-claim rejected, forbidden vocab rejected, never-raises-when-canned-raises.
- `tests/missions/test_voice_announcer.py` (+2): approved readback is contextually
  rephrased yet faithful; falls back to the exact signed summary when generation fails.
- Regression: full `tests/unit/voice` + `tests/missions` green except pre-existing
  unrelated failures (grok worker-routing, google-calendar usage-card — parallel
  in-flight work). Lint clean on all new/owned files.
- Manual: a computer-use turn ("open the browser and check my tabs") and a failing
  one — the spoken readback fits the situation, in the turn's language; pulling the
  flash provider falls back cleanly to the canned line. Restart via
  `POST /api/settings/restart-app` to load.
