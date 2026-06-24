# Couple the Assistant Name to the Wake Word

**Date:** 2026-06-20
**Status:** Approved design, pending implementation plan
**Author:** brainstorming session

## Problem

The assistant currently has two independent identity controls:

- `[trigger.wake_word].phrase` — the spoken trigger ("Hey Jarvis").
- `[persona].name` — an explicit, separate override for how the assistant refers
  to itself ("Friday"), surfaced as the "Assistant Name" Settings panel and a
  dedicated onboarding step.

These were deliberately decoupled on 2026-06-19 so a user could wake "Hey
Computer" while the assistant called itself "Friday". The maintainer has decided
to reverse that: the assistant name should be **derived solely from the wake
word**, with no separate name control anywhere. "Hey Ruben" → the assistant is
called "Ruben". Setting the wake word is the single act that names the assistant.

## Goal

Remove the separate assistant-name override entirely (UI, onboarding, REST write
path, and config field) and make the assistant name a pure function of the wake
phrase. Surface the coupling in the UI so the derived name is visible while the
user sets the wake word.

## Current architecture (what is coupled to what)

Everything that needs the assistant's name reads the single resolver
`resolve_assistant_name(config)` in `jarvis/brain/assistant_name.py`. Today it
resolves in three stages (first non-empty wins):

1. `[persona].name` explicit override.
2. Wake phrase with its trigger prefix stripped (`phrase_core`), title-cased.
3. `DEFAULT_ASSISTANT_NAME` ("Assistant") fallback.

Consumers (all unchanged by this work — they keep reading the resolver):

- `jarvis/brain/manager.py::_build_system_prompt` (~L1752) injects a name
  override directive into the system prompt when the resolved name ≠ "Assistant".
- `jarvis/brain/agent_instructions.py::instructions_filename` names the
  per-user instructions file `<Name>.md` and auto-migrates it on rename.
- Frontend bylines: `useAssistantNameSeed` seeds the resolved name into the
  event store; the Sidebar header, `RunTurnCard`, `TurnCard`,
  `FrontierSwitchModal`, and i18n interpolation all read it from the store.

Override entry points to be removed:

- Config field: `PersonaConfig.name` (`jarvis/core/config.py` ~L103).
- Writer: `config_writer.set_assistant_name()` (~L190).
- REST: `PUT`/`DELETE /api/settings/assistant-name`
  (`jarvis/ui/web/settings_routes.py` ~L639); `GET` is kept read-only.
- Settings UI: `AssistantNamePanel` in
  `frontend/src/views/SettingsView.tsx` (~L306) + the `useAssistantName`
  (save) hook in `frontend/src/hooks/useAssistantName.ts`.
- Onboarding: the `persona-theme` step — `PersonaThemeStep.tsx` (+ test) and its
  key in both `OnboardingFlow.tsx` `REGISTRY`/`STEP_KEYS` and
  `jarvis/setup/onboarding_meta.py` `ONBOARDING_STEPS`. (Note: despite its name,
  this step only sets the name; the on-screen theme/overlay is a separate
  `system-style` step and is untouched.)

## Desired behavior

The single source of the assistant name is the wake phrase. `resolve_assistant_name`
becomes two stages:

1. Wake phrase with prefix stripped, title-cased ("Hey Ruben" → "Ruben",
   "micron" → "Micron", "Hey Computer" → "Computer").
2. `DEFAULT_ASSISTANT_NAME` ("Assistant") when there is no usable wake phrase
   (pre-onboarding / empty).

There is no way to make the spoken name differ from the wake word. Wake word and
name are intentionally identical.

## Changes

### Backend (Python)

1. **`jarvis/brain/assistant_name.py`** — Drop stage 1 (the `[persona].name`
   read). Resolve from the wake phrase, else the fallback. Update the module
   docstring to describe the two-stage resolution and the removed override.

2. **`jarvis/core/config.py`** — Remove `PersonaConfig.name`. Verify the config
   model tolerates a now-unknown `[persona] name` key in existing TOML files
   (Pydantic v2 defaults to `extra="ignore"`; confirm no model on this path sets
   `extra="forbid"`). If `PersonaConfig` becomes empty and nothing else reads
   `cfg.persona`, the plan may drop the model and its `JarvisConfig.persona`
   field; if removal carries boot risk, keep the field as a dead, deprecated,
   never-read key instead. Decision deferred to the plan after a
   `grep` for `persona.name` / `.persona\b` readers outside the resolver.

3. **`jarvis/core/config_writer.py`** — Remove `set_assistant_name()`. In
   `set_wake_word()`, also strip a stale `[persona] name` entry from the TOML so
   a previously-set override (e.g. the maintainer's current "Josef") does not
   linger. Keep the existing `_WRITE_LOCK` + tempfile + BOM-safe path (AP-7).

4. **`jarvis/ui/web/settings_routes.py`** — Remove `PUT`/`DELETE
   /api/settings/assistant-name` and the `AssistantNameBody` model. Keep `GET
   /api/settings/assistant-name` but return only `resolved` (and `default`); drop
   the `name`/explicit field from the response. The frontend's
   `useAssistantNameSeed` keeps using this endpoint to seed the byline name.

5. **`jarvis/setup/onboarding_meta.py`** — Remove `"persona-theme"` from
   `ONBOARDING_STEPS`.

6. **No changes** to `manager.py::_build_system_prompt` or
   `agent_instructions.py` — they read the resolver and inherit the new behavior,
   including the `<Name>.md` auto-rename when the wake word changes.

### Frontend (TypeScript)

7. **`SettingsView.tsx`** — Remove `AssistantNamePanel` and its render site.

8. **`hooks/useAssistantName.ts`** — Remove (the save hook is now dead). Keep
   `hooks/useAssistantNameSeed.ts` (read-only seed; still needed for bylines).

9. **Onboarding** — Delete `steps/PersonaThemeStep.tsx` and its test; remove
   `persona-theme`/`PersonaThemeStep` from `OnboardingFlow.tsx` `REGISTRY`
   (which derives `STEP_KEYS`).

10. **Wake-word live preview (the new visibility)** — In the wake-word surfaces,
    show a live, derived-name hint as the user types:
    - Onboarding `steps/WakeWordStep.tsx`: below the "Hey ___" input, render
      e.g. "Your assistant will be called: **Ruben**". The derivation mirrors
      the backend (`phrase_core` prefix-strip + title-case); since the onboarding
      input is the bare word with a fixed "Hey " prefix, the preview is the
      title-cased trimmed input (empty input → no hint / the neutral fallback).
    - Settings wake-word panel: the same live hint next to the phrase field.
    - Add i18n keys (en/de/es source per the Output Language Policy; English is
      the source string) such as `onboarding.wake_word.derived_name` and
      `settings.wake_word.derived_name`. The obsolete `onboarding.persona.*` keys
      are removed.

### Tests

11. **`tests/unit/brain/test_assistant_name.py`** — Invert the override tests:
    a `[persona].name` value must now be **ignored**; the wake phrase always
    wins ("Hey Computer" + persona "Friday" → "Computer"). Keep/extend the
    derivation + fallback cases.

12. **`tests/unit/brain/test_system_prompt_name.py`** — Update the
    "explicit persona name wins" test to assert the wake phrase wins instead.

13. **Onboarding parity** — Update the cross-layer parity test that asserts
    frontend `STEP_KEYS` == backend `ONBOARDING_STEPS` for the removed step.
    Find it via the comment in `OnboardingFlow.tsx` ("cross-layer parity test").

14. **Frontend** — Delete `PersonaThemeStep.test.tsx`; update
    `OnboardingFlow.test.tsx` if it references the step; add a test for the
    wake-word live-preview hint (Settings and onboarding).

## Migration / compatibility

- Existing `jarvis.toml` with `[persona] name = "X"`: the key is ignored after
  this change, and the next wake-word save removes it. The assistant's name
  becomes whatever the wake word derives to. A user who wants the old name keeps
  it by setting the wake word accordingly ("Hey X"). We do not auto-rewrite the
  wake word from the old persona name — the wake word is left untouched.
- `<Name>.md` agent-instructions file: continues to auto-migrate on the next read
  when the resolved name changes (existing behavior in `agent_instructions.py`).
- Onboarding state that already recorded `persona-theme` in `steps`/`current_step`
  (e.g. a maintainer re-running via `?onboarding=force`): the backend now serves
  the shorter step list; the frontend renders from `onb.state.steps`, so a stale
  client step list is the only edge — acceptable, as onboarding is first-run and
  the maintainer can force-replay.

## Out of scope

- No change to the wake-word engine/sensitivity/fuzzy-match settings.
- No change to the TTS voice, language resolution, or the system-style/overlay
  onboarding step.
- No retroactive rename of already-recorded session bylines in stored history.

## Anti-patterns to respect

- Config writes stay on `config_writer` (`_WRITE_LOCK` + tempfile + BOM-safe) —
  AP-7.
- Removing a config field must not trip the pre-validate/boot path — verify
  `extra` handling rather than assuming (AP-16 is about *adding* keys; this is
  removal, but the boot-safety concern is the mirror image).
- Keep frontend/backend onboarding step lists in lockstep — the parity test is
  the guard (multi-layer drift, BUG-008 family).
