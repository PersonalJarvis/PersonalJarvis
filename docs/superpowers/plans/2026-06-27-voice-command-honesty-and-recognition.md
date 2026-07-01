# Voice-Command Honesty & Recognition Hardening Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every deterministic spoken meta-command answer truthfully — never silent, never a blind "done" — and close the recognition gaps that let plausible phrasings fall through, with a regression net that prevents the whole failure class from returning.

**Architecture:** The `voice_command_gate` recognises 6 deterministic command kinds (provider_switch, subagent_switch, language_switch, cancel, depth_deep, depth_fast); `BrainManager.generate()` intercepts each BEFORE the LLM and executes it. An audit (2026-06-27) found two failure classes: (a) HONESTY — provider_switch/cancel/depth return `""` (silent, even on failure) and language_switch speaks success even when persist fails; (b) RECOGNITION — natural phrasings ("ändere den Anbieter", "halt") are not matched. We route the provider switch through the already-validated `app_control.apply_provider_switch("brain", …)` (exact mirror of the subagent fix already shipped), give cancel/depth honest readbacks, make language persist honest, fill the recognition gaps, and add a data-driven recognition checklist + an anti-drift completeness guard so a new command kind cannot be added without an honesty test.

**Tech Stack:** Python 3.11, pytest (`asyncio_mode=auto`), the existing `jarvis/brain/voice_command_gate.py` + `jarvis/brain/manager.py` + `jarvis/brain/app_control.py`. Tests run with `C:\Program Files\Python311\python.exe -m pytest`.

## Global Constraints

- **Artifacts are English** (code, comments, docstrings, commit messages). Spoken phrase TABLES carry de/en/es and are product surface, not artifacts — mark any inline German fixture with `# i18n-allow`.
- **Runtime Output Language doctrine:** every spoken phrase table MUST carry all three supported languages (de/en/es) and resolve its key through `self._resolve_turn_lang()` — never a binary `de`/`en` shortcut (CLAUDE.md "Runtime Output Language" §3).
- **Honesty contract (the whole point):** a deterministic spoken command's readback comes from the ACTUALLY-CHECKED result of the action — never a blind success, never silence on failure.
- **No new hard dependency.** Base `python:3.11-slim` install must still import.
- **Shared working tree:** commit each task hunk-isolated; hold back foreign hunks (the tree carries parallel-session work). Use `git commit -F <msgfile> -- <paths>` (PowerShell 5.1 mangles inline `-m` with quotes).
- **Provider-agnostic:** never branch on a provider name to enable behavior; the brand-name→canonical mapping lives in `PROVIDER_ALIASES` (manager) and the validation lives in `apply_provider_switch`.
- Run the real interpreter: `C:\Program Files\Python311\python.exe -m pytest`.

---

## File Structure

| File | Responsibility | Change |
|---|---|---|
| `jarvis/brain/manager.py` | Spoken phrase tables + the 6 intercept handlers in `generate()` | Modify: add provider/cancel/depth/session-persist phrase tables + `_apply_main_provider_switch` + `_provider_switch_failure_phrase`; rewrite the cancel/switch/depth intercepts to speak; make language persist honest |
| `jarvis/brain/voice_command_gate.py` | Deterministic recognition regexes | Modify: add `ändere/setze/stell` provider verbs; add `halt` cancel verb |
| `tests/unit/brain/test_voice_command_honesty.py` | NEW — the honesty regression net (success speaks real result; failure is honest; never silent) per command kind | Create |
| `tests/unit/brain/voice_command_cases.py` | NEW — the growing recognition checklist: real utterances → expected (kind, target) | Create |
| `tests/unit/brain/test_voice_command_checklist.py` | NEW — runs every checklist case + the anti-drift completeness guard | Create |
| `tests/unit/brain/test_voice_command_gate.py` | Existing recognition unit tests | Modify: add the new-verb recognition tests |

**Out of scope (documented, not fixed):** `jarvis/plugins/tool/open_app.py` speaks "Gestartet: X" from a fire-and-forget `subprocess.Popen` without a launch proof. The common failure (app not found) IS already honest (`FileNotFoundError` → error result, open_app.py:333); verifying a GUI window actually appeared would add latency without a reliable signal. Tracked as a known low-risk limitation, not addressed here (YAGNI). `_apply_reply_language_switch`'s persist honesty IS in scope (Task 4) because it is a one-line truthfulness fix.

---

## Task 1: Main-brain voice switch speaks an honest result

The HIGH-risk finding. `generate()` runs `await self.switch(target); return ""` (manager.py:5103-5106) — completely silent, even when `switch()` swallows an unknown / Jarvis-Agent-only / unloadable provider (manager.py:2555-2576). The Jarvis-Agent path already routes through the validated `apply_provider_switch` with an honest readback; this makes the main brain do the same.

**Files:**
- Modify: `jarvis/brain/manager.py` (phrase tables near line 1279; new method near `_apply_subagent_provider_switch` ~2777; intercept at 5103-5106)
- Test: `tests/unit/brain/test_voice_command_honesty.py` (create)

**Interfaces:**
- Consumes: `apply_provider_switch(tier, provider, *, cfg, persist=True) -> dict` and `resolve_running_cfg()` from `jarvis.brain.app_control`; `PROVIDER_ALIASES` (manager.py:135); `self._resolve_turn_lang() -> str` (de/en/es).
- Produces: `BrainManager._apply_main_provider_switch(self, word: str) -> str` (async) — returns a non-empty spoken readback for a recognised provider word (honest on success AND failure), or `""` for an unmappable word so the caller falls through to the brain.

- [ ] **Step 1: Write the failing honesty tests**

Create `tests/unit/brain/test_voice_command_honesty.py`:

```python
"""Every deterministic spoken command must answer from the CHECKED result —
never silent, never a blind "done". Audit 2026-06-27 found provider_switch,
cancel and depth returned "" (silent) and language_switch spoke success on a
persist failure. These tests are the regression net for the whole class.
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
async def test_main_switch_success_speaks_real_target(monkeypatch) -> None:
    mgr = _manager()

    async def _ok(tier, provider, *, cfg, persist=True):
        assert tier == "brain"
        assert provider == "gemini"
        return {"ok": True, "new_provider": "gemini", "applied_live": True}

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _ok)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_main_provider_switch("gemini")
    assert out
    assert "Gemini" in out


@pytest.mark.asyncio
async def test_main_switch_failure_is_honest(monkeypatch) -> None:
    mgr = _manager()

    async def _fail(tier, provider, *, cfg, persist=True):
        return {
            "ok": False, "error_kind": "missing_credential",
            "error": "Gemini is not configured — its API key is missing.",
        }

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _fail)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_main_provider_switch("gemini")
    assert out
    low = out.lower()
    assert "erledigt" not in low and "done" not in low  # NOT a false success
    assert "gemini" in low  # names what failed


@pytest.mark.asyncio
async def test_main_switch_subagent_only_is_honest(monkeypatch) -> None:
    mgr = _manager()

    async def _fail(tier, provider, *, cfg, persist=True):
        return {"ok": False, "error_kind": "subagent_only", "error": "subagent only"}

    monkeypatch.setattr("jarvis.brain.app_control.apply_provider_switch", _fail)
    monkeypatch.setattr("jarvis.brain.app_control.resolve_running_cfg", lambda: mgr._config)

    out = await mgr._apply_main_provider_switch("antigravity")
    assert out
    assert "erledigt" not in out.lower() and "done" not in out.lower()


@pytest.mark.asyncio
async def test_main_switch_unknown_word_falls_through() -> None:
    mgr = _manager()
    assert await mgr._apply_main_provider_switch("flibberprovider") == ""
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_honesty.py -q`
Expected: FAIL — `AttributeError: 'BrainManager' object has no attribute '_apply_main_provider_switch'`.

- [ ] **Step 3: Add the provider phrase tables**

In `jarvis/brain/manager.py`, after the `_subagent_switch_failure_phrase` function (ends ~line 1279), add:

```python
# Main-brain provider switch — the voice_command_gate "provider_switch" path.
# Mirrors the subagent tables: routed through the validated apply_provider_switch
# so the spoken readback is HONEST (audit 2026-06-27: the old path returned ""
# silently, even when the switch was refused). de/en/es (Runtime Output Language).
_PROVIDER_SWITCH_CONFIRM: dict[str, str] = {
    "de": "Erledigt — dein Haupt-Brain läuft jetzt auf {p}.", <!-- i18n-allow -->
    "en": "Done — your main brain now runs on {p}.",
    "es": "Listo — tu cerebro principal ahora usa {p}.",
}
_PROVIDER_SWITCH_FAIL: dict[str, dict[str, str]] = {
    "missing_credential": {
        "de": "{p} ist nicht eingerichtet — hinterlege zuerst den Schlüssel, dann stelle ich um.", <!-- i18n-allow -->
        "en": "{p} isn't set up — add its key first, then I'll switch.",
        "es": "{p} no está configurado — añade su clave primero y luego cambio.",
    },
    "subagent_only": {
        "de": "{p} geht nur als Sub-Agent, nicht als Haupt-Brain.", <!-- i18n-allow -->
        "en": "{p} only works as a sub-agent, not as the main brain.",
        "es": "{p} solo funciona como sub-agente, no como cerebro principal.",
    },
    "other": {
        "de": "Den Haupt-Brain konnte ich nicht auf {p} umstellen.", <!-- i18n-allow -->
        "en": "I couldn't switch the main brain to {p}.",
        "es": "No pude cambiar el cerebro principal a {p}.",
    },
}


def _provider_switch_failure_phrase(result: dict, display: str, lang: str) -> str:
    kind = str(result.get("error_kind") or "other")
    table = _PROVIDER_SWITCH_FAIL.get(kind, _PROVIDER_SWITCH_FAIL["other"])
    return table.get(lang, table["de"]).format(p=display)
```

- [ ] **Step 4: Add the `_apply_main_provider_switch` method**

In `jarvis/brain/manager.py`, immediately AFTER the `_apply_subagent_provider_switch` method (ends ~line 2810), add:

```python
    async def _apply_main_provider_switch(self, word: str) -> str:
        """Deterministic main-brain provider switch with an HONEST readback.

        Mirrors ``_apply_subagent_provider_switch``: routes through the one
        validated ``apply_provider_switch("brain", …)`` (credential / catalog /
        subagent-only checks + live-apply) and speaks the CHECKED result. The
        old path (``await self.switch(word); return ""``) was silent even on a
        refused switch (audit 2026-06-27). Returns ``""`` for an unmappable word
        so the caller falls through to the brain.
        """
        canonical = PROVIDER_ALIASES.get(word.strip().lower())
        if canonical is None:
            return ""
        from jarvis.brain.app_control import (
            apply_provider_switch,
            get_spec,
            resolve_running_cfg,
        )
        try:
            result = await apply_provider_switch("brain", canonical, cfg=resolve_running_cfg())
        except Exception as exc:  # noqa: BLE001
            log.warning("main-brain voice switch failed: %s", exc)
            result = {"ok": False, "error_kind": "other", "error": str(exc)}
        lang = self._resolve_turn_lang()
        spec = get_spec(str(result.get("new_provider") or canonical))
        display = getattr(spec, "label", None) or canonical
        if result.get("ok"):
            log.info("main-brain provider switched to %r via deterministic voice gate", canonical)
            template = _PROVIDER_SWITCH_CONFIRM.get(lang, _PROVIDER_SWITCH_CONFIRM["de"])
            return template.format(p=display)
        return _provider_switch_failure_phrase(result, display, lang)
```

- [ ] **Step 5: Rewrite the intercept to speak**

In `jarvis/brain/manager.py`, replace the provider-switch intercept (currently 5103-5106):

```python
        switch_target = self._detect_switch_intent(user_text)
        if switch_target:
            await self.switch(switch_target)
            return ""
```

with:

```python
        switch_target = self._detect_switch_intent(user_text)
        if switch_target:
            confirmation = await self._apply_main_provider_switch(switch_target)
            if confirmation:
                await self._record_response_side_effects(
                    user_text=user_text,
                    response_text=confirmation,
                    use_history=use_history,
                    trace_id=turn_trace_id,
                )
                return confirmation
```

- [ ] **Step 6: Run the tests to verify they pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_honesty.py -q`
Expected: PASS (4 passed).

- [ ] **Step 7: Commit (hunk-isolated)**

Verify only your hunks are staged, then commit:
```bash
git --no-pager diff jarvis/brain/manager.py | grep '^@@'   # confirm only YOUR hunks
git commit -F <msgfile> -- jarvis/brain/manager.py tests/unit/brain/test_voice_command_honesty.py
```
Message subject: `fix(voice): main-brain voice switch speaks an honest result (no more silent switch)`

If foreign hunks are interleaved in manager.py, use the stash-isolation dance: `git stash push -m bak -- jarvis/brain/manager.py`, re-apply ONLY your edits, verify the diff, commit, then `git checkout "stash@{0}" -- jarvis/brain/manager.py` and `git stash drop "stash@{0}"`.

---

## Task 2: Cancel speaks how many tasks it stopped

`generate()` runs `self._cancel_all_background_tasks(); return ""` (manager.py:5099-5101) — silent. The method already returns the count (manager.py:4807-4826, `-> int`); we just speak it.

**Files:**
- Modify: `jarvis/brain/manager.py` (phrase tables near 1279; intercept 5099-5101)
- Test: `tests/unit/brain/test_voice_command_honesty.py` (extend)

**Interfaces:**
- Consumes: `self._cancel_all_background_tasks() -> int`; `self._resolve_turn_lang()`.
- Produces: a non-empty spoken readback on the cancel intercept (count > 0 names the count; count == 0 says nothing was running).

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/brain/test_voice_command_honesty.py`:

```python
@pytest.mark.asyncio
async def test_cancel_readback_names_count(monkeypatch) -> None:
    mgr = _manager()
    monkeypatch.setattr(mgr, "_cancel_all_background_tasks", lambda: 2)
    phrase = mgr._cancel_readback(2)
    assert phrase and "2" in phrase


@pytest.mark.asyncio
async def test_cancel_readback_honest_when_nothing_running() -> None:
    mgr = _manager()
    phrase = mgr._cancel_readback(0)
    assert phrase  # never silent
    # honest: does not claim it stopped something
    assert "2" not in phrase
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_honesty.py -k cancel_readback -q`
Expected: FAIL — `AttributeError: ... has no attribute '_cancel_readback'`.

- [ ] **Step 3: Add the cancel phrase tables**

In `jarvis/brain/manager.py`, after the `_PROVIDER_SWITCH_FAIL` block / `_provider_switch_failure_phrase` (from Task 1), add:

```python
# Background-task cancel — the voice_command_gate "cancel" path. Honest readback:
# names the count when something was stopped, says so plainly when nothing ran
# (audit 2026-06-27: the old path was silent either way). de/en/es.
_CANCEL_CONFIRM: dict[str, str] = {
    "de": "Erledigt — {n} laufende Aufgabe(n) gestoppt.",
    "en": "Done — stopped {n} running task(s).",
    "es": "Listo — detuve {n} tarea(s) en curso.",
}
_CANCEL_NONE: dict[str, str] = {
    "de": "Es lief gerade nichts, das ich stoppen könnte.", <!-- i18n-allow -->
    "en": "Nothing was running to stop.",
    "es": "No había nada en curso que detener.",
}
```

- [ ] **Step 4: Add the `_cancel_readback` method**

In `jarvis/brain/manager.py`, immediately AFTER `_cancel_all_background_tasks` (ends ~line 4826), add:

```python
    def _cancel_readback(self, count: int) -> str:
        """Honest spoken readback for a deterministic cancel: name the count, or
        say plainly that nothing was running. Never silent (audit 2026-06-27)."""
        lang = self._resolve_turn_lang()
        if count > 0:
            return _CANCEL_CONFIRM.get(lang, _CANCEL_CONFIRM["de"]).format(n=count)
        return _CANCEL_NONE.get(lang, _CANCEL_NONE["de"])
```

- [ ] **Step 5: Rewrite the intercept to speak**

Replace the cancel intercept (currently 5099-5101):

```python
        if self._detect_cancel_intent(user_text):
            self._cancel_all_background_tasks()
            return ""
```

with:

```python
        if self._detect_cancel_intent(user_text):
            confirmation = self._cancel_readback(self._cancel_all_background_tasks())
            await self._record_response_side_effects(
                user_text=user_text,
                response_text=confirmation,
                use_history=use_history,
                trace_id=turn_trace_id,
            )
            return confirmation
```

- [ ] **Step 6: Run to verify pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_honesty.py -q`
Expected: PASS (6 passed).

- [ ] **Step 7: Commit (hunk-isolated)**

Message subject: `fix(voice): cancel command speaks how many tasks it stopped (no more silent cancel)`
Paths: `jarvis/brain/manager.py tests/unit/brain/test_voice_command_honesty.py`.

---

## Task 3: Depth override confirms the new thinking depth

`generate()` runs `self._force_level = "deep"/"fast"; return ""` (manager.py:5140-5146) — silent. Speak a confirmation.

**Files:**
- Modify: `jarvis/brain/manager.py` (phrase table near 1279; intercept 5140-5146)
- Test: `tests/unit/brain/test_voice_command_honesty.py` (extend)

**Interfaces:**
- Consumes: `self._resolve_turn_lang()`.
- Produces: `BrainManager._depth_readback(self, level: str) -> str` — non-empty for "deep"/"fast".

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/brain/test_voice_command_honesty.py`:

```python
@pytest.mark.parametrize("level", ["deep", "fast"])
def test_depth_readback_confirms(level: str) -> None:
    mgr = _manager()
    phrase = mgr._depth_readback(level)
    assert phrase  # never silent
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_honesty.py -k depth_readback -q`
Expected: FAIL — `AttributeError: ... '_depth_readback'`.

- [ ] **Step 3: Add the depth phrase table**

In `jarvis/brain/manager.py`, after the `_CANCEL_NONE` block (Task 2), add:

```python
# Thinking-depth override — the voice_command_gate "depth_deep"/"depth_fast"
# path. Confirms the new depth instead of staying silent (audit 2026-06-27).
_DEPTH_CONFIRM: dict[str, dict[str, str]] = {
    "deep": {
        "de": "Alles klar — ich denke ab jetzt gründlicher.", <!-- i18n-allow -->
        "en": "Got it — I'll think more deeply from now on.",
        "es": "Entendido — pensaré más a fondo a partir de ahora.",
    },
    "fast": {
        "de": "Alles klar — ich denke ab jetzt schneller.",
        "en": "Got it — I'll think faster from now on.",
        "es": "Entendido — pensaré más rápido a partir de ahora.",
    },
}
```

- [ ] **Step 4: Add the `_depth_readback` method**

In `jarvis/brain/manager.py`, immediately AFTER `_cancel_readback` (Task 2), add:

```python
    def _depth_readback(self, level: str) -> str:
        """Honest spoken confirmation of a depth override. Never silent."""
        lang = self._resolve_turn_lang()
        table = _DEPTH_CONFIRM.get(level, _DEPTH_CONFIRM["deep"])
        return table.get(lang, table["de"])
```

- [ ] **Step 5: Rewrite the intercept to speak**

Replace the depth intercept (currently 5140-5146):

```python
        depth_override = self._detect_depth_override(user_text)
        if depth_override == "deep":
            self._force_level = "deep"
            return ""
        if depth_override == "fast":
            self._force_level = "fast"
            return ""
```

with:

```python
        depth_override = self._detect_depth_override(user_text)
        if depth_override in ("deep", "fast"):
            self._force_level = depth_override
            confirmation = self._depth_readback(depth_override)
            await self._record_response_side_effects(
                user_text=user_text,
                response_text=confirmation,
                use_history=use_history,
                trace_id=turn_trace_id,
            )
            return confirmation
```

- [ ] **Step 6: Run to verify pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_honesty.py -q`
Expected: PASS (8 passed).

- [ ] **Step 7: Commit (hunk-isolated)**

Message subject: `fix(voice): depth override confirms the new thinking depth (no more silent toggle)`
Paths: `jarvis/brain/manager.py tests/unit/brain/test_voice_command_honesty.py`.

---

## Task 4: Reply-language switch is honest about persistence

`_apply_reply_language_switch` (manager.py:2710-2742) speaks the permanent "from now on" confirmation even when the `config_writer.set_reply_language` persist fails (caught + logged at 2737-2740). The live switch DID apply, but it reverts after restart — so the readback overpromises. Track the persist result and say "for this session" honestly when persist failed.

**Files:**
- Modify: `jarvis/brain/manager.py` (phrase table near 1232; method 2710-2742)
- Test: `tests/unit/brain/test_voice_command_honesty.py` (extend)

**Interfaces:**
- Consumes: existing `_LANG_SWITCH_CONFIRM`; new `_LANG_SWITCH_CONFIRM_SESSION`.
- Produces: `_apply_reply_language_switch` speaks the session-scoped phrase when persist failed, the permanent phrase when it succeeded.

- [ ] **Step 1: Write the failing test**

Append to `tests/unit/brain/test_voice_command_honesty.py`:

```python
def test_reply_language_persist_failure_is_honest(monkeypatch) -> None:
    mgr = _manager()
    # live switch succeeds, persist raises -> readback must NOT promise "from now on"
    monkeypatch.setattr(mgr, "set_reply_language", lambda code: None)

    def _boom(_lang):
        raise OSError("jarvis.toml is read-only")

    import jarvis.core.config_writer as cw
    monkeypatch.setattr(cw, "set_reply_language", _boom)

    out = mgr._apply_reply_language_switch("en")
    assert out  # never silent
    low = out.lower()
    assert "session" in low or "sitzung" in low or "sesión" in low  # scoped, honest
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_honesty.py -k reply_language_persist -q`
Expected: FAIL — the current code returns the permanent `_LANG_SWITCH_CONFIRM` phrase (no "session"/"Sitzung").

- [ ] **Step 3: Add the session-scoped phrase table**

In `jarvis/brain/manager.py`, immediately AFTER `_LANG_SWITCH_CONFIRM` (ends ~line 1232), add:

```python
# Spoken when the live reply-language switch applied but PERSIST failed (read-only
# / locked jarvis.toml). Honest: scoped to this session, not "from now on", because
# it reverts on restart (audit 2026-06-27). de/en/es.
_LANG_SWITCH_CONFIRM_SESSION: dict[str, str] = {
    "de": "Für diese Sitzung antworte ich auf Deutsch — dauerhaft speichern hat nicht geklappt.", <!-- i18n-allow -->
    "en": "For this session I'll reply in English — saving it permanently didn't work.",
    "es": "Por esta sesión responderé en español — no pude guardarlo de forma permanente.",
}
```

Note: the language WORD inside the phrase is fixed per table entry and read for the resolved code below; keep all three entries phrased in their own language (so the new TTS voice is audible, mirroring `_LANG_SWITCH_CONFIRM`).

- [ ] **Step 4: Make the persist result drive the readback**

In `jarvis/brain/manager.py`, replace the persist block + return in `_apply_reply_language_switch` (currently 2733-2742):

```python
        try:
            from jarvis.core import config_writer

            config_writer.set_reply_language(lang)
        except Exception as exc:  # noqa: BLE001
            log.warning(
                "reply-language persist failed (live switch still applied): %s", exc
            )
        log.info("reply-language switched to %r via deterministic voice gate", lang)
        return _LANG_SWITCH_CONFIRM.get(lang, _LANG_SWITCH_CONFIRM["de"])
```

with:

```python
        persisted = True
        try:
            from jarvis.core import config_writer

            config_writer.set_reply_language(lang)
        except Exception as exc:  # noqa: BLE001
            persisted = False
            log.warning(
                "reply-language persist failed (live switch still applied): %s", exc
            )
        log.info(
            "reply-language switched to %r via deterministic voice gate (persisted=%s)",
            lang, persisted,
        )
        if persisted:
            return _LANG_SWITCH_CONFIRM.get(lang, _LANG_SWITCH_CONFIRM["de"])
        return _LANG_SWITCH_CONFIRM_SESSION.get(lang, _LANG_SWITCH_CONFIRM_SESSION["de"])
```

- [ ] **Step 5: Run to verify pass**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_honesty.py -q`
Expected: PASS (9 passed).

- [ ] **Step 6: Commit (hunk-isolated)**

Message subject: `fix(voice): reply-language switch is honest when persistence fails (session-scoped readback)`
Paths: `jarvis/brain/manager.py tests/unit/brain/test_voice_command_honesty.py`.

---

## Task 5: Close the recognition gaps (provider verbs + cancel "halt")

The audit found natural phrasings that don't match: "ändere/setze/stell den Anbieter auf X" (provider_switch) and "halt" (cancel). Add them — staying strict (provider still requires a trailing provider alias; cancel still requires sentence-start or "jarvis"). <!-- i18n-allow -->

**Files:**
- Modify: `jarvis/brain/voice_command_gate.py` (`_PROVIDER_PATTERN` ~42; `_CANCEL_PATTERN` ~57)
- Test: `tests/unit/brain/test_voice_command_gate.py` (extend)

**Interfaces:**
- Consumes: nothing new.
- Produces: `_PROVIDER_PATTERN` also matches the `ändere/änder/setze/setz/stell` verbs; `_CANCEL_PATTERN` also matches `halt`. <!-- i18n-allow -->

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/brain/test_voice_command_gate.py`:

```python
@pytest.mark.parametrize(
    "text,target",
    [
        ("ändere den Provider auf gemini", "gemini"),       # i18n-allow: German voice fixture
        ("setze den Brain-Provider auf openai", "openai"),  # i18n-allow: German voice fixture
        ("stell den Provider auf claude", "claude"),        # i18n-allow: German voice fixture
    ],
)
def test_provider_switch_extra_verbs(text: str, target: str) -> None:
    m = match_voice_command(text)
    assert m is not None and m.kind == "provider_switch", f"no match for {text!r}"
    assert m.target == target


def test_cancel_recognizes_halt() -> None:
    m = match_voice_command("halt")
    assert m is not None and m.kind == "cancel"


def test_halt_midsentence_is_not_cancel() -> None:
    # "das ist halt so" must NOT cancel — halt only at sentence start / after jarvis <!-- i18n-allow -->
    m = match_voice_command("das ist halt so") <!-- i18n-allow -->
    assert m is None or m.kind != "cancel"
```

- [ ] **Step 2: Run to verify failure**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_gate.py -k "extra_verbs or halt" -q`
Expected: FAIL — extra verbs return None; "halt" not matched.

- [ ] **Step 3: Add the provider verbs**

In `jarvis/brain/voice_command_gate.py`, change the first line of `_PROVIDER_PATTERN` (line 43) from:

```python
    r"\b(?:wechsel[n]?|wechsle|switch(?:\s+to)?|benutze?|nutze|use|nimm)"
```

to:

```python
    r"\b(?:wechsel[n]?|wechsle|änder\w*|aender\w*|setz\w*|stell\w*"
    r"|switch(?:\s+to)?|benutze?|nutze|use|nimm)"
```

(The pattern still requires a trailing known provider alias with a word boundary, so "stell die Heizung an" — no provider word — does not match.) <!-- i18n-allow -->

- [ ] **Step 4: Add the cancel "halt" verb**

In `jarvis/brain/voice_command_gate.py`, change `_CANCEL_PATTERN` (lines 57-60) from:

```python
_CANCEL_PATTERN = re.compile(
    r"^(?:jarvis[,\s]+)?(?:stopp?|abbruch|abbrechen|cancel|stop\s+sub)\b",
    re.IGNORECASE,
)
```

to:

```python
_CANCEL_PATTERN = re.compile(
    r"^(?:jarvis[,\s]+)?(?:stopp?|abbruch|abbrechen|cancel|stop\s+sub|halt)\b",
    re.IGNORECASE,
)
```

(`halt` matches only at sentence start or after "jarvis" — the existing `^` anchor — so "das ist halt so" still falls through. `\b` keeps it from matching "halten"/"Haltung".) <!-- i18n-allow -->

- [ ] **Step 5: Run to verify pass + no regressions**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_gate.py -q`
Expected: PASS (all — new + existing, ~38 passed).

- [ ] **Step 6: Commit (hunk-isolated)**

The `_LANG_OUTPUT_VERB` block in this file may carry a parallel-session hunk — keep it neutralised at HEAD for the commit, then restore it (the established dance for this file).
Message subject: `fix(voice): recognize "ändere/setze/stell <provider>" and "halt" to cancel` <!-- i18n-allow -->
Paths: `jarvis/brain/voice_command_gate.py tests/unit/brain/test_voice_command_gate.py`.

---

## Task 6: Recognition checklist + anti-drift completeness guard

The structural insurance the user asked for. A growing data file of real utterances → expected (kind, target) that runs automatically, plus a guard that fails if a new `VoiceCommandMatch.kind` is added without an honesty test — so this whole class cannot silently return.

**Files:**
- Create: `tests/unit/brain/voice_command_cases.py` (the growing checklist data)
- Create: `tests/unit/brain/test_voice_command_checklist.py` (runs the checklist + the completeness guard)

**Interfaces:**
- Consumes: `match_voice_command` from `jarvis.brain.voice_command_gate`; `VoiceCommandMatch` (its `kind` Literal); the honesty test module.
- Produces: `RECOGNITION_CASES: list[tuple[str, str, str]]` (utterance, expected kind, expected target — `""` when the kind carries no target).

- [ ] **Step 1: Create the checklist data file**

Create `tests/unit/brain/voice_command_cases.py`:

```python
"""The growing voice-command recognition checklist — real utterances the gate
MUST classify correctly. Add a case here whenever a phrasing is mis-recognised
in the field; the checklist test then guards it forever. (utterance, kind, target);
target is "" for kinds with no target (cancel, depth_deep, depth_fast).
"""
from __future__ import annotations

# (utterance, expected kind, expected target)
RECOGNITION_CASES: list[tuple[str, str, str]] = [
    # provider_switch
    ("wechsel auf gemini", "provider_switch", "gemini"),
    ("switch to openai", "provider_switch", "openai"),
    ("wechsel von gemini auf openai", "provider_switch", "openai"),  # i18n-allow: fixture
    ("switch from claude to gemini", "provider_switch", "gemini"),
    ("nutze chatgpt", "provider_switch", "chatgpt"),
    ("switch to anthropic", "provider_switch", "anthropic"),
    ("ändere den Provider auf gemini", "provider_switch", "gemini"),  # i18n-allow: fixture
    ("stell den Provider auf claude", "provider_switch", "claude"),   # i18n-allow: fixture
    # subagent_switch
    ("stell den subagent provider auf gemini", "subagent_switch", "gemini"),  # i18n-allow: fixture
    ("stell den subagent provider von antigravity auf codex um", "subagent_switch", "codex"),  # i18n-allow: fixture
    # language_switch
    ("stell auf Englisch um", "language_switch", "en"),               # i18n-allow: fixture
    ("antworte auf deutsch und englisch", "language_switch", "de"),   # i18n-allow: fixture
    ("respond in German", "language_switch", "de"),
    # cancel
    ("jarvis stopp", "cancel", ""),
    ("halt", "cancel", ""),
    # depth
    ("denk gründlich", "depth_deep", ""),                             # i18n-allow: fixture
    ("nimm haiku", "depth_fast", ""),
]
```

- [ ] **Step 2: Create the checklist + completeness guard test**

Create `tests/unit/brain/test_voice_command_checklist.py`:

```python
"""Runs the whole recognition checklist, and guards that every command KIND the
gate can emit has a matching honesty test — so a new deterministic command
cannot be added without an honest readback (the audit 2026-06-27 root class).
"""
from __future__ import annotations

import typing

import pytest

from jarvis.brain.voice_command_gate import VoiceCommandMatch, match_voice_command

from tests.unit.brain.voice_command_cases import RECOGNITION_CASES


@pytest.mark.parametrize("utterance,kind,target", RECOGNITION_CASES)
def test_recognition_checklist(utterance: str, kind: str, target: str) -> None:
    m = match_voice_command(utterance)
    assert m is not None, f"not recognised: {utterance!r}"
    assert m.kind == kind, f"{utterance!r}: expected {kind}, got {m.kind}"
    if target:
        assert m.target == target, f"{utterance!r}: expected target {target!r}, got {m.target!r}"


def test_every_command_kind_has_an_honesty_test() -> None:
    """Anti-drift: the set of kinds the gate can emit must equal the set we have
    deliberately given an honest readback. Adding a new kind to the gate without
    updating this guard (and adding an honesty test) fails here — the structural
    insurance against a new silent/blind command."""
    # The kinds the gate's Literal can emit (single source of truth).
    gate_kinds = set(typing.get_args(VoiceCommandMatch.__dataclass_fields__["kind"].type))
    # The kinds we have audited and given an honest readback (2026-06-27).
    audited_kinds = {
        "provider_switch",
        "subagent_switch",
        "language_switch",
        "cancel",
        "depth_deep",
        "depth_fast",
    }
    assert gate_kinds == audited_kinds, (
        "voice command kinds drifted — a new kind needs an honest readback + an "
        f"honesty test. gate={sorted(gate_kinds)} audited={sorted(audited_kinds)}"
    )
```

Note: `VoiceCommandMatch.__dataclass_fields__["kind"].type` is the `Literal[...]` (because `from __future__ import annotations` stores it as a string only if not resolved — if `get_args` returns empty, fall back to parsing `typing.get_type_hints(VoiceCommandMatch)["kind"]`). Verify in Step 3 and adjust if the field type is a string.

- [ ] **Step 3: Run the checklist + guard**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_checklist.py -q`
Expected: PASS. If `test_every_command_kind_has_an_honesty_test` errors because `get_args` returned empty (annotations stored as strings), change the `gate_kinds` line to:
```python
    import typing
    hints = typing.get_type_hints(VoiceCommandMatch)
    gate_kinds = set(typing.get_args(hints["kind"]))
```
and re-run until PASS.

- [ ] **Step 4: Full regression sweep**

Run: `& "C:\Program Files\Python311\python.exe" -m pytest tests/unit/brain/test_voice_command_gate.py tests/unit/brain/test_voice_command_honesty.py tests/unit/brain/test_voice_command_checklist.py tests/unit/brain/test_provider_alias_resolution.py tests/unit/brain/test_subagent_voice_switch_validated.py tests/unit/brain/test_routing.py -q`
Expected: PASS (all green).

- [ ] **Step 5: Commit (hunk-isolated)**

Message subject: `test(voice): recognition checklist + anti-drift honesty-coverage guard`
Paths: `tests/unit/brain/voice_command_cases.py tests/unit/brain/test_voice_command_checklist.py`.

---

## Self-Review

**Spec coverage:**
- Honesty — provider_switch (Task 1), cancel (Task 2), depth (Task 3), language persist (Task 4): covered. ✓
- Recognition gaps — provider verbs + cancel halt (Task 5): covered. ✓
- Structural insurance — recognition checklist + completeness guard (Task 6): covered. ✓
- open_app blind success: explicitly documented as out-of-scope with justification (not a silent cap). ✓

**Type consistency:** `_apply_main_provider_switch` / `_cancel_readback` / `_depth_readback` signatures match their callers and tests. `apply_provider_switch` result keys (`ok`, `error_kind`, `new_provider`) match app_control.py:472-480. `PROVIDER_ALIASES` resolves brand words → canonical before `apply_provider_switch("brain", …)`, which then validates via `get_spec`. ✓

**Placeholder scan:** no TBD/TODO; every code step shows full code. The one conditional is Task 6 Step 3 (the `get_args` fallback), which is concrete and resolved during the step. ✓
