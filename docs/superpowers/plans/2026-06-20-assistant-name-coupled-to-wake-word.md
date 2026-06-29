# Couple Assistant Name to Wake Word — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make the assistant's name a pure function of the wake phrase, removing the separate `[persona].name` override from the resolver, config, REST write path, Settings UI, and onboarding — and surface the derived name live where the wake word is set.

**Architecture:** Everything that needs the name already reads one resolver, `resolve_assistant_name(config)`. We cut its first stage (the persona override) so the name always derives from the wake phrase; all downstream consumers (system prompt, the `<Name>.md` agent-instructions file, frontend bylines) inherit the change unchanged. Then we delete the now-dead override entry points and add a live "Your assistant will be called: X" hint mirroring the backend derivation.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, tomlkit (`config_writer`); React + TypeScript + Vitest; i18n JSON (en/de/es).

**Spec:** `docs/superpowers/specs/2026-06-20-assistant-name-coupled-to-wake-word-design.md`

**Commit policy for this repo:** The working tree is shared with parallel sessions. Each commit step stages ONLY that task's explicit paths (hunk-isolated) — never `git add -A`. Commits happen only on the maintainer's go; if running unattended, complete the code + tests and leave committing to the maintainer.

---

## Task 1: Simplify the resolver — wake phrase always wins

**Files:**
- Modify: `jarvis/brain/assistant_name.py`
- Test: `tests/unit/brain/test_assistant_name.py`

- [ ] **Step 1: Rewrite the override tests to assert the wake phrase wins**

In `tests/unit/brain/test_assistant_name.py`, replace the "Explicit override wins" section (the `test_explicit_persona_name_overrides_wake_phrase` and `test_explicit_name_is_trimmed` tests, lines ~42-53) with tests proving a `[persona].name` value is now IGNORED:

```python
# ----------------------------------------------------------------------
# The wake phrase is the single source — a legacy [persona].name is ignored
# ----------------------------------------------------------------------

def test_legacy_persona_name_is_ignored_in_favor_of_wake_phrase():
    # A stale override from before the coupling must NOT win anymore.
    cfg = _cfg(persona_name="Friday", wake_phrase="Hey Computer")
    assert resolve_assistant_name(cfg) == "Computer"


def test_legacy_persona_name_alone_does_not_name_the_assistant():
    # No wake phrase + a stale persona name → fall back, do not use the override.
    assert resolve_assistant_name(_cfg(persona_name="Friday", wake_phrase="")) == DEFAULT_ASSISTANT_NAME
```

The derivation + fallback tests (`test_name_derived_from_wake_phrase`, the four fallback tests, `test_whitespace_only_phrase_falls_back`) stay unchanged and must keep passing.

- [ ] **Step 2: Run the tests to verify the two new ones fail**

Run: `pytest tests/unit/brain/test_assistant_name.py -v`
Expected: `test_legacy_persona_name_is_ignored_in_favor_of_wake_phrase` and `test_legacy_persona_name_alone_does_not_name_the_assistant` FAIL (resolver still returns "Friday"); all others PASS.

- [ ] **Step 3: Drop stage 1 from the resolver**

In `jarvis/brain/assistant_name.py`, replace the module docstring's resolution list and the `resolve_assistant_name` body so the persona override is gone. The function becomes:

```python
"""Resolve the assistant's own name (how it refers to itself).

The name is a pure function of the wake phrase — there is no separate name
setting. Resolution order (first non-empty wins):
  1. The wake phrase with its trigger prefix stripped — "Hey Jarvis" -> "Jarvis",
     "Micron" -> "Micron", "Hey Athena" -> "Athena", "Hey Computer" -> "Computer".
  2. ``DEFAULT_ASSISTANT_NAME`` — the neutral shipped fallback when no wake phrase
     is set (pre-onboarding state). Not "Jarvis", so the product imposes no name.

A legacy ``[persona].name`` key in an old jarvis.toml is intentionally ignored:
the wake word is the single control (see the 2026-06-20 coupling design).

Capitalisation: the derived name is title-cased token-by-token so a lowercase
wake phrase ("micron") still yields a proper name ("Micron").
"""
from __future__ import annotations

from typing import Any

DEFAULT_ASSISTANT_NAME = "Assistant"


def resolve_assistant_name(config: Any) -> str:
    """Return the assistant's display name from ``config`` (see module docstring)."""
    # 1. Derive from the wake phrase (prefix stripped, title-cased).
    trigger = getattr(config, "trigger", None)
    wake_word = getattr(trigger, "wake_word", None) if trigger is not None else None
    phrase = (getattr(wake_word, "phrase", "") or "") if wake_word is not None else ""
    if phrase:
        try:
            from jarvis.speech.wake_constants import phrase_core

            core = phrase_core(phrase)
        except Exception:  # noqa: BLE001 — never break name resolution on import/parse
            core = []
        if core:
            return " ".join(tok.capitalize() for tok in core)

    # 2. Historical default / safety fallback.
    return DEFAULT_ASSISTANT_NAME
```

- [ ] **Step 4: Run the tests to verify all pass**

Run: `pytest tests/unit/brain/test_assistant_name.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit (on the maintainer's go)**

```bash
git add jarvis/brain/assistant_name.py tests/unit/brain/test_assistant_name.py
git commit -m "refactor(name): derive assistant name solely from the wake phrase"
```

---

## Task 2: Update the system-prompt test BEFORE the config field is removed

**Files:**
- Test: `tests/unit/brain/test_system_prompt_name.py`

> This must land before Task 3. The helper currently does `cfg.persona.name = persona_name`; once the Pydantic field is gone, that assignment raises. We remove the persona-name path from the test here.

- [ ] **Step 1: Rewrite the helper and the override test**

In `tests/unit/brain/test_system_prompt_name.py`, change `_manager_with_name` to drop the `persona_name` parameter and the `cfg.persona.name` assignment, and replace the "explicit persona name wins" test with one proving the wake phrase wins:

Replace the helper (lines ~14-30):

```python
def _manager_with_name(*, wake_phrase: str = "Hey Jarvis") -> BrainManager:
    """A BrainManager with __init__ bypassed — only the attrs the prompt needs."""
    m = BrainManager.__new__(BrainManager)
    m._soul = None
    m._user_profile = None
    m._people = None
    m._core_memory = None
    m._awareness_manager = None
    m._system_prompt_extra = "ROUTER DISCIPLINE BLOCK"
    m._wiki_context_suffix = ""
    m._reply_language = "auto"
    cfg = load_config()
    cfg.performance.cache_optimized_prompt = False
    cfg.trigger.wake_word.phrase = wake_phrase
    m._config = cfg
    return m
```

Replace `test_explicit_persona_name_wins_over_wake_phrase` (lines ~48-53) with:

```python
def test_wake_phrase_is_the_only_name_source() -> None:
    # "Hey Computer" wake → the assistant is "Computer"; there is no override.
    prompt = _manager_with_name(wake_phrase="Hey Computer")._build_system_prompt()
    assert "Du bist Computer" in prompt
    assert "DEIN NAME IST COMPUTER" in prompt
    assert "nicht Jarvis" in prompt
```

The other two tests (`test_default_name_keeps_jarvis_and_no_identity_directive`,
`test_wake_phrase_micron_makes_assistant_micron`) stay unchanged.

- [ ] **Step 2: Run the test**

Run: `pytest tests/unit/brain/test_system_prompt_name.py -v`
Expected: ALL PASS (the resolver from Task 1 already derives from the wake phrase; this test no longer touches `persona.name`).

- [ ] **Step 3: Commit (on the maintainer's go)**

```bash
git add tests/unit/brain/test_system_prompt_name.py
git commit -m "test(name): system prompt name comes from the wake phrase only"
```

---

## Task 3: Remove the `PersonaConfig.name` field

**Files:**
- Modify: `jarvis/core/config.py:103-114`

> `PersonaConfig` has no `model_config`, so Pydantic v2 defaults to `extra="ignore"`: an old `jarvis.toml` with `[persona] name = "X"` still validates cleanly (the key is dropped). We keep the (now empty) class and the `JarvisConfig.persona` field so no `cfg.persona` reader crashes.

- [ ] **Step 1: Confirm no code reads `persona.name` as a hard attribute**

Run: `git grep -n "persona\.name\|persona_name" -- "jarvis/**/*.py"`
Expected: the only remaining hits are `getattr(persona, "name", "")` (safe — the GET route, fixed in Task 4) and nothing that does a bare `cfg.persona.name` read. If a bare read exists outside a test, convert it to `getattr(..., "name", "")` in this step.

- [ ] **Step 2: Empty out the class**

In `jarvis/core/config.py`, replace the `PersonaConfig` definition (lines ~103-114) with:

```python
class PersonaConfig(BaseModel):
    """Reserved ``[persona]`` table. The assistant's name is no longer stored
    here — it derives solely from the wake phrase (see
    ``jarvis.brain.assistant_name.resolve_assistant_name`` and the 2026-06-20
    coupling design). A legacy ``[persona] name`` key in an existing jarvis.toml
    is ignored (Pydantic ``extra="ignore"``); the next wake-word save strips it.
    """
```

(An empty Pydantic model body needs no `pass` — the docstring is the body.)

- [ ] **Step 3: Verify the config still loads with a legacy key**

Run:
```bash
python -c "from jarvis.core.config import PersonaConfig; PersonaConfig.model_validate({'name': 'Josef'}); print('ok: legacy [persona] name ignored')"
```
Expected: prints `ok: legacy [persona] name ignored` (no validation error, and the instance has no `name` attribute).

- [ ] **Step 4: Run the brain name tests again (regression)**

Run: `pytest tests/unit/brain/test_assistant_name.py tests/unit/brain/test_system_prompt_name.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit (on the maintainer's go)**

```bash
git add jarvis/core/config.py
git commit -m "refactor(config): drop [persona].name — name derives from the wake word"
```

---

## Task 4: Remove the write path; keep GET read-only; strip the dead key on wake-word save

**Files:**
- Modify: `jarvis/core/config_writer.py` (remove `set_assistant_name`; extend `set_wake_word`)
- Modify: `jarvis/ui/web/settings_routes.py:612-671`
- Test: `tests/unit/core/test_config_writer.py` (add a stripping test if the file exists; otherwise create `tests/unit/core/test_config_writer_persona_strip.py`)

- [ ] **Step 1: Write a failing test for the dead-key strip on wake-word save**

Create `tests/unit/core/test_config_writer_persona_strip.py`:

```python
"""set_wake_word() removes a stale [persona] name so a legacy override can't linger."""
from __future__ import annotations

from pathlib import Path

from jarvis.core import config_writer


def test_set_wake_word_strips_legacy_persona_name(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        "[persona]\nname = \"Josef\"\n\n[trigger.wake_word]\nphrase = \"Hey Jarvis\"\n",
        encoding="utf-8",
    )

    config_writer.set_wake_word("Hey Alex", path=toml)

    text = toml.read_text(encoding="utf-8")
    assert "Hey Alex" in text
    # The stale identity override is gone; the wake word is now the single source.
    assert "Josef" not in text


def test_set_wake_word_without_persona_table_is_a_noop_strip(tmp_path: Path) -> None:
    toml = tmp_path / "jarvis.toml"
    toml.write_text("[trigger.wake_word]\nphrase = \"Hey Jarvis\"\n", encoding="utf-8")

    config_writer.set_wake_word("Hey Nova", path=toml)  # must not raise

    assert "Hey Nova" in toml.read_text(encoding="utf-8")
```

- [ ] **Step 2: Run it to verify failure**

Run: `pytest tests/unit/core/test_config_writer_persona_strip.py -v`
Expected: `test_set_wake_word_strips_legacy_persona_name` FAILS ("Josef" still present).

- [ ] **Step 3: Remove `set_assistant_name` and strip the key in `set_wake_word`**

In `jarvis/core/config_writer.py`:

(a) Delete the entire `set_assistant_name` function (lines ~233-243).

(b) Add a small private helper next to the other `_patch_*` helpers (e.g. right after `_patch_wake_word_toml`, ~line 1075). It mirrors `_patch_table`'s exact read/BOM/parse/dump/`_atomic_write` shape — reuse the module's `_WRITE_LOCK`, `_BOM`, and `_atomic_write`; do NOT introduce a second TOML I/O path (AP-7):

```python
def _strip_persona_name(path: Path) -> None:
    """Remove a stale ``[persona] name`` entry (the legacy assistant-name override).

    The wake word is now the single name source, so a leftover ``[persona] name``
    from before the 2026-06-20 coupling must not linger. Best-effort: a missing
    file/table/key is a no-op. Preserves comments and the optional BOM, exactly
    like :func:`_patch_table`.
    """
    if not path.exists():
        return

    with _WRITE_LOCK:
        raw = path.read_text(encoding="utf-8")
        had_bom = raw.startswith(_BOM)
        if had_bom:
            raw = raw[len(_BOM):]
        doc: TOMLDocument = tomlkit.parse(raw)
        persona = doc.get("persona")
        if persona is None or "name" not in persona:
            return
        del persona["name"]
        out = tomlkit.dumps(doc)
        if had_bom:
            out = _BOM + out
        _atomic_write(path, out)
```

Then call it at the end of `set_wake_word` (after the existing `_patch_wake_word_toml(path, values)` line — `_patch_wake_word_toml` releases `_WRITE_LOCK` before returning, so the re-acquire in `_strip_persona_name` is sequential, not nested: no deadlock):

```python
    _patch_wake_word_toml(path, values)
    try:
        _strip_persona_name(path)
    except Exception as exc:  # noqa: BLE001 — cleanup is best-effort, never breaks the save
        log.debug("persona-name strip skipped: %s", exc)
```

- [ ] **Step 4: Run the strip tests**

Run: `pytest tests/unit/core/test_config_writer_persona_strip.py -v`
Expected: BOTH PASS.

- [ ] **Step 5: Slim the REST endpoints**

In `jarvis/ui/web/settings_routes.py`:

(a) Delete the `AssistantNameBody` model (lines ~619-622).

(b) Delete the entire `put_assistant_name` handler (lines ~639-671).

(c) Replace the `get_assistant_name` handler (lines ~625-636) with a read-only version that returns only the resolved name:

```python
@router.get("/assistant-name")
async def get_assistant_name(request: Request) -> dict[str, object]:
    """The assistant's resolved name. Read-only: the name derives from the wake
    phrase (set via PUT /api/settings/wake-word), there is no separate control."""
    from jarvis.brain.assistant_name import DEFAULT_ASSISTANT_NAME, resolve_assistant_name

    cfg = _config(request)
    return {
        "resolved": resolve_assistant_name(cfg),
        "default": DEFAULT_ASSISTANT_NAME,
    }
```

(d) Update the section comment above it (lines ~612-616) to: `# Assistant name (read-only). The name derives from the wake phrase; GET exposes the resolved name for the frontend bylines. There is no write endpoint.`

- [ ] **Step 6: Verify the backend imports and the route still answers**

Run: `python -c "import jarvis.ui.web.settings_routes as r; print('import ok')"`
Expected: prints `import ok` (no reference to the deleted `set_assistant_name` / `AssistantNameBody`).

Run: `git grep -n "set_assistant_name\|AssistantNameBody\|put_assistant_name" -- jarvis`
Expected: no hits.

- [ ] **Step 7: Commit (on the maintainer's go)**

```bash
git add jarvis/core/config_writer.py jarvis/ui/web/settings_routes.py tests/unit/core/test_config_writer_persona_strip.py
git commit -m "refactor(settings): remove assistant-name write path; GET stays read-only; strip stale key on wake-word save"
```

---

## Task 5: Remove the `persona-theme` onboarding step (backend)

**Files:**
- Modify: `jarvis/setup/onboarding_meta.py:18-28`
- Test: `tests/unit/setup/test_onboarding_meta.py`

- [ ] **Step 1: Update the parity test first**

In `tests/unit/setup/test_onboarding_meta.py`, replace the step-order assertions (lines ~9-14) with:

```python
    assert "wake-word" in m.ONBOARDING_STEPS
    assert "persona-theme" not in m.ONBOARDING_STEPS
    # The "system-style" overlay chooser now sits right after mic-test and right
    # before finish (the persona-theme name step was removed 2026-06-20).
    assert m.ONBOARDING_STEPS.index("system-style") == m.ONBOARDING_STEPS.index("mic-test") + 1
    assert m.ONBOARDING_STEPS[-2] == "system-style"
    assert len(m.ONBOARDING_STEPS) == 8
```

- [ ] **Step 2: Run it to verify failure**

Run: `pytest tests/unit/setup/test_onboarding_meta.py -v`
Expected: `test_meta_constants` FAILS (`persona-theme` still present, len == 9).

- [ ] **Step 3: Remove the step from the canonical list**

In `jarvis/setup/onboarding_meta.py`, delete the `"persona-theme",` line from `ONBOARDING_STEPS` (line ~25), leaving:

```python
ONBOARDING_STEPS: list[str] = [
    "welcome",
    "terms",
    "language",
    "wake-word",
    "api-keys",
    "mic-test",
    "system-style",
    "finish",
]
```

- [ ] **Step 4: Run the test**

Run: `pytest tests/unit/setup/test_onboarding_meta.py -v`
Expected: ALL PASS.

- [ ] **Step 5: Commit (on the maintainer's go)**

```bash
git add jarvis/setup/onboarding_meta.py tests/unit/setup/test_onboarding_meta.py
git commit -m "feat(onboarding): drop the separate persona-name step"
```

---

## Task 6: Frontend — shared `deriveAssistantName` helper (mirrors the backend)

**Files:**
- Create: `jarvis/ui/web/frontend/src/lib/deriveAssistantName.ts`
- Test: `jarvis/ui/web/frontend/src/lib/deriveAssistantName.test.ts`

- [ ] **Step 1: Write the failing test**

Create `jarvis/ui/web/frontend/src/lib/deriveAssistantName.test.ts`:

```typescript
import { describe, expect, it } from "vitest";
import { deriveAssistantName } from "./deriveAssistantName";

describe("deriveAssistantName", () => {
  it("strips a wake prefix and title-cases", () => {
    expect(deriveAssistantName("Hey Alex")).toBe("Alex");
    expect(deriveAssistantName("hey computer")).toBe("Computer");
    expect(deriveAssistantName("ok friday")).toBe("Friday");
    expect(deriveAssistantName("Micron")).toBe("Micron");
    expect(deriveAssistantName("micron")).toBe("Micron");
  });

  it("returns empty string for blank input", () => {
    expect(deriveAssistantName("")).toBe("");
    expect(deriveAssistantName("   ")).toBe("");
  });

  it("keeps an all-prefix phrase rather than emptying it", () => {
    // mirrors backend phrase_core: never returns empty for a non-empty phrase
    expect(deriveAssistantName("Hey")).toBe("Hey");
  });
});
```

- [ ] **Step 2: Run it to verify failure**

Run (from `jarvis/ui/web/frontend/`): `npm run test -- deriveAssistantName`
Expected: FAIL ("Cannot find module './deriveAssistantName'").

- [ ] **Step 3: Implement the helper**

Create `jarvis/ui/web/frontend/src/lib/deriveAssistantName.ts`:

```typescript
/**
 * Derive the assistant's display name from a wake phrase — the TS mirror of
 * `jarvis.speech.wake_constants.phrase_core` + the title-casing in
 * `jarvis.brain.assistant_name.resolve_assistant_name`. Used for the live
 * "Your assistant will be called: X" hint while the user sets the wake word.
 *
 * Keep in lockstep with the backend WAKE_PREFIXES set.
 */
const WAKE_PREFIXES = new Set([
  "hey", "hi", "ok", "okay", "hello", "hallo", "yo", "hej",
]);

export function deriveAssistantName(phrase: string): string {
  // normalize_phrase: lower-case, punctuation→space, split (keeps umlauts/ß).
  const tokens = (phrase || "")
    .toLowerCase()
    .replace(/[^0-9a-zäöüß]+/g, " ")
    .trim()
    .split(/\s+/)
    .filter(Boolean);
  if (tokens.length === 0) return "";

  // phrase_core: drop leading wake prefixes, but never empty a non-empty phrase.
  let core = [...tokens];
  while (core.length > 0 && WAKE_PREFIXES.has(core[0])) core.shift();
  if (core.length === 0) core = tokens;

  return core.map((tok) => tok.charAt(0).toUpperCase() + tok.slice(1)).join(" ");
}
```

- [ ] **Step 4: Run the test**

Run (from `jarvis/ui/web/frontend/`): `npm run test -- deriveAssistantName`
Expected: ALL PASS.

- [ ] **Step 5: Commit (on the maintainer's go)**

```bash
git add jarvis/ui/web/frontend/src/lib/deriveAssistantName.ts jarvis/ui/web/frontend/src/lib/deriveAssistantName.test.ts
git commit -m "feat(ui): add deriveAssistantName helper mirroring the backend"
```

---

## Task 7: Frontend Settings — remove the name panel, add the wake-word live preview

**Files:**
- Modify: `jarvis/ui/web/frontend/src/views/SettingsView.tsx`

- [ ] **Step 1: Remove the AssistantNamePanel and its usage**

In `SettingsView.tsx`:

(a) Delete the import on line ~33: `import { useAssistantName } from "@/hooks/useAssistantName";`

(b) Delete the `<AssistantNamePanel />` render on line ~70.

(c) Delete the entire `AssistantNamePanel` function (lines ~300-389, the block from its doc-comment through its closing brace).

(d) If `Bot` (the lucide icon used only by that panel) is now unused, remove it from the lucide import to keep `tsc`/lint clean. Verify with a search for `Bot` in the file before removing.

- [ ] **Step 2: Add the live derived-name preview to WakeWordPanel**

In the `WakeWordPanel` function, add the import at the top of the file (near the other `@/lib` imports):

```typescript
import { deriveAssistantName } from "@/lib/deriveAssistantName";
```

Then, inside `WakeWordPanel`, compute the derived name from the live `phrase` state and render a hint right after the phrase `<input>` (after line ~194, before the engine `<label>`):

```tsx
          {(() => {
            const derived = deriveAssistantName(phrase);
            return derived ? (
              <p className="mt-1.5 text-xs text-muted-foreground">
                {t("settings_view.wake_word.derived_name").replace("{0}", derived)}
              </p>
            ) : null;
          })()}
```

- [ ] **Step 3: Type-check and build**

Run (from `jarvis/ui/web/frontend/`): `npx tsc -b`
Expected: no errors (no dangling `useAssistantName` / `AssistantNamePanel` / `Bot` references).

- [ ] **Step 4: Commit (on the maintainer's go)**

```bash
git add jarvis/ui/web/frontend/src/views/SettingsView.tsx
git commit -m "feat(settings): drop the assistant-name panel; show derived name under the wake word"
```

---

## Task 8: Frontend Onboarding — delete the persona step, wire the wake-word preview

**Files:**
- Delete: `jarvis/ui/web/frontend/src/components/onboarding/steps/PersonaThemeStep.tsx`
- Delete: `jarvis/ui/web/frontend/src/components/onboarding/steps/PersonaThemeStep.test.tsx`
- Modify: `jarvis/ui/web/frontend/src/components/onboarding/OnboardingFlow.tsx`
- Modify: `jarvis/ui/web/frontend/src/components/onboarding/OnboardingFlow.test.tsx`
- Modify: `jarvis/ui/web/frontend/src/components/onboarding/steps/WakeWordStep.tsx`

- [ ] **Step 1: Delete the persona step files**

```bash
git rm jarvis/ui/web/frontend/src/components/onboarding/steps/PersonaThemeStep.tsx \
       jarvis/ui/web/frontend/src/components/onboarding/steps/PersonaThemeStep.test.tsx
```

- [ ] **Step 2: Remove it from the REGISTRY**

In `OnboardingFlow.tsx`:

(a) Delete the import on line ~11: `import { PersonaThemeStep } from "./steps/PersonaThemeStep";`

(b) Delete the `"persona-theme": PersonaThemeStep,` entry from `REGISTRY` (line ~31). `STEP_KEYS` derives from `REGISTRY`, so it updates automatically.

- [ ] **Step 3: Update the OnboardingFlow parity test**

In `OnboardingFlow.test.tsx`:

(a) Delete the mock on line ~23: `vi.mock("./steps/PersonaThemeStep", () => ({ PersonaThemeStep: dbl("step-persona-theme") }));`

(b) In the "REGISTRY covers exactly the canonical backend steps" test (lines ~81-88), remove `"persona-theme"` from the expected set:

```typescript
it("REGISTRY covers exactly the canonical backend steps", () => {
  expect(new Set(STEP_KEYS)).toEqual(
    new Set([
      "welcome", "terms", "language", "wake-word",
      "api-keys", "mic-test", "system-style", "finish",
    ]),
  );
});
```

- [ ] **Step 4: Add the live preview to the onboarding WakeWordStep**

In `WakeWordStep.tsx`:

(a) Add the import near the top:

```typescript
import { deriveAssistantName } from "@/lib/deriveAssistantName";
```

(b) After the phrase input block (the closing `</div>` of the `flex items-center gap-2` row, line ~53), insert the derived-name hint. The onboarding input is the bare word with a fixed "Hey " prefix, so mirror the backend by deriving from the full stored phrase:

```tsx
      {(() => {
        const derived = deriveAssistantName(`Hey ${trimmed}`);
        return trimmed.length >= 2 && derived ? (
          <p className="text-xs text-muted-foreground">
            {t("onboarding.wake_word.derived_name").replace("{0}", derived)}
          </p>
        ) : null;
      })()}
```

- [ ] **Step 5: Add a preview test to WakeWordStep.test.tsx**

`WakeWordStep.test.tsx` mocks `useT` as `(k) => k`, so the rendered hint text is the raw key `onboarding.wake_word.derived_name` (the `.replace("{0}", …)` leaves it untouched — no `{0}` in the key). That lets us assert the conditional rendering without a real translation. Append this test (the exact `deriveAssistantName` output is already covered by `deriveAssistantName.test.ts`):

```typescript
it("shows the derived-name preview only after a valid word is typed", () => {
  render(<WakeWordStep onb={onb} goNext={vi.fn()} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  // No preview before typing.
  expect(screen.queryByText("onboarding.wake_word.derived_name")).toBeNull();
  // A valid word (>= 2 chars) surfaces the hint line.
  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  expect(screen.queryByText("onboarding.wake_word.derived_name")).not.toBeNull();
});
```

- [ ] **Step 6: Type-check and run the onboarding tests**

Run (from `jarvis/ui/web/frontend/`): `npx tsc -b`
Expected: no errors.

Run: `npm run test -- onboarding WakeWordStep`
Expected: ALL PASS (no reference to the deleted PersonaThemeStep; the new preview test passes).

- [ ] **Step 7: Commit (on the maintainer's go)**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/
git commit -m "feat(onboarding): remove persona-name step; preview derived name on the wake-word step"
```

---

## Task 9: Delete the now-dead `useAssistantName` save hook

**Files:**
- Delete: `jarvis/ui/web/frontend/src/hooks/useAssistantName.ts`

> `useAssistantNameSeed.ts` stays — it read-only-seeds the resolved name into the store for the bylines and still uses the GET endpoint.

- [ ] **Step 1: Confirm there are no remaining importers**

Run: `git grep -n "useAssistantName\b" -- jarvis/ui/web/frontend/src`
Expected: no hits (Task 7 removed the only importer). If any hit remains, fix it before deleting.

- [ ] **Step 2: Delete the file**

```bash
git rm jarvis/ui/web/frontend/src/hooks/useAssistantName.ts
```

- [ ] **Step 3: Type-check**

Run (from `jarvis/ui/web/frontend/`): `npx tsc -b`
Expected: no errors.

- [ ] **Step 4: Commit (on the maintainer's go)**

```bash
git add jarvis/ui/web/frontend/src/hooks/useAssistantName.ts
git commit -m "chore(ui): remove dead useAssistantName save hook"
```

---

## Task 10: i18n — remove obsolete keys, add the derived-name hint (en/de/es)

**Files:**
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/en.json`
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/de.json`
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/es.json`

> Per the Output Language Policy, the English value is the source string; de/es are the runtime translations.

- [ ] **Step 1: Remove the obsolete blocks in all three locales**

In each of `en.json`, `de.json`, `es.json`:

(a) Delete the entire `onboarding.persona` block (the object with `title`/`name_label`/`name_placeholder`/`skip` — in `en.json` around line 1329).

(b) Delete the entire `settings_view.assistant_name` block (the object with `title`/`description`/`label`/`current`/`auto_hint`/`save`/`saved`/`restart_required`). Find it with: `git grep -n "assistant_name" -- jarvis/ui/web/frontend/src/i18n`.

> Take care to remove a trailing comma left dangling by the deletion so the JSON stays valid.

- [ ] **Step 2: Add the derived-name hint key in the two wake-word blocks**

In each locale, add a `derived_name` key to BOTH:
- the `settings_view.wake_word` block (the one with `phrase_label`/`engine_label`), and
- the `onboarding.wake_word` block (the one with `prefix`/`ack_label`/`references_title`).

Values:

`en.json` — both blocks:
```json
"derived_name": "Your assistant will be called: {0}"
```

`de.json` — both blocks:
```json
"derived_name": "Dein Assistent heißt dann: {0}"
```

`es.json` — both blocks:
```json
"derived_name": "Tu asistente se llamará: {0}"
```

(Add a comma after the preceding key as needed to keep valid JSON.)

- [ ] **Step 3: Validate the JSON and that no obsolete keys remain**

Run:
```bash
node -e "for(const l of ['en','de','es']){JSON.parse(require('fs').readFileSync('jarvis/ui/web/frontend/src/i18n/locales/'+l+'.json','utf8'));console.log(l,'valid')}"
```
Expected: `en valid`, `de valid`, `es valid`.

Run: `git grep -n "assistant_name\|onboarding\.persona\|\"persona\"" -- jarvis/ui/web/frontend/src/i18n`
Expected: no hits.

- [ ] **Step 4: Commit (on the maintainer's go)**

```bash
git add jarvis/ui/web/frontend/src/i18n/locales/
git commit -m "i18n: drop assistant-name/persona keys, add wake-word derived-name hint (en/de/es)"
```

---

## Task 11: Full verification

**Files:** none (verification only)

- [ ] **Step 1: Backend — no leftovers and targeted tests green**

Run: `git grep -n "set_assistant_name\|AssistantNameBody\|persona\.name\b" -- jarvis`
Expected: only safe `getattr(..., "name", ...)` style hits, if any; no write-path or bare-read references.

Run:
```bash
pytest tests/unit/brain/test_assistant_name.py tests/unit/brain/test_system_prompt_name.py \
       tests/unit/core/test_config_writer_persona_strip.py tests/unit/setup/test_onboarding_meta.py -v
```
Expected: ALL PASS.

- [ ] **Step 2: Backend — boot import sanity**

Run: `python -c "import jarvis; import jarvis.ui.web.settings_routes; import jarvis.core.config; import jarvis.brain.assistant_name; print('imports ok')"`
Expected: prints `imports ok`.

- [ ] **Step 3: Lint**

Run: `ruff check jarvis/brain/assistant_name.py jarvis/core/config.py jarvis/core/config_writer.py jarvis/ui/web/settings_routes.py jarvis/setup/onboarding_meta.py`
Expected: no findings.

- [ ] **Step 4: Frontend — type-check, tests, build**

Run (from `jarvis/ui/web/frontend/`):
```bash
npx tsc -b
npm run test
npm run build
```
Expected: `tsc` clean; all vitest tests pass; build succeeds.

- [ ] **Step 5: Onboarding cross-layer parity (backend ↔ frontend)**

Run: `pytest tests/unit/setup/test_onboarding_meta.py -v` and (from the frontend) `npm run test -- OnboardingFlow`
Expected: both green — `ONBOARDING_STEPS` (8 steps, no `persona-theme`) matches the frontend `STEP_KEYS`.

- [ ] **Step 6: Manual smoke (maintainer, live app)**

After a `POST /api/settings/restart-app`:
1. Settings → the "Assistant Name" panel is gone; the Wake-Word panel shows "Your assistant will be called: X" live as the phrase is edited.
2. Set the wake word to "Hey Alex"; confirm the Sidebar header / chat byline read "Alex" after the change propagates.
3. Replay onboarding via `?onboarding=force`; confirm there is no separate name step and the wake-word step shows the derived-name preview.

---

## Done criteria

- The assistant name is resolved only from the wake phrase; a legacy `[persona].name` is ignored and stripped on the next wake-word save.
- No assistant-name write endpoint, Settings panel, onboarding step, config field, or writer remains; `GET /api/settings/assistant-name` is read-only and still feeds the bylines.
- The wake-word surfaces (Settings + onboarding) show the derived name live, localized in en/de/es.
- All targeted backend tests, the frontend test suite, `tsc`, `ruff`, and `npm run build` are green; onboarding step parity holds at 8 steps.
