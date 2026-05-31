# ADR-0023 — Hybrid native Gemini Computer-Use engine

**Status:** Accepted (2026-05-29) — implemented behind a default-OFF flag.
**Context plan:** `~/.claude/plans/goofy-singing-piglet.md` (Computer-Use rework, Wave 3).

## Context

The Computer-Use loop (`jarvis/harness/screenshot_only_loop.py`, POAV) decides each
step with a **hand-rolled** vision+JSON prompt: it sends a screenshot + goal to the
active brain (`gemini-3.5-flash`) and parses a JSON action. Google ships a **native
`computer_use` tool** (models `gemini-3-flash-preview` and
`gemini-2.5-computer-use-preview-10-2025`) that is trained for precise on-screen
grounding and returns predefined UI-action `FunctionCall`s. The maintainer asked to
"use the power of the CU model" without losing provider independence (multi-provider
doctrine, no vendor lock).

Two empirical findings shaped the decision (verified against `google-genai 1.67.0` +
`ai.google.dev/gemini-api/docs/computer-use`):

1. **Native CU coordinates are a 0-1000 normalized grid** — the EXACT grid the loop
   already uses (`_resolve_click_pixel`). Native actions therefore execute through the
   existing `_execute_action` backend with no coordinate translation.
2. **Native CU only exposes `ENVIRONMENT_BROWSER`** — it is browser-oriented. The
   generic actions (`click_at`, `type_text_at`, `key_combination`, `scroll_*`) work on
   any screenshot incl. the desktop; the browser-navigation functions (`navigate`,
   `go_back`, `go_forward`, `search`) and actions the loop cannot express
   (`drag_and_drop`, `hover_at`) do not.

## Decision

Add the native engine as a **per-step alternative** to the hand-rolled decision,
gated behind `[computer_use].prefer_native` (default **False**), provider-restricted
to Gemini, with a hand-rolled fallback on ANY failure.

- **Adapter:** `jarvis/harness/native_computer_use.py`.
  - `map_native_action(name, args) -> list[dict]` — pure mapping of a Gemini CU
    `FunctionCall` to the loop's action vocabulary. Unsupported actions map to `[]`.
  - `GeminiNativeCU.from_config(cfg)` — returns an engine only when
    `prefer_native` is on AND `brain.primary == "gemini"`, else `None`.
  - `GeminiNativeCU.decide(...)` — calls the `computer_use` tool with
    `ENVIRONMENT_BROWSER` and `EXCLUDED_BROWSER_FUNCTIONS` (the browser-nav +
    unmappable functions), maps the first `FunctionCall`, returns `None` on any error.
- **Seam:** `screenshot_only_loop._decide_native_batch(...)` is called before the
  hand-rolled `_call_brain` + `_parse_actions`. Native results are re-validated via
  `_validate_action_dict` (defense-in-depth) and, when present, used; otherwise the
  loop runs the hand-rolled path unchanged.
- **Context wiring:** `ComputerUseContext.native_cu` (built in `jarvis/brain/factory.py`).
- **App launch stays deterministic:** `open_app` remains the loop's launch primitive;
  native CU handles in-app grounding (click/type/scroll/key).

## Consequences

- **Zero regression by construction:** default `prefer_native=False` → `native_cu is
  None` → the seam is a no-op; any native failure falls back per-step. Enabling the
  flag can never make the loop worse than the hand-rolled default.
- **Honest limitation:** native CU is browser-scoped and preview. It is enabled only
  after a live smoke against the CU model on the user's account. For desktop-heavy
  goals the hand-rolled loop remains the primary engine.
- **Provider independence preserved:** the flag is Gemini-only and optional; every
  other provider (and Gemini with the flag off) uses the portable hand-rolled loop.
- **Runtime provider switch:** `native_cu` is built at brain-construction time, so a
  runtime "switch to Gemini" does not retroactively enable native CU until the next
  build/restart. Acceptable for an opt-in, default-off feature.

## Regression guards

- `tests/unit/harness/test_native_computer_use.py` — exhaustive `map_native_action`
  cases, `from_config` gating, `decide()` with an injected fake client + error/empty
  fallback, and the `_decide_native_batch` seam (default no-op + native-used +
  invalid-action fallback).
- `tests/unit/harness/test_screenshot_only_loop.py` — unchanged hand-rolled path stays
  green (the seam guards on `ctx.native_cu`).
