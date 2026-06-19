# Spec — Intelligent Vision Gating (Hybrid)

**Date:** 2026-05-31
**Status:** Approved (design), pre-implementation
**Owner:** (maintainer)

## Problem

The router brain attaches a fresh screenshot to (almost) every conversational
turn. A large, fresh image competes with — and in practice overrides — the text
conversation history in the model's attention. Symptom reported by the user:
within one voice session, asking "what did we just talk about?" gets an answer
based on the current screen, not the conversation. The model "forgets" because
it is fixated on the screenshot.

### Root cause (verified, file:line)

- `jarvis/brain/vision_gate.py:44` — `should_attach_screenshot()` returns `True`
  for **every non-smalltalk turn** unconditionally. A content question like
  "was haben wir besprochen?" is not smalltalk → image is always attached.
- The image rides on the current turn's user message only (`dispatcher.py:106`),
  not persisted to `_history`. The conversation history (text) IS present, but
  the prominent image dominates attention.
- The conversation-memory mechanism itself works (proven: 2-turn "remember 47"
  test, `_history` 0→2→4, context retained). So this is NOT a history bug — it
  is an over-eager vision-injection bug.

### Live config (verified)

- `[brain.router.vision].enabled = true` → router vision IS active.
- `performance.conditional_vision` unset → defaults to `True` → the gate runs.
- `[vision].enabled = false` is the **legacy/global** section, unrelated.
- `max_image_kb = 500`.

## Goal

Jarvis decides, per turn, whether it actually needs a screenshot — instead of
attaching one by default. Conversation context stays primary; the screen is
consulted only when relevant. Must avoid the 2026-04-28 regression (aggressive
skip → router hallucinates a blank desktop).

## Design — two waves

### Wave 1 — Invert the heuristic (skip-by-default)

`jarvis/brain/vision_gate.py::should_attach_screenshot()`:

- **Today:** attach UNLESS (is_smalltalk AND no visual marker) → almost always attach.
- **New:** attach ONLY IF the utterance carries a clear screen reference.
  Smalltalk vs. content-question no longer forces the image; only an explicit
  visual reference does.
- Expand `_VISUAL_MARKERS` to cover the common real screen references that the
  current short list misses, so Wave 2 (the fallback) is rarely needed:
  - deictic: "hier", "da", "das da", "oben/unten links/rechts"
  - screen nouns: "bildschirm", "fenster", "seite", "fehlermeldung", "button",
    "knopf", "menü", "dialog", "tab", "zeile"
  - verbs/actions: "schau", "sieh", "guck", "zeig", "lies vor", "lies das",
    "klick", "scroll", "markier", "öffne das", "mach … zu"
  - diagnosis: "warum ist das", "was steht da", "was bedeutet das", "was ist das"
  - English equivalents.
- Keep the function signature (`text`, `is_smalltalk`) for backward
  compatibility with the existing call site; `is_smalltalk` becomes an optional
  secondary signal (a non-smalltalk turn no longer auto-attaches).
- The `conditional_vision` flag remains the master switch (legacy = always-attach
  when False).

**Tests** (`tests/integration/test_conditional_vision.py` + new unit tests for
`vision_gate`):
- KEEP green: "was siehst du hier" → image; "schau mal das hier" → image.
- NEW: "was haben wir besprochen?" → NO image (the user's exact case).
- NEW: "erklär mir nochmal X" → NO image.
- NEW: "wie spät ist es" → NO image.
- NEW: "warum ist das rot?" → image (diagnosis marker).
- NEW: "klick auf den Button" → image.

### Wave 2 — Image-as-a-tool (the safety net)

`jarvis/brain/tool_use_loop.py:510` currently discards a tool's image artifacts
(serializes only `{success, output, error}`). Fix:

- After a tool returns image artifacts (e.g. the existing `screenshot` /
  `look_at_screen` tool), feed the image back into the conversation so the brain
  can actually see it on its next iteration.
- Gemini accepts images only on `user`-role messages (`gemini.py` `_to_gemini_contents`
  attaches `inline_data` only for user role), and `tool`-role becomes a
  `functionResponse` with no image support. Therefore: after the tool-result
  message, append a synthetic `user`-role `BrainMessage` carrying the captured
  image (`images=(ImageBlock(...),)`) with a short text like "Screenshot:".
- Confirm a screen-look tool is in `ROUTER_TOOLS` and add one line to the router
  system prompt: "If you are unsure whether you need to see the screen, call
  `look_at_screen`."

**Effect:** if Wave 1's heuristic misses a real screen reference, the brain can
pull the image itself — no per-turn image tax, no blank-desktop hallucination.

**Tests:**
- A tool returning an image artifact results in an `images`-bearing user message
  appended before the next brain iteration.
- A text-only tool result is unchanged (no synthetic image message).

## Out of scope (YAGNI / safety)

- No extra mini-model classifier call (latency on the voice path).
- VisionContextProvider background loop unchanged.
- Images stay non-persistent in `_history` (correct).
- screenshot tool risk tier unchanged (monitor/safe).

## Files touched

- `jarvis/brain/vision_gate.py` (Wave 1 — logic + markers)
- `tests/integration/test_conditional_vision.py` + `tests/unit/brain/test_vision_gate.py` (Wave 1)
- `jarvis/brain/tool_use_loop.py` (Wave 2 — artifact→image wiring)
- `jarvis/brain/router.py` (Wave 2 — one prompt line) + `jarvis/brain/factory.py` (verify tool in ROUTER_TOOLS)
- `tests/unit/brain/test_tool_use_loop_image_feedback.py` (Wave 2)

## Verification

- `pytest tests/integration/test_conditional_vision.py tests/unit/brain/test_vision_gate.py tests/unit/brain/test_tool_use_loop_image_feedback.py -v`
- Manual: voice session — "merk dir die Zahl 12" → "und 5 dazu" → "welche Zahl?"
  answers from conversation, no screen fixation; "was siehst du auf dem
  Bildschirm?" still works.
