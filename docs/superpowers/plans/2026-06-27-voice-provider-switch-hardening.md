# Voice Provider/Jarvis-Agent Switch Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every spoken provider / Jarvis-Agent / reply-language switch resolve to the RIGHT target and either execute-and-confirm honestly or fail-and-say-so — never a silent or false "done".

**Architecture:** The deterministic voice gate (`jarvis/brain/voice_command_gate.py`) recognises the command + target; the `BrainManager` handlers execute it. The root cause of "sometimes it isn't recognised / silently does the wrong thing" is that the Jarvis-Agent voice handler is a THIRD switch implementation that skips the credential validation the REST endpoint and `app_control._switch_subagent` both have. Task 1 routes it through the one validated function and renders an honest readback. Tasks 2–4 fix three recognition bugs in the gate.

**Tech Stack:** Python 3.11, `re`, pytest. Run tests with `C:\Program Files\Python311\python.exe -m pytest` (the real CPython, not the uv shim).

**Pre-flight:** `python -c "import jarvis; print(jarvis.__file__)"` must point inside this working tree (`...\Personal Jarvis\jarvis\__init__.py`). If not, run `pwsh scripts/preflight.ps1` first.

---

## File Structure

- Modify: `jarvis/brain/manager.py` — `_apply_subagent_provider_switch` (~line 2755) routes through the validated `apply_provider_switch` + honest readback; add a `_subagent_switch_failure_phrase` helper; add `"anthropic"` to `PROVIDER_ALIASES` (~line 135); the caller at ~line 5115 awaits the now-async handler.
- Modify: `jarvis/brain/voice_command_gate.py` — `_PROVIDER_PATTERN` (~line 42) accepts an optional "von/from <source>"; `_PROVIDER_ALIASES` (~line 24) gains `"chatgpt"`/`"anthropic"`; `_match_language_switch` (~line 111) picks the earliest-in-text language.
- Test: `tests/unit/brain/test_voice_command_gate.py` (recognition), `tests/unit/brain/test_subagent_voice_switch_validated.py` (new — execution/readback honesty).

Tasks are independent and committed separately. Do them in order (Task 1 is the critical structural fix).

---

## Task 1: Jarvis-Agent voice switch routes through the validated path + honest readback

**Why:** `_apply_subagent_provider_switch` maps the word then calls `config_writer.set_sub_jarvis_provider` BLINDLY — no credential check, and it returns "Erledigt" even when the persist throws. So "switch the subagent to OpenAI" (no key) says done, then the next mission fails; and a read-only-TOML persist failure is spoken as success. The validated `app_control.apply_provider_switch("subagent", ...)` already does the Codex-OAuth / Antigravity-OAuth / key-presence checks and returns a structured result — route through it.

**Files:**
- Create: `tests/unit/brain/test_subagent_voice_switch_validated.py`
- Modify: `jarvis/brain/manager.py` (`_apply_subagent_provider_switch` ~2755-2795; its caller ~5115)

- [ ] **Step 1: Write the failing test**

Create `tests/unit/brain/test_subagent_voice_switch_validated.py`:

```python
"""The deterministic subagent voice switch must validate + speak honestly.

Forensic 2026-06-27: the voice gate path persisted blindly (no credential check)
and said "Erledigt" even on failure — unlike the REST endpoint and
app_control._switch_subagent, which validate. This routes it through the one
validated apply_provider_switch and renders an honest readback.
"""
from __future__ import annotations

from types import SimpleNamespace
from uuid import UUID

import pytest

from jarvis.brain.manager import BrainManager
from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig


class _FakeExecutor:
    async def execute_confirmed(self, trace_id: UUID, **_):  # pragma: no cover
        return SimpleNamespace(success=True, output="ok", error=None)

    async def cancel_pending(self, trace_id: UUID):  # pragma: no cover
        return True


def _manager() -> BrainManager:
    cfg = JarvisConfig()
    cfg.brain.primary = "fake"
    return BrainManager(config=cfg, bus=EventBus(), tools={}, tool_executor=_FakeExecutor())


@pytest.mark.asyncio
async def test_voice_subagent_switch_success_speaks_real_target(monkeypatch) -> None:
    mgr = _manager()

    async def _ok(tier, provider, *, cfg, persist=True):
        assert tier == "subagent"
        assert provider == "openai-codex"  # "codex" mapped to canonical
        return {"ok": True, "new_provider": "openai-codex", "requires_restart": True}

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _ok)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_subagent_provider_switch("codex")
    assert "Codex" in out  # the DISPLAY name of the real target


@pytest.mark.asyncio
async def test_voice_subagent_switch_failure_is_honest(monkeypatch) -> None:
    mgr = _manager()

    async def _fail(tier, provider, *, cfg, persist=True):
        return {
            "ok": False, "error_kind": "missing_credential",
            "error": "Codex is not connected — run 'codex login' first.",
        }

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _fail)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_subagent_provider_switch("codex")
    assert out  # never silent
    low = out.lower()
    assert "erledigt" not in low and "done" not in low  # NOT a false success
    assert "codex" in low  # names what failed


@pytest.mark.asyncio
async def test_voice_subagent_switch_unknown_word_falls_through(monkeypatch) -> None:
    mgr = _manager()
    # an unmapped spoken word returns "" so the caller falls through to the brain
    assert await mgr._apply_subagent_provider_switch("flibberprovider") == ""
```

- [ ] **Step 2: Run the test to verify it fails**

Run: `python -m pytest tests/unit/brain/test_subagent_voice_switch_validated.py -v`
Expected: FAIL — `_apply_subagent_provider_switch` is currently sync (a coroutine `await` on it raises `TypeError`), and the success test fails because the current code persists blindly without calling `apply_provider_switch`.

- [ ] **Step 3: Add the honest-failure phrase helper**

In `jarvis/brain/manager.py`, immediately AFTER the `_SUBAGENT_SWITCH_CONFIRM` dict (it ends ~line 1256, the `"es": ...` line), add:

```python
# Honest spoken failure phrases for a deterministic subagent switch that the
# validated apply_provider_switch refused (missing credential / unknown). Never
# a false "done"; always names what failed. de/en/es (Runtime Output Language).
_SUBAGENT_SWITCH_FAIL: dict[str, dict[str, str]] = {
    "missing_credential": {
        "de": "{p} ist nicht verbunden — richte es zuerst ein, dann stelle ich um.", <!-- i18n-allow -->
        "en": "{p} isn't connected — set it up first, then I'll switch.",
        "es": "{p} no está conectado — configúralo primero y luego cambio.",
    },
    "other": {
        "de": "Das konnte ich nicht auf {p} umstellen.", <!-- i18n-allow -->
        "en": "I couldn't switch the sub-agent to {p}.",
        "es": "No pude cambiar el sub-agente a {p}.",
    },
}


def _subagent_switch_failure_phrase(result: dict, display: str, lang: str) -> str:
    kind = str(result.get("error_kind") or "other")
    table = _SUBAGENT_SWITCH_FAIL.get(kind, _SUBAGENT_SWITCH_FAIL["other"])
    return table.get(lang, table["de"]).format(p=display)
```

- [ ] **Step 4: Rewrite `_apply_subagent_provider_switch` to validate + render honestly**

Replace the whole method body (`jarvis/brain/manager.py` ~2755-2795) with:

```python
    async def _apply_subagent_provider_switch(self, word: str) -> str:
        """Execute a recognised sub-agent provider switch through the ONE
        validated path (app_control.apply_provider_switch) instead of persisting
        blindly. Maps the spoken word to a canonical slug, runs the same
        credential validation the REST endpoint uses, and renders an HONEST
        readback: the real target on success, a named reason on failure, never a
        false "done". Returns "" for an unknown word (caller falls through to the
        brain). Forensic 2026-06-27: the old blind-persist path said "Erledigt"
        for an unconnected provider and even when the persist threw.
        """
        canonical = _SUBAGENT_VOICE_TO_CANONICAL.get(word.strip().lower())
        if canonical is None:
            return ""
        from jarvis.brain.app_control import apply_provider_switch, resolve_running_cfg

        try:
            result = await apply_provider_switch(
                "subagent", canonical, cfg=resolve_running_cfg()
            )
        except Exception as exc:  # noqa: BLE001 — never crash the turn
            log.warning("sub-agent voice switch failed: %s", exc)
            result = {"ok": False, "error_kind": "other", "error": str(exc)}

        lang = self._resolve_turn_lang()
        if result.get("ok"):
            new = str(result.get("new_provider") or canonical)
            display = _SUBAGENT_DISPLAY.get(new, new)
            log.info("sub-agent provider switched to %r via deterministic voice gate", new)
            template = _SUBAGENT_SWITCH_CONFIRM.get(lang, _SUBAGENT_SWITCH_CONFIRM["de"])
            return template.format(p=display)
        display = _SUBAGENT_DISPLAY.get(canonical, canonical)
        return _subagent_switch_failure_phrase(result, display, lang)
```

- [ ] **Step 5: Await the now-async handler at the call site**

In `jarvis/brain/manager.py` ~line 5115, change:

```python
            confirmation = self._apply_subagent_provider_switch(subagent_switch)
```
to:
```python
            confirmation = await self._apply_subagent_provider_switch(subagent_switch)
```

- [ ] **Step 6: Run the test to verify it passes**

Run: `python -m pytest tests/unit/brain/test_subagent_voice_switch_validated.py -v`
Expected: PASS (all three).

- [ ] **Step 7: Run the related suites for regressions**

Run: `python -m pytest tests/unit/brain/test_voice_command_gate.py tests/unit/brain/test_voice_confirm_flow.py -q`
Expected: PASS.

- [ ] **Step 8: Lint + commit**

Run: `ruff check jarvis/brain/manager.py`
Expected: no NEW errors in the edited regions (the file has pre-existing E501s far from these lines — ignore those).

```bash
git add jarvis/brain/manager.py tests/unit/brain/test_subagent_voice_switch_validated.py
git commit -m "fix(voice): subagent voice switch validates via apply_provider_switch + honest readback"
```

---

## Task 2: Main-brain provider switch recognises "from X to Y"

**Why:** `_PROVIDER_PATTERN` has no "von/from" branch, so "wechsel von Gemini auf OpenAI" matches nothing and falls through to the brain LLM (which may refuse or force-spawn). The Jarvis-Agent matcher was already fixed for this; the main-brain regex wasn't. <!-- i18n-allow -->

**Files:**
- Modify: `jarvis/brain/voice_command_gate.py` (`_PROVIDER_PATTERN` ~42-49)
- Test: `tests/unit/brain/test_voice_command_gate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/brain/test_voice_command_gate.py`:

```python
def test_main_provider_switch_from_x_to_y_targets_y() -> None:
    # "von Gemini auf OpenAI" must target OpenAI (the destination), not fall through. <!-- i18n-allow -->
    m = match_voice_command("wechsel von gemini auf openai")  # i18n-allow: fixture
    assert m is not None and m.kind == "provider_switch"
    assert m.target == "openai"


def test_main_provider_switch_from_x_to_y_english() -> None:
    m = match_voice_command("switch from claude to gemini")
    assert m is not None and m.kind == "provider_switch"
    assert m.target == "gemini"


def test_main_provider_switch_plain_still_works() -> None:
    m = match_voice_command("wechsel auf gemini")
    assert m is not None and m.kind == "provider_switch"
    assert m.target == "gemini"
```

- [ ] **Step 2: Run to verify the new "from X to Y" tests fail**

Run: `python -m pytest tests/unit/brain/test_voice_command_gate.py -k "from_x_to_y or plain_still" -v`
Expected: FAIL — `test_main_provider_switch_from_x_to_y_targets_y` and `..._english` fail (no match); the plain test passes.

- [ ] **Step 3: Add the optional "von/from <source>" branch to `_PROVIDER_PATTERN`**

In `jarvis/brain/voice_command_gate.py`, replace the `_PROVIDER_PATTERN` definition (~42-49) with:

```python
_PROVIDER_PATTERN = re.compile(
    r"\b(?:wechsel[n]?|wechsle|switch(?:\s+to)?|benutze?|nutze|use|nimm)"
    r"(?:\s+(?:den|die|das|der|the|deinen|deine|dein|meinen|meine|mein|my))?"  # i18n-allow: German input vocabulary
    r"(?:\s+(?:brain[-\s]*provider|provider|anbieter|sprach[-\s]*modell|modell|model))?"
    # Optional "von/from <source>" so "switch FROM gemini TO openai" targets the
    # destination after auf/zu/to, not the source (forensic 2026-06-27).
    r"(?:\s+(?:von|from)\s+(?:" + "|".join(re.escape(p) for p in _PROVIDER_ALIASES) + r"))?"
    r"(?:\s+(?:auf|zu|to))?\s+"
    r"(?P<provider>" + "|".join(re.escape(p) for p in _PROVIDER_ALIASES) + r")\b",
    re.IGNORECASE,
)
```

- [ ] **Step 4: Run the provider-switch tests**

Run: `python -m pytest tests/unit/brain/test_voice_command_gate.py -k "provider_switch or explicit_provider" -v`
Expected: PASS — the from-X-to-Y tests + the plain test + the pre-existing provider tests.

- [ ] **Step 5: Lint + commit**

Run: `ruff check jarvis/brain/voice_command_gate.py`
Expected: no errors.

```bash
git add jarvis/brain/voice_command_gate.py tests/unit/brain/test_voice_command_gate.py
git commit -m "fix(voice): main-brain provider switch recognises 'from X to Y' (targets Y)"
```

---

## Task 3: Add "chatgpt" / "anthropic" spoken aliases to the main-brain switch

**Why:** The gate's `_PROVIDER_ALIASES` has no "chatgpt"/"anthropic", so "nutze chatgpt" / "switch to anthropic" don't match. The manager already maps `"chatgpt": "openai"` but is MISSING `"anthropic"`. Both layers must agree.

**Files:**
- Modify: `jarvis/brain/voice_command_gate.py` (`_PROVIDER_ALIASES` ~24-32)
- Modify: `jarvis/brain/manager.py` (`PROVIDER_ALIASES` ~135-147)
- Test: `tests/unit/brain/test_voice_command_gate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/brain/test_voice_command_gate.py`:

```python
def test_main_provider_aliases_chatgpt_and_anthropic() -> None:
    assert match_voice_command("nutze chatgpt").target == "chatgpt"
    assert match_voice_command("switch to anthropic").target == "anthropic"


def test_manager_maps_anthropic_to_claude_api() -> None:
    from jarvis.brain.manager import PROVIDER_ALIASES
    assert PROVIDER_ALIASES["anthropic"] == "claude-api"
    assert PROVIDER_ALIASES["chatgpt"] == "openai"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/brain/test_voice_command_gate.py -k "chatgpt_and_anthropic or maps_anthropic" -v`
Expected: FAIL — `match_voice_command("nutze chatgpt")` is None, and `PROVIDER_ALIASES["anthropic"]` raises `KeyError`.

- [ ] **Step 3: Add the gate aliases**

In `jarvis/brain/voice_command_gate.py`, replace the `_PROVIDER_ALIASES` tuple (~24-32) with (longer variants stay before their prefixes):

```python
_PROVIDER_ALIASES = (
    "claude-api",
    "openrouter",
    "anthropic",
    "chatgpt",
    "ollama",
    "gemini",
    "claude",
    "openai",
    "gpt",
)
```

- [ ] **Step 4: Add the manager mapping**

In `jarvis/brain/manager.py`, in the `PROVIDER_ALIASES` dict (~135-147), add `"anthropic": "claude-api",` next to the other claude aliases:

```python
PROVIDER_ALIASES = {
    "claude": "claude-api",
    "anthropic": "claude-api",
    "opus": "claude-api",
    "haiku": "claude-api",
    "sonnet": "claude-api",
    "gpt": "openai",
    "chatgpt": "openai",
    "openai": "openai",
    "gemini": "gemini",
    "flash": "gemini",
    "pro": "gemini",
    "openrouter": "openrouter",
}
```

- [ ] **Step 5: Run the tests**

Run: `python -m pytest tests/unit/brain/test_voice_command_gate.py -k "chatgpt_and_anthropic or maps_anthropic" -v`
Expected: PASS.

- [ ] **Step 6: Lint + commit**

Run: `ruff check jarvis/brain/voice_command_gate.py jarvis/brain/manager.py`
Expected: no new errors.

```bash
git add jarvis/brain/voice_command_gate.py jarvis/brain/manager.py tests/unit/brain/test_voice_command_gate.py
git commit -m "fix(voice): main-brain switch accepts 'chatgpt'/'anthropic' (manager maps anthropic->claude-api)"
```

---

## Task 4: Reply-language switch picks the earliest language in the TEXT

**Why:** `_match_language_switch` loops `_LANG_ALIASES` in DICT-INSERTION order and returns the first that appears anywhere, so "antworte auf Deutsch und Englisch" returns "en" (english is earlier in the dict) instead of "de". <!-- i18n-allow -->

**Files:**
- Modify: `jarvis/brain/voice_command_gate.py` (`_match_language_switch` ~111-125)
- Test: `tests/unit/brain/test_voice_command_gate.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/brain/test_voice_command_gate.py`:

```python
def test_language_switch_picks_earliest_in_text() -> None:
    # Two languages in one sentence → the one mentioned FIRST wins, not the first
    # in dict-insertion order (forensic 2026-06-27).
    m = match_voice_command("antworte auf deutsch und englisch")  # i18n-allow: fixture
    assert m is not None and m.kind == "language_switch"
    assert m.target == "de"


def test_language_switch_single_still_works() -> None:
    m = match_voice_command("antworte auf englisch")  # i18n-allow: fixture
    assert m is not None and m.kind == "language_switch"
    assert m.target == "en"
```

- [ ] **Step 2: Run to verify it fails**

Run: `python -m pytest tests/unit/brain/test_voice_command_gate.py -k "earliest_in_text or single_still" -v`
Expected: FAIL — `test_language_switch_picks_earliest_in_text` returns "en" (the single test passes).

- [ ] **Step 3: Pick the earliest-in-text language**

In `jarvis/brain/voice_command_gate.py`, replace the language-word scan at the top of `_match_language_switch` (~112-116) — the `code: str | None = None` / `for word, c in _LANG_ALIASES.items(): ... break` block — with:

```python
    # Pick the language word at the EARLIEST position in the text, not the first
    # in dict-insertion order — "auf Deutsch und Englisch" must resolve to "de" <!-- i18n-allow -->
    # (forensic 2026-06-27).
    best: tuple[int, str] | None = None  # (start_position, code)
    for word, c in _LANG_ALIASES.items():
        m = re.search(rf"\b{re.escape(word)}\b", t)
        if m is not None and (best is None or m.start() < best[0]):
            best = (m.start(), c)
    code = best[1] if best is not None else None
```

(The rest of the function — the `if code is None: return None` and the verb/prep gating — is unchanged.)

- [ ] **Step 4: Run the tests**

Run: `python -m pytest tests/unit/brain/test_voice_command_gate.py -k "language" -v`
Expected: PASS — the earliest-in-text test, the single test, and the pre-existing language tests.

- [ ] **Step 5: Lint + commit**

Run: `ruff check jarvis/brain/voice_command_gate.py`
Expected: no errors.

```bash
git add jarvis/brain/voice_command_gate.py tests/unit/brain/test_voice_command_gate.py
git commit -m "fix(voice): reply-language switch picks the earliest language in the sentence"
```

---

## Final Verification

- [ ] **Step 1: Run all touched suites together**

Run: `python -m pytest tests/unit/brain/test_voice_command_gate.py tests/unit/brain/test_subagent_voice_switch_validated.py tests/unit/brain/test_voice_confirm_flow.py tests/missions/worker_runtime/test_provider_map.py -q`
Expected: PASS.

- [ ] **Step 2: Lint both changed modules**

Run: `ruff check jarvis/brain/voice_command_gate.py`
Expected: no errors. (`manager.py` has pre-existing E501s unrelated to these edits — confirm your hunks aren't among the flagged lines.)

- [ ] **Step 3: Manual live check (after restart)**

Restart via `POST /api/settings/restart-app`. By voice, try: "wechsel von Gemini auf OpenAI" (→ switches to OpenAI), "stell den Subagent auf einen nicht-verbundenen Anbieter" (→ honest "not connected", NOT "Erledigt"), "nutze ChatGPT", "antworte auf Deutsch und Englisch" (→ German). <!-- i18n-allow -->

---

## Notes

- **Do NOT touch** `_SUBAGENT_VOICE_TO_CANONICAL` (manager.py ~1240) or `_match_subagent_switch` (voice_command_gate.py) — both verified correct/already-fixed on 2026-06-27.
- **Shared tree:** `manager.py` and `voice_command_gate.py` are edited by parallel sessions. Before each commit confirm `git diff <file>` shows ONLY your hunks; if a foreign hunk is present, hold it back (set it to HEAD, commit, restore) as done on 2026-06-27.
- **Language:** all new strings (phrases, comments, commit messages) are English per the repo language-policy gate; the spoken phrase tables carry de/en/es product text, which is allowed.
- **Restart:** none of these are live until `POST /api/settings/restart-app`.
