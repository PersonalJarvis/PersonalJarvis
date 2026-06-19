# First-Time Onboarding & Setup Guide Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Replace the single-step wake-word overlay with a branded, multi-step first-time setup guide (welcome + intro clip, legal terms gate, language, wake-word, API keys, mic test, persona/theme, finish) that runs on a fresh clone with zero config.

**Architecture:** Extend the existing blocking overlay pattern (`WakeWordOnboardingGate` in `App.tsx`) into an `OnboardingGate` → `OnboardingFlow` stepper. A tiny JSON state store (`setup_state.json`) plus a new `/api/onboarding/*` router track completion, the resume step, skips, terms acceptance, and the wake-word responsibility acknowledgment. The legal posture is **no technical blocking**: the user picks any wake-word but must accept versioned Terms and tick a wake-word responsibility acknowledgment; informational reference links are shown.

**Tech Stack:** Python 3.11 / FastAPI (backend routes + state), React 18 + TypeScript + Tailwind + zustand (frontend), vitest + @testing-library/react (frontend tests), pytest + FastAPI TestClient (backend tests).

## Global Constraints

- **Artifacts are English.** All code, comments, docstrings, route descriptions, and i18n **source** strings are English. German/Spanish only as i18n translation values. CI `language-policy` gate blocks new German source lines.
- **i18n:** new UI strings get an English source key in `en.json` plus translations in `de.json` and `es.json`. Resolve via `useT()` (`@/i18n`), dotted keys.
- **No new hard dependency.** Nothing Windows-only or GPU-only in the importable path. Base install must still boot on `python:3.11-slim`.
- **Atomic state writes** reuse the existing tempfile + `os.replace` pattern in `jarvis/setup/state.py`. Never raise from the state layer — best-effort UX.
- **Routes never 5xx on read.** `GET /api/onboarding/state` fails open (reports a safe default) — same posture as `setup_routes.py`.
- **Fail-open gate.** If the state fetch errors, the gate renders nothing (never trap the user). There is **no** server-side wake-word trademark rejection.
- **Canonical step keys (single source of truth, exact strings):** `welcome`, `terms`, `language`, `wake-word`, `api-keys`, `mic-test`, `persona-theme`, `finish`.
- **Terms version:** `CURRENT_TERMS_VERSION = "1.0"`.
- **Frontend tests** mock `@/i18n` with an identity translator and stub `fetch` via `vi.stubGlobal` (see `WakeWordOnboardingGate.test.tsx`).

---

## File Structure

**Backend (create):**
- `jarvis/setup/onboarding_meta.py` — constants: `CURRENT_TERMS_VERSION`, `WAKE_WORD_LEGAL_REFERENCES`, `ONBOARDING_STEPS`.
- `jarvis/ui/web/onboarding_routes.py` — `/api/onboarding/*` router (state, step, accept-terms, acknowledge-wake-word, complete).
- `docs/legal/TERMS.md` — canonical English Terms & Disclaimer (v1.0).
- `tests/unit/setup/test_onboarding_state.py`, `tests/unit/ui/test_onboarding_routes.py`.

**Backend (modify):**
- `jarvis/setup/state.py` — add onboarding getters/setters; extract a shared `_merge_state` atomic writer.
- `jarvis/ui/web/server.py` — register the onboarding router.
- `jarvis/__main__.py` — add `--reset-onboarding`.

**Frontend (create):**
- `src/hooks/useOnboarding.ts` — state + actions hook.
- `src/components/onboarding/OnboardingGate.tsx` — visibility/resume/fail-open.
- `src/components/onboarding/OnboardingFlow.tsx` — stepper shell + `StepProps`.
- `src/components/onboarding/IntroClip.tsx` — video-or-Gigi media slot.
- `src/components/onboarding/steps/{Welcome,Terms,Language,WakeWord,ApiKeys,MicTest,PersonaTheme,Finish}Step.tsx`.
- matching `*.test.tsx` / `*.test.ts` files.

**Frontend (modify):**
- `src/App.tsx` — swap `<WakeWordOnboardingGate />` for `<OnboardingGate />`.
- `src/i18n/locales/{en,de,es}.json` — `onboarding.*` keys.

**Removal:** `WakeWordOnboardingGate.tsx` (+ its test) is superseded by the wake-word *step*. Remove it in Task 7 once `OnboardingGate` mounts.

---

## PHASE 1 — BACKEND

### Task 1: Onboarding state functions

**Files:**
- Modify: `jarvis/setup/state.py`
- Test: `tests/unit/setup/test_onboarding_state.py` (create)

**Interfaces:**
- Consumes: existing `load_setup_state(path)`, `state_path(path)`.
- Produces:
  - `get_onboarding_state(path=None) -> dict[str, Any]` with keys `completed_at`, `current_step`, `skipped_steps`, `terms_accepted_at`, `terms_version`, `wake_word_acknowledged_at`.
  - `set_onboarding_step(step: str, skipped: list[str] | None = None, path=None) -> None`
  - `accept_terms(version: str, path=None) -> None`
  - `acknowledge_wake_word(path=None) -> None`
  - `mark_onboarding_complete(path=None) -> None`
  - `is_onboarding_complete(path=None) -> bool`
  - `reset_onboarding(path=None) -> list[str]` (returns removed keys)
  - `_merge_state(updates: dict[str, Any], path=None) -> None` (private, atomic).

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/setup/test_onboarding_state.py
from jarvis.setup import state as st


def test_onboarding_roundtrip(tmp_path):
    p = tmp_path / "setup_state.json"

    # Fresh state: everything empty/None.
    assert st.is_onboarding_complete(p) is False
    fresh = st.get_onboarding_state(p)
    assert fresh["completed_at"] is None
    assert fresh["current_step"] is None
    assert fresh["skipped_steps"] == []
    assert fresh["terms_version"] is None
    assert fresh["wake_word_acknowledged_at"] is None

    # Record progress.
    st.set_onboarding_step("wake-word", skipped=["api-keys"], path=p)
    st.accept_terms("1.0", path=p)
    st.acknowledge_wake_word(p)
    mid = st.get_onboarding_state(p)
    assert mid["current_step"] == "wake-word"
    assert mid["skipped_steps"] == ["api-keys"]
    assert mid["terms_version"] == "1.0"
    assert isinstance(mid["terms_accepted_at"], str) and mid["terms_accepted_at"]
    assert isinstance(mid["wake_word_acknowledged_at"], str)

    # Complete.
    st.mark_onboarding_complete(p)
    assert st.is_onboarding_complete(p) is True

    # An unrelated key written earlier is preserved by the merge writer.
    st._merge_state({"obsidian_setup_seen_at": "2026-01-01T00:00:00+00:00"}, p)
    st.set_onboarding_step("finish", path=p)
    assert st.load_setup_state(p)["obsidian_setup_seen_at"] == "2026-01-01T00:00:00+00:00"

    # Reset clears only onboarding keys, keeps the foreign key.
    removed = st.reset_onboarding(p)
    assert "onboarding_completed_at" in removed
    after = st.get_onboarding_state(p)
    assert after["completed_at"] is None
    assert st.load_setup_state(p)["obsidian_setup_seen_at"] == "2026-01-01T00:00:00+00:00"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/setup/test_onboarding_state.py -v`
Expected: FAIL — `AttributeError: module 'jarvis.setup.state' has no attribute 'is_onboarding_complete'`.

- [ ] **Step 3: Write minimal implementation**

Add to `jarvis/setup/state.py` (after `mark_obsidian_seen`, before `__all__`). First extract the shared atomic writer, then build the onboarding helpers on top of it:

```python
# ---------------------------------------------------------------------------
# Shared atomic merge-writer (extracted from mark_obsidian_seen).
# ---------------------------------------------------------------------------
_ONBOARDING_KEYS = (
    "onboarding_completed_at",
    "onboarding_step",
    "onboarding_skipped_steps",
    "terms_accepted_at",
    "terms_version",
    "wake_word_acknowledged_at",
)


def _merge_state(updates: dict[str, Any], path: Path | None = None) -> None:
    """Merge ``updates`` into the state file atomically. Never raises."""
    target = state_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        logger.warning("_merge_state: cannot mkdir %s: %s", target.parent, exc)
        return

    state = load_setup_state(path)
    state.update(updates)

    tempfile_path: Path | None = None
    try:
        tempfile_path = target.with_suffix(target.suffix + f".tmp-{secrets.token_hex(4)}")
        with open(tempfile_path, "w", encoding="utf-8", newline="") as fp:
            json.dump(state, fp, indent=2, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tempfile_path, target)
        tempfile_path = None
    except OSError as exc:
        logger.warning("_merge_state: write failed for %s: %s", target, exc)
    finally:
        if tempfile_path is not None and tempfile_path.exists():
            try:
                tempfile_path.unlink()
            except OSError:
                logger.debug("_merge_state: tempfile cleanup failed for %s", tempfile_path)


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# First-time onboarding flags.
# ---------------------------------------------------------------------------
def get_onboarding_state(path: Path | None = None) -> dict[str, Any]:
    """Return a normalized onboarding view (missing keys default to None/[])."""
    s = load_setup_state(path)
    skipped = s.get("onboarding_skipped_steps")
    return {
        "completed_at": s.get("onboarding_completed_at") or None,
        "current_step": s.get("onboarding_step") or None,
        "skipped_steps": list(skipped) if isinstance(skipped, list) else [],
        "terms_accepted_at": s.get("terms_accepted_at") or None,
        "terms_version": s.get("terms_version") or None,
        "wake_word_acknowledged_at": s.get("wake_word_acknowledged_at") or None,
    }


def is_onboarding_complete(path: Path | None = None) -> bool:
    value = load_setup_state(path).get("onboarding_completed_at")
    return isinstance(value, str) and bool(value)


def set_onboarding_step(
    step: str, skipped: list[str] | None = None, path: Path | None = None
) -> None:
    updates: dict[str, Any] = {"onboarding_step": step}
    if skipped is not None:
        updates["onboarding_skipped_steps"] = list(skipped)
    _merge_state(updates, path)


def accept_terms(version: str, path: Path | None = None) -> None:
    _merge_state({"terms_accepted_at": _now_iso(), "terms_version": version}, path)


def acknowledge_wake_word(path: Path | None = None) -> None:
    _merge_state({"wake_word_acknowledged_at": _now_iso()}, path)


def mark_onboarding_complete(path: Path | None = None) -> None:
    _merge_state({"onboarding_completed_at": _now_iso()}, path)


def reset_onboarding(path: Path | None = None) -> list[str]:
    """Remove all onboarding keys from the state file. Returns the removed keys."""
    s = load_setup_state(path)
    removed = [k for k in _ONBOARDING_KEYS if k in s]
    for k in removed:
        s.pop(k, None)
    # Rewrite the full surviving dict atomically (reuse the temp-replace pattern).
    target = state_path(path)
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp = target.with_suffix(target.suffix + f".tmp-{secrets.token_hex(4)}")
        with open(tmp, "w", encoding="utf-8", newline="") as fp:
            json.dump(s, fp, indent=2, ensure_ascii=False)
            fp.flush()
            os.fsync(fp.fileno())
        os.replace(tmp, target)
    except OSError as exc:
        logger.warning("reset_onboarding: write failed for %s: %s", target, exc)
    return removed
```

Then add the new names to `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/setup/test_onboarding_state.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/setup/state.py tests/unit/setup/test_onboarding_state.py
git commit -m "feat(setup): onboarding state flags (step/terms/wake-word ack/complete)"
```

---

### Task 2: Onboarding metadata constants + Terms doc

**Files:**
- Create: `jarvis/setup/onboarding_meta.py`
- Create: `docs/legal/TERMS.md`
- Test: `tests/unit/setup/test_onboarding_meta.py` (create)

**Interfaces:**
- Produces: `CURRENT_TERMS_VERSION: str`, `WAKE_WORD_LEGAL_REFERENCES: list[dict[str, str]]` (each `{"label","url"}`), `ONBOARDING_STEPS: list[str]`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/setup/test_onboarding_meta.py
from jarvis.setup import onboarding_meta as m


def test_meta_constants():
    assert m.CURRENT_TERMS_VERSION == "1.0"
    assert m.ONBOARDING_STEPS[0] == "welcome"
    assert m.ONBOARDING_STEPS[-1] == "finish"
    assert "terms" in m.ONBOARDING_STEPS and "wake-word" in m.ONBOARDING_STEPS
    assert len(m.WAKE_WORD_LEGAL_REFERENCES) >= 3
    for ref in m.WAKE_WORD_LEGAL_REFERENCES:
        assert ref["label"] and ref["url"].startswith("https://")
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/setup/test_onboarding_meta.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.setup.onboarding_meta'`.

- [ ] **Step 3: Write minimal implementation**

```python
# jarvis/setup/onboarding_meta.py
"""Static metadata for the first-time onboarding guide.

Single source of truth for the shipped Terms version, the canonical step
order, and the informational trademark reference links shown on the
wake-word step. There is deliberately NO denylist — the user chooses any
activation word and self-certifies responsibility (see docs/legal/TERMS.md).
"""
from __future__ import annotations

CURRENT_TERMS_VERSION = "1.0"

# Canonical step order — must match the frontend StepProps keys.
ONBOARDING_STEPS: list[str] = [
    "welcome",
    "terms",
    "language",
    "wake-word",
    "api-keys",
    "mic-test",
    "persona-theme",
    "finish",
]

# Informational only; not exhaustive and possibly out of date (stated in the UI).
WAKE_WORD_LEGAL_REFERENCES: list[dict[str, str]] = [
    {"label": "EUIPO trademark search (EU)", "url": "https://euipo.europa.eu/eSearch/"},
    {"label": "USPTO trademark search (US)", "url": "https://www.uspto.gov/trademarks/search"},
    {"label": "WIPO Global Brand Database", "url": "https://branddb.wipo.int/"},
    {"label": "DPMA register (Germany)", "url": "https://register.dpma.de/"},
]

__all__ = ["CURRENT_TERMS_VERSION", "ONBOARDING_STEPS", "WAKE_WORD_LEGAL_REFERENCES"]
```

Create `docs/legal/TERMS.md` with the canonical text from the spec §6.1 (verbatim — heading `# Personal Jarvis — Terms of Use & Disclaimer (v1.0)` followed by the 8 numbered clauses).

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/setup/test_onboarding_meta.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/setup/onboarding_meta.py docs/legal/TERMS.md tests/unit/setup/test_onboarding_meta.py
git commit -m "feat(setup): onboarding metadata constants + canonical Terms doc"
```

---

### Task 3: Onboarding routes + server registration

**Files:**
- Create: `jarvis/ui/web/onboarding_routes.py`
- Modify: `jarvis/ui/web/server.py` (import at line ~244, include at line ~319)
- Test: `tests/unit/ui/test_onboarding_routes.py` (create)

**Interfaces:**
- Consumes: Task 1 state functions, Task 2 constants, `jarvis.core.config.is_first_run`.
- Produces (endpoints):
  - `GET /api/onboarding/state` → `{completed, current_step, skipped_steps, terms: {accepted, accepted_version, current_version}, wake_word_acknowledged, legal_references, steps}`
  - `POST /api/onboarding/step` body `{step: str, skipped?: list[str]}` → `{ok: true}`
  - `POST /api/onboarding/accept-terms` → `{ok: true, version: str}`
  - `POST /api/onboarding/acknowledge-wake-word` → `{ok: true}`
  - `POST /api/onboarding/complete` → `{ok: true}`
- **Migration + force rule:** `completed = False if JARVIS_FORCE_ONBOARDING set, else (state.completed_at is not None) or (not is_first_run())`.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/ui/test_onboarding_routes.py
import os
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from jarvis.ui.web import onboarding_routes


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Redirect the state file into tmp via the module's path override.
    monkeypatch.setattr(onboarding_routes, "_STATE_PATH_OVERRIDE", tmp_path / "s.json")
    # Pretend this is a first run (no legacy .setup-complete).
    monkeypatch.setattr(onboarding_routes, "is_first_run", lambda: True)
    monkeypatch.delenv("JARVIS_FORCE_ONBOARDING", raising=False)
    app = FastAPI()
    app.include_router(onboarding_routes.router)
    return TestClient(app)


def test_state_starts_incomplete(client):
    r = client.get("/api/onboarding/state")
    assert r.status_code == 200
    body = r.json()
    assert body["completed"] is False
    assert body["terms"]["current_version"] == "1.0"
    assert body["terms"]["accepted"] is False
    assert body["steps"][0] == "welcome"
    assert len(body["legal_references"]) >= 3


def test_accept_terms_then_complete(client):
    assert client.post("/api/onboarding/accept-terms").json()["version"] == "1.0"
    client.post("/api/onboarding/acknowledge-wake-word")
    client.post("/api/onboarding/step", json={"step": "finish", "skipped": ["mic-test"]})
    client.post("/api/onboarding/complete")
    body = client.get("/api/onboarding/state").json()
    assert body["completed"] is True
    assert body["terms"]["accepted"] is True
    assert body["wake_word_acknowledged"] is True
    assert body["skipped_steps"] == ["mic-test"]


def test_legacy_install_is_migrated(client, monkeypatch):
    # Not first run (legacy .setup-complete present) → treated as onboarded.
    monkeypatch.setattr(onboarding_routes, "is_first_run", lambda: False)
    assert client.get("/api/onboarding/state").json()["completed"] is True


def test_force_env_overrides(client, monkeypatch):
    monkeypatch.setattr(onboarding_routes, "is_first_run", lambda: False)
    monkeypatch.setenv("JARVIS_FORCE_ONBOARDING", "1")
    assert client.get("/api/onboarding/state").json()["completed"] is False
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/ui/test_onboarding_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'jarvis.ui.web.onboarding_routes'`.

- [ ] **Step 3: Write minimal implementation**

```python
# jarvis/ui/web/onboarding_routes.py
"""FastAPI routes for the first-time onboarding guide.

All reads fail open (never 5xx): a missing/corrupt state file reports a safe
"incomplete" default so the UI gate can decide its own behaviour. The legal
posture is no server-side blocking — the wake-word save route is unchanged;
this module only records acceptance/acknowledgment + the resume pointer.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

from fastapi import APIRouter
from pydantic import BaseModel

from jarvis.core.config import is_first_run
from jarvis.setup import state as st
from jarvis.setup.onboarding_meta import (
    CURRENT_TERMS_VERSION,
    ONBOARDING_STEPS,
    WAKE_WORD_LEGAL_REFERENCES,
)

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])

# Tests override this to redirect the state file; production leaves it None
# (state.py resolves the default data/setup_state.json).
_STATE_PATH_OVERRIDE: Path | None = None


def _path() -> Path | None:
    return _STATE_PATH_OVERRIDE


class StepBody(BaseModel):
    step: str
    skipped: list[str] | None = None


def _force_onboarding() -> bool:
    return bool(os.environ.get("JARVIS_FORCE_ONBOARDING"))


@router.get("/state")
async def get_state() -> dict:
    try:
        s = st.get_onboarding_state(_path())
        legacy_done = False
        try:
            legacy_done = not is_first_run()
        except Exception as exc:  # noqa: BLE001
            log.debug("onboarding: is_first_run failed: %s", exc)
        completed = (s["completed_at"] is not None) or legacy_done
        if _force_onboarding():
            completed = False
        return {
            "completed": completed,
            "current_step": s["current_step"],
            "skipped_steps": s["skipped_steps"],
            "terms": {
                "accepted": s["terms_accepted_at"] is not None,
                "accepted_version": s["terms_version"],
                "current_version": CURRENT_TERMS_VERSION,
            },
            "wake_word_acknowledged": s["wake_word_acknowledged_at"] is not None,
            "legal_references": WAKE_WORD_LEGAL_REFERENCES,
            "steps": ONBOARDING_STEPS,
        }
    except Exception as exc:  # noqa: BLE001 — UI must keep working
        log.warning("onboarding_get_state_failed: %s", exc, exc_info=True)
        return {
            "completed": False,
            "current_step": None,
            "skipped_steps": [],
            "terms": {"accepted": False, "accepted_version": None, "current_version": CURRENT_TERMS_VERSION},
            "wake_word_acknowledged": False,
            "legal_references": WAKE_WORD_LEGAL_REFERENCES,
            "steps": ONBOARDING_STEPS,
        }


@router.post("/step")
async def post_step(body: StepBody) -> dict:
    st.set_onboarding_step(body.step, skipped=body.skipped, path=_path())
    return {"ok": True}


@router.post("/accept-terms")
async def post_accept_terms() -> dict:
    st.accept_terms(CURRENT_TERMS_VERSION, path=_path())
    return {"ok": True, "version": CURRENT_TERMS_VERSION}


@router.post("/acknowledge-wake-word")
async def post_ack_wake_word() -> dict:
    st.acknowledge_wake_word(_path())
    return {"ok": True}


@router.post("/complete")
async def post_complete() -> dict:
    st.mark_onboarding_complete(_path())
    return {"ok": True}


__all__ = ["router"]
```

Register in `jarvis/ui/web/server.py`: add near line 244 `from .onboarding_routes import router as onboarding_router` and near line 319 (next to `app.include_router(setup_router)`) `app.include_router(onboarding_router)`.

- [ ] **Step 4: Run tests to verify they pass**

Run: `pytest tests/unit/ui/test_onboarding_routes.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/onboarding_routes.py jarvis/ui/web/server.py tests/unit/ui/test_onboarding_routes.py
git commit -m "feat(onboarding): /api/onboarding/* routes with migration + force-env"
```

---

### Task 4: `--reset-onboarding` CLI

**Files:**
- Modify: `jarvis/__main__.py` (`_parse_args` ~line 38, `main` dispatch ~line 311)
- Test: `tests/unit/setup/test_reset_onboarding_cli.py` (create)

**Interfaces:**
- Consumes: Task 1 `state.reset_onboarding`, `jarvis.core.config.DATA_DIR`.
- Produces: `_cmd_reset_onboarding() -> int` and the `--reset-onboarding` flag. Clears onboarding state keys AND removes `data/.setup-complete` so the next launch shows the guide.

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/setup/test_reset_onboarding_cli.py
from jarvis import __main__ as m
from jarvis.setup import state as st


def test_reset_onboarding_clears_markers(tmp_path, monkeypatch, capsys):
    state_file = tmp_path / "setup_state.json"
    setup_complete = tmp_path / ".setup-complete"
    setup_complete.write_text("done", encoding="utf-8")
    st.mark_onboarding_complete(state_file)

    monkeypatch.setattr(m.cfg, "DATA_DIR", tmp_path, raising=False)
    monkeypatch.setattr(m, "_ONBOARDING_STATE_PATH", state_file, raising=False)

    rc = m._cmd_reset_onboarding()
    assert rc == 0
    assert st.is_onboarding_complete(state_file) is False
    assert not setup_complete.exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/unit/setup/test_reset_onboarding_cli.py -v`
Expected: FAIL — `AttributeError: module 'jarvis.__main__' has no attribute '_cmd_reset_onboarding'`.

- [ ] **Step 3: Write minimal implementation**

In `jarvis/__main__.py`, add the flag in `_parse_args`:

```python
    parser.add_argument(
        "--reset-onboarding",
        action="store_true",
        dest="reset_onboarding",
        help="Clear onboarding markers so the first-run guide shows again.",
    )
```

Add the command function and a test-overridable state path near the other `_cmd_*`:

```python
from jarvis.core import config as cfg  # if not already imported
from jarvis.setup import state as _onb_state

_ONBOARDING_STATE_PATH = None  # tests override; None => state.py default


def _cmd_reset_onboarding() -> int:
    removed = _onb_state.reset_onboarding(_ONBOARDING_STATE_PATH)
    marker = cfg.DATA_DIR / ".setup-complete"
    if marker.exists():
        try:
            marker.unlink()
        except OSError as exc:
            print(f"Could not remove {marker}: {exc}")
    print(f"Onboarding reset. Cleared keys: {removed or 'none'}; removed .setup-complete.")
    print("Next launch will show the setup guide.")
    return 0
```

Dispatch it in `main()` before the `args.wizard or cfg.is_first_run()` branch:

```python
    if args.reset_onboarding:
        return _cmd_reset_onboarding()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/unit/setup/test_reset_onboarding_cli.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/__main__.py tests/unit/setup/test_reset_onboarding_cli.py
git commit -m "feat(cli): --reset-onboarding clears guide markers for a fresh run"
```

---

## PHASE 2 — FRONTEND

### Task 5: i18n keys for the onboarding namespace

**Files:**
- Modify: `src/i18n/locales/en.json`, `de.json`, `es.json`
- Test: `tests/unit/...` not needed; covered by the existing `src/__tests__/i18n.test.ts` parity test (run it).

**Interfaces:**
- Produces: the `onboarding.*` key tree (English source). Add the same key paths to all three files. Minimum keys (extend as steps need them):

```
onboarding.nav.next / back / skip / finish
onboarding.welcome.title / subtitle / cta / skip_setup
onboarding.terms.title / intro / accept_label / continue / read_more
onboarding.terms.body            (the v1.0 Terms text — English source mirrors docs/legal/TERMS.md)
onboarding.language.title / ui_label / reply_label
onboarding.wake_word.title / body / prefix / input_label / placeholder
onboarding.wake_word.notice / references_title / references_caveat / ack_label / cta / saving
onboarding.api_keys.title / body / skip / works_now
onboarding.mic_test.title / body / no_mic / play_sample / skip
onboarding.persona.title / name_label / theme_label / skip
onboarding.finish.title / body / start_cta / skipped_title
```

- [ ] **Step 1: Add the English source keys** to `en.json` under a new top-level `"onboarding"` object (real copy, not placeholders), e.g. `"welcome": {"title": "Welcome to Personal Jarvis", "subtitle": "Your local, private voice assistant.", "cta": "Get started", "skip_setup": "Skip setup"}`, the full Terms body string under `onboarding.terms.body`, etc.

- [ ] **Step 2: Add German translations** to `de.json` at the identical key paths.

- [ ] **Step 3: Add Spanish translations** to `es.json` at the identical key paths.

- [ ] **Step 4: Run the i18n parity test**

Run: `cd jarvis/ui/web/frontend && npm run test -- i18n`
Expected: PASS (all three locales have matching key sets).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "i18n(onboarding): add onboarding.* key tree (en/de/es)"
```

---

### Task 6: `useOnboarding` hook

**Files:**
- Create: `src/hooks/useOnboarding.ts`
- Test: `src/hooks/useOnboarding.test.ts` (create)

**Interfaces:**
- Produces:
  - `interface OnboardingState { completed: boolean; current_step: string | null; skipped_steps: string[]; terms: { accepted: boolean; accepted_version: string | null; current_version: string }; wake_word_acknowledged: boolean; legal_references: { label: string; url: string }[]; steps: string[]; }`
  - `useOnboarding()` → `{ state, loading, error, refetch, saveStep, acceptTerms, acknowledgeWakeWord, complete }` where `saveStep(step: string, skipped?: string[]) => Promise<void>`, the rest `() => Promise<void>`. `complete()` dispatches `window` event `jarvis:onboarding-changed`.

- [ ] **Step 1: Write the failing test**

```ts
// src/hooks/useOnboarding.test.ts
import { renderHook, act, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { useOnboarding } from "./useOnboarding";

afterEach(() => vi.restoreAllMocks());

const STATE = {
  completed: false,
  current_step: null,
  skipped_steps: [],
  terms: { accepted: false, accepted_version: null, current_version: "1.0" },
  wake_word_acknowledged: false,
  legal_references: [{ label: "EUIPO", url: "https://euipo.europa.eu/eSearch/" }],
  steps: ["welcome", "terms", "finish"],
};

it("loads state and posts step", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation((url: string, init?: RequestInit) => {
      calls.push([url, init]);
      return Promise.resolve({ ok: true, json: () => Promise.resolve(STATE) });
    }),
  );

  const { result } = renderHook(() => useOnboarding());
  await waitFor(() => expect(result.current.state?.terms.current_version).toBe("1.0"));

  await act(async () => {
    await result.current.saveStep("terms", ["mic-test"]);
  });
  const put = calls.find(([u, i]) => u === "/api/onboarding/step" && i?.method === "POST");
  expect(put).toBeDefined();
  expect(JSON.parse((put![1]!.body as string)).step).toBe("terms");
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- useOnboarding`
Expected: FAIL — cannot resolve `./useOnboarding`.

- [ ] **Step 3: Write minimal implementation**

```ts
// src/hooks/useOnboarding.ts
import { useCallback, useEffect, useState } from "react";

export interface LegalReference {
  label: string;
  url: string;
}

export interface OnboardingState {
  completed: boolean;
  current_step: string | null;
  skipped_steps: string[];
  terms: { accepted: boolean; accepted_version: string | null; current_version: string };
  wake_word_acknowledged: boolean;
  legal_references: LegalReference[];
  steps: string[];
}

async function post(url: string, body?: unknown): Promise<void> {
  await fetch(url, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

export function useOnboarding() {
  const [state, setState] = useState<OnboardingState | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/onboarding/state");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setState((await res.json()) as OnboardingState);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const saveStep = useCallback(
    (step: string, skipped?: string[]) =>
      post("/api/onboarding/step", { step, skipped }),
    [],
  );
  const acceptTerms = useCallback(() => post("/api/onboarding/accept-terms"), []);
  const acknowledgeWakeWord = useCallback(
    () => post("/api/onboarding/acknowledge-wake-word"),
    [],
  );
  const complete = useCallback(async () => {
    await post("/api/onboarding/complete");
    window.dispatchEvent(new CustomEvent("jarvis:onboarding-changed"));
  }, []);

  return { state, loading, error, refetch, saveStep, acceptTerms, acknowledgeWakeWord, complete };
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- useOnboarding`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/hooks/useOnboarding.ts jarvis/ui/web/frontend/src/hooks/useOnboarding.test.ts
git commit -m "feat(onboarding): useOnboarding hook (state + step/terms/ack/complete)"
```

---

### Task 7: `OnboardingGate` + App wire-in (replaces WakeWordOnboardingGate)

**Files:**
- Create: `src/components/onboarding/OnboardingGate.tsx`
- Modify: `src/App.tsx`
- Delete: `src/components/onboarding/WakeWordOnboardingGate.tsx` + `WakeWordOnboardingGate.test.tsx`
- Test: `src/components/onboarding/OnboardingGate.test.tsx` (create)

**Interfaces:**
- Consumes: `useOnboarding` (Task 6), `OnboardingFlow` (Task 8 — for this task render a placeholder div with `role="dialog"` so the test is meaningful; Task 8 swaps in the real flow).
- Produces: `<OnboardingGate />` — renders nothing while loading, on error (fail open), or when `state.completed`; otherwise the overlay. Re-fetches on `jarvis:onboarding-changed`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/OnboardingGate.test.tsx
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("./OnboardingFlow", () => ({
  OnboardingFlow: () => <div data-testid="flow" />,
}));

import { OnboardingGate } from "./OnboardingGate";

afterEach(() => { cleanup(); vi.restoreAllMocks(); });

function stub(state: object | "error") {
  vi.stubGlobal(
    "fetch",
    vi.fn().mockImplementation(() =>
      state === "error"
        ? Promise.reject(new Error("net"))
        : Promise.resolve({ ok: true, json: () => Promise.resolve(state) }),
    ),
  );
}

const base = {
  current_step: null, skipped_steps: [],
  terms: { accepted: false, accepted_version: null, current_version: "1.0" },
  wake_word_acknowledged: false, legal_references: [], steps: ["welcome"],
};

it("shows the overlay when not completed", async () => {
  stub({ ...base, completed: false });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.getByRole("dialog")).toBeDefined());
});

it("renders nothing when completed", async () => {
  stub({ ...base, completed: true });
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
});

it("fails open on error", async () => {
  stub("error");
  render(<OnboardingGate />);
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull(), { timeout: 500 });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- OnboardingGate`
Expected: FAIL — cannot resolve `./OnboardingGate`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/OnboardingGate.tsx
import { useEffect } from "react";
import { useOnboarding } from "@/hooks/useOnboarding";
import { OnboardingFlow } from "./OnboardingFlow";

export function OnboardingGate() {
  const onb = useOnboarding();

  useEffect(() => {
    const onChanged = () => void onb.refetch();
    window.addEventListener("jarvis:onboarding-changed", onChanged);
    return () => window.removeEventListener("jarvis:onboarding-changed", onChanged);
  }, [onb]);

  if (onb.loading) return null;
  if (onb.error) return null; // fail open
  if (!onb.state || onb.state.completed) return null;

  return (
    <div
      role="dialog"
      aria-modal="true"
      className="fixed inset-0 z-50 flex items-center justify-center bg-background/90 backdrop-blur-sm"
    >
      <OnboardingFlow onb={onb} />
    </div>
  );
}
```

Update `src/App.tsx`: change the import on line 10 to `import { OnboardingGate } from "@/components/onboarding/OnboardingGate";` and the mount on line 42 to `<OnboardingGate />`. Delete `WakeWordOnboardingGate.tsx` and its test.

- [ ] **Step 4: Run tests + typecheck**

Run: `cd jarvis/ui/web/frontend && npm run test -- OnboardingGate && npx tsc --noEmit`
Expected: PASS; no type errors (Task 8 must define `OnboardingFlow`'s `onb` prop — until then the mocked import keeps the test green; do Task 8 before the build).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/OnboardingGate.tsx jarvis/ui/web/frontend/src/components/onboarding/OnboardingGate.test.tsx jarvis/ui/web/frontend/src/App.tsx
git rm jarvis/ui/web/frontend/src/components/onboarding/WakeWordOnboardingGate.tsx jarvis/ui/web/frontend/src/components/onboarding/WakeWordOnboardingGate.test.tsx
git commit -m "feat(onboarding): OnboardingGate overlay replaces WakeWordOnboardingGate"
```

---

### Task 8: `OnboardingFlow` stepper shell + `StepProps`

**Files:**
- Create: `src/components/onboarding/OnboardingFlow.tsx`
- Test: `src/components/onboarding/OnboardingFlow.test.tsx` (create)

**Interfaces:**
- Consumes: the `useOnboarding()` return object via prop `onb`, `MascotGigi` (props `{size?, className?, reactToVoice?, enableComments?}`).
- Produces:
  - `export interface StepProps { onb: ReturnType<typeof useOnboarding>; goNext: () => void; goBack: () => void; skip: () => void; isFirst: boolean; isLast: boolean; }`
  - `OnboardingFlow({ onb }: { onb: ReturnType<typeof useOnboarding> })` — owns the active step index (initialized from `onb.state.current_step` against `onb.state.steps`), renders the matching step component, a progress indicator, a decorative `<MascotGigi>` host, and persists the step via `onb.saveStep` on change. `goNext` past the last step calls `onb.complete()`.
- The step registry maps step key → component (filled in by Tasks 10–17; until then, unknown keys render a fallback `<div>`).

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/OnboardingFlow.test.tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="gigi" /> }));

import { OnboardingFlow } from "./OnboardingFlow";

afterEach(cleanup);

function makeOnb(overrides = {}) {
  return {
    state: {
      completed: false, current_step: null, skipped_steps: [],
      terms: { accepted: false, accepted_version: null, current_version: "1.0" },
      wake_word_acknowledged: false, legal_references: [],
      steps: ["welcome", "terms", "finish"],
    },
    loading: false, error: null,
    refetch: vi.fn(), saveStep: vi.fn(), acceptTerms: vi.fn(),
    acknowledgeWakeWord: vi.fn(), complete: vi.fn(),
    ...overrides,
  } as never;
}

it("renders the first step and a Gigi host", () => {
  render(<OnboardingFlow onb={makeOnb()} />);
  expect(screen.getByTestId("gigi")).toBeDefined();
  expect(screen.getByText("onboarding.welcome.title")).toBeDefined();
});

it("advancing persists the step", () => {
  const onb = makeOnb();
  render(<OnboardingFlow onb={onb} />);
  fireEvent.click(screen.getByRole("button", { name: "onboarding.welcome.cta" }));
  expect((onb as never as { saveStep: ReturnType<typeof vi.fn> }).saveStep).toHaveBeenCalledWith("terms", []);
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- OnboardingFlow`
Expected: FAIL — cannot resolve `./OnboardingFlow`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/OnboardingFlow.tsx
import { useMemo, useState } from "react";
import { MascotGigi } from "@/components/MascotGigi";
import { useT } from "@/i18n";
import type { useOnboarding } from "@/hooks/useOnboarding";
import { WelcomeStep } from "./steps/WelcomeStep";
import { TermsStep } from "./steps/TermsStep";
import { LanguageStep } from "./steps/LanguageStep";
import { WakeWordStep } from "./steps/WakeWordStep";
import { ApiKeysStep } from "./steps/ApiKeysStep";
import { MicTestStep } from "./steps/MicTestStep";
import { PersonaThemeStep } from "./steps/PersonaThemeStep";
import { FinishStep } from "./steps/FinishStep";

export interface StepProps {
  onb: ReturnType<typeof useOnboarding>;
  goNext: () => void;
  goBack: () => void;
  skip: () => void;
  isFirst: boolean;
  isLast: boolean;
}

const REGISTRY: Record<string, (p: StepProps) => JSX.Element> = {
  welcome: WelcomeStep,
  terms: TermsStep,
  language: LanguageStep,
  "wake-word": WakeWordStep,
  "api-keys": ApiKeysStep,
  "mic-test": MicTestStep,
  "persona-theme": PersonaThemeStep,
  finish: FinishStep,
};

export function OnboardingFlow({ onb }: { onb: ReturnType<typeof useOnboarding> }) {
  const t = useT();
  const steps = onb.state?.steps ?? ["welcome", "finish"];
  const initial = Math.max(0, steps.indexOf(onb.state?.current_step ?? "welcome"));
  const [idx, setIdx] = useState(initial);
  const [skipped, setSkipped] = useState<string[]>(onb.state?.skipped_steps ?? []);

  const StepComp = useMemo(
    () => REGISTRY[steps[idx]] ?? ((_: StepProps) => <div>{steps[idx]}</div>),
    [steps, idx],
  );

  const persistAndGo = (next: number, nextSkipped = skipped) => {
    if (next >= steps.length) {
      void onb.complete();
      return;
    }
    setIdx(next);
    setSkipped(nextSkipped);
    void onb.saveStep(steps[next], nextSkipped);
  };

  const props: StepProps = {
    onb,
    goNext: () => persistAndGo(idx + 1),
    goBack: () => setIdx((i) => Math.max(0, i - 1)),
    skip: () => persistAndGo(idx + 1, [...new Set([...skipped, steps[idx]])]),
    isFirst: idx === 0,
    isLast: idx === steps.length - 1,
  };

  return (
    <div className="flex w-full max-w-lg flex-col gap-6 rounded-2xl border border-border bg-card p-8 shadow-2xl">
      <div className="flex items-center justify-between">
        <div className="flex gap-1.5" aria-label={t("onboarding.nav.next")}>
          {steps.map((s, i) => (
            <span
              key={s}
              className={`h-1.5 w-6 rounded-full ${i <= idx ? "bg-primary" : "bg-muted"}`}
            />
          ))}
        </div>
        <MascotGigi size={48} reactToVoice={steps[idx] === "mic-test"} enableComments={false} />
      </div>
      <StepComp {...props} />
    </div>
  );
}
```

> Note: this task depends on Tasks 10–17 for the step components. Implement the step files as stubs first (a one-line `export function XStep(_: StepProps) { return <div>key</div>; }`) so this compiles, then flesh each out in its own task. The two tests here only exercise `WelcomeStep`, so do Task 10 (WelcomeStep) together with this task or stub the rest.

- [ ] **Step 4: Run test + typecheck**

Run: `cd jarvis/ui/web/frontend && npm run test -- OnboardingFlow`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/OnboardingFlow.tsx jarvis/ui/web/frontend/src/components/onboarding/OnboardingFlow.test.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/
git commit -m "feat(onboarding): OnboardingFlow stepper shell + StepProps + step stubs"
```

---

### Task 9: `IntroClip` media slot

**Files:**
- Create: `src/components/onboarding/IntroClip.tsx`
- Test: `src/components/onboarding/IntroClip.test.tsx` (create)

**Interfaces:**
- Produces: `IntroClip({ src }: { src?: string })` — renders a `<video controls>` with the source when `src` is a non-empty string, otherwise a decorative animated Gigi fallback (`<MascotGigi size={120} />` inside a framed box). Respects `prefers-reduced-motion` by not autoplaying.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/IntroClip.test.tsx
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";

vi.mock("@/components/MascotGigi", () => ({ MascotGigi: () => <div data-testid="gigi" /> }));
import { IntroClip } from "./IntroClip";

afterEach(cleanup);

it("renders the Gigi fallback when no src", () => {
  render(<IntroClip />);
  expect(screen.getByTestId("gigi")).toBeDefined();
});

it("renders a video when src is given", () => {
  const { container } = render(<IntroClip src="/static/intro.webm" />);
  expect(container.querySelector("video")).not.toBeNull();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- IntroClip`
Expected: FAIL — cannot resolve `./IntroClip`.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/IntroClip.tsx
import { MascotGigi } from "@/components/MascotGigi";

export function IntroClip({ src }: { src?: string }) {
  if (src && src.trim().length > 0) {
    return (
      <video
        className="aspect-video w-full rounded-xl border border-border"
        src={src}
        controls
        playsInline
        preload="metadata"
      />
    );
  }
  return (
    <div className="flex aspect-video w-full items-center justify-center rounded-xl border border-border bg-gradient-to-br from-background to-card">
      <MascotGigi size={120} reactToVoice={false} enableComments={false} />
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- IntroClip`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/IntroClip.tsx jarvis/ui/web/frontend/src/components/onboarding/IntroClip.test.tsx
git commit -m "feat(onboarding): IntroClip media slot (video or Gigi fallback)"
```

---

### Task 10: WelcomeStep

**Files:**
- Modify/Create: `src/components/onboarding/steps/WelcomeStep.tsx`
- Test: `src/components/onboarding/steps/WelcomeStep.test.tsx` (create)

**Interfaces:** Consumes `StepProps`, `IntroClip`. Renders the hero (title/subtitle), `<IntroClip />`, a primary CTA `onboarding.welcome.cta` calling `goNext`, and a "skip setup" link calling `skip`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/steps/WelcomeStep.test.tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
vi.mock("../IntroClip", () => ({ IntroClip: () => <div data-testid="clip" /> }));
import { WelcomeStep } from "./WelcomeStep";
afterEach(cleanup);

it("renders the clip and advances on CTA", () => {
  const goNext = vi.fn();
  render(<WelcomeStep onb={{} as never} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst isLast={false} />);
  expect(screen.getByTestId("clip")).toBeDefined();
  fireEvent.click(screen.getByRole("button", { name: "onboarding.welcome.cta" }));
  expect(goNext).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- WelcomeStep`
Expected: FAIL (stub returns wrong content / no button).

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/steps/WelcomeStep.tsx
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";
import { IntroClip } from "../IntroClip";

export function WelcomeStep({ goNext, skip }: StepProps) {
  const t = useT();
  return (
    <div className="flex flex-col gap-5 text-center">
      <h1 className="font-display text-2xl font-semibold">{t("onboarding.welcome.title")}</h1>
      <p className="text-sm text-muted-foreground">{t("onboarding.welcome.subtitle")}</p>
      <IntroClip />
      <Button className="w-full" onClick={goNext}>{t("onboarding.welcome.cta")}</Button>
      <button className="text-xs text-muted-foreground underline" onClick={skip}>
        {t("onboarding.welcome.skip_setup")}
      </button>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- WelcomeStep`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/WelcomeStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/WelcomeStep.test.tsx
git commit -m "feat(onboarding): WelcomeStep hero + intro clip"
```

---

### Task 11: TermsStep (acceptance gate)

**Files:**
- Modify/Create: `src/components/onboarding/steps/TermsStep.tsx`
- Test: `src/components/onboarding/steps/TermsStep.test.tsx` (create)

**Interfaces:** Consumes `StepProps`. Renders the scrollable terms body (`onboarding.terms.body`), an "I accept" checkbox, and a continue button **disabled until checked**. On continue: `await onb.acceptTerms()` then `goNext()`. There is no skip on this step.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/steps/TermsStep.test.tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { TermsStep } from "./TermsStep";
afterEach(cleanup);

it("blocks continue until accepted, then accepts + advances", async () => {
  const goNext = vi.fn();
  const acceptTerms = vi.fn().mockResolvedValue(undefined);
  render(
    <TermsStep
      onb={{ acceptTerms } as never}
      goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false}
    />,
  );
  const cta = screen.getByRole("button", { name: "onboarding.terms.continue" });
  expect((cta as HTMLButtonElement).disabled).toBe(true);
  fireEvent.click(screen.getByRole("checkbox"));
  expect((cta as HTMLButtonElement).disabled).toBe(false);
  fireEvent.click(cta);
  await waitFor(() => expect(acceptTerms).toHaveBeenCalled());
  expect(goNext).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- TermsStep`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/steps/TermsStep.tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function TermsStep({ onb, goNext }: StepProps) {
  const t = useT();
  const [accepted, setAccepted] = useState(false);
  const [busy, setBusy] = useState(false);

  async function onContinue() {
    if (!accepted || busy) return;
    setBusy(true);
    try {
      await onb.acceptTerms();
      goNext();
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.terms.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.terms.intro")}</p>
      <div className="max-h-56 min-h-0 overflow-y-auto scrollbar-jarvis whitespace-pre-line rounded-md border border-border bg-background p-3 text-xs text-muted-foreground">
        {t("onboarding.terms.body")}
      </div>
      <label className="flex items-center gap-2 text-sm">
        <input type="checkbox" checked={accepted} onChange={(e) => setAccepted(e.target.checked)} />
        {t("onboarding.terms.accept_label")}
      </label>
      <Button className="w-full" disabled={!accepted || busy} onClick={onContinue}>
        {t("onboarding.terms.continue")}
      </Button>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- TermsStep`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/TermsStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/TermsStep.test.tsx
git commit -m "feat(onboarding): TermsStep acceptance gate (checkbox-gated, records version)"
```

---

### Task 12: LanguageStep

**Files:**
- Modify/Create: `src/components/onboarding/steps/LanguageStep.tsx`
- Test: `src/components/onboarding/steps/LanguageStep.test.tsx` (create)

**Interfaces:** Consumes `StepProps`, `@/i18n` (`useUiLanguage`, `setUiLanguage`, `useReplyLanguage`, `setReplyLanguage`). Two selects (UI language en/de/es; reply language auto/en/de/es). A `goNext` button. Reuses the existing live-applying setters (they push to the backend).

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/steps/LanguageStep.test.tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
const setUiLanguage = vi.fn();
vi.mock("@/i18n", () => ({
  useT: () => (k: string) => k,
  useUiLanguage: () => "en",
  useReplyLanguage: () => "auto",
  setUiLanguage,
  setReplyLanguage: vi.fn(),
}));
import { LanguageStep } from "./LanguageStep";
afterEach(() => { cleanup(); setUiLanguage.mockClear(); });

it("changes UI language and advances", () => {
  const goNext = vi.fn();
  render(<LanguageStep onb={{} as never} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  fireEvent.change(screen.getByLabelText("onboarding.language.ui_label"), { target: { value: "de" } });
  expect(setUiLanguage).toHaveBeenCalledWith("de");
  fireEvent.click(screen.getByRole("button", { name: "onboarding.nav.next" }));
  expect(goNext).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- LanguageStep`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/steps/LanguageStep.tsx
import { Button } from "@/components/ui/button";
import {
  useT, useUiLanguage, setUiLanguage, useReplyLanguage, setReplyLanguage,
  type UiLanguage, type ReplyLanguage,
} from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function LanguageStep({ goNext }: StepProps) {
  const t = useT();
  const ui = useUiLanguage();
  const reply = useReplyLanguage();
  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.language.title")}</h2>
      <label className="text-sm">{t("onboarding.language.ui_label")}
        <select aria-label={t("onboarding.language.ui_label")} value={ui}
          onChange={(e) => setUiLanguage(e.target.value as UiLanguage)}
          className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm">
          <option value="en">English</option><option value="de">Deutsch</option><option value="es">Español</option>
        </select>
      </label>
      <label className="text-sm">{t("onboarding.language.reply_label")}
        <select aria-label={t("onboarding.language.reply_label")} value={reply}
          onChange={(e) => setReplyLanguage(e.target.value as ReplyLanguage)}
          className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm">
          <option value="auto">Auto</option><option value="en">English</option>
          <option value="de">Deutsch</option><option value="es">Español</option>
        </select>
      </label>
      <Button className="w-full" onClick={goNext}>{t("onboarding.nav.next")}</Button>
    </div>
  );
}
```

(`UiLanguage` and `ReplyLanguage` are already exported types in `@/i18n`.)

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- LanguageStep`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/LanguageStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/LanguageStep.test.tsx
git commit -m "feat(onboarding): LanguageStep (UI + reply language)"
```

---

### Task 13: WakeWordStep (Hey ▢ composer + references + acknowledgment)

**Files:**
- Modify/Create: `src/components/onboarding/steps/WakeWordStep.tsx`
- Test: `src/components/onboarding/steps/WakeWordStep.test.tsx` (create)

**Interfaces:** Consumes `StepProps`, `useWakeWord` (existing — `saveWakeWord`). Renders a fixed "Hey" prefix + a word input, the trademark notice, the `onb.state.legal_references` as links, the freshness caveat, and a **required acknowledgment checkbox**. The save button is disabled until both a non-empty word AND the checkbox are present. On save: `await onb.acknowledgeWakeWord()`, then `saveWakeWord({ phrase: "Hey " + word, engine: "auto", persist: true })`, then `goNext()`. No skip.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/steps/WakeWordStep.test.tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
const saveWakeWord = vi.fn().mockResolvedValue({ ok: true });
vi.mock("@/hooks/useWakeWord", () => ({ useWakeWord: () => ({ saveWakeWord }) }));
import { WakeWordStep } from "./WakeWordStep";
afterEach(() => { cleanup(); saveWakeWord.mockClear(); });

const onb = {
  state: { legal_references: [{ label: "EUIPO", url: "https://euipo.europa.eu/eSearch/" }] },
  acknowledgeWakeWord: vi.fn().mockResolvedValue(undefined),
} as never;

it("requires word + acknowledgment, then saves 'Hey <word>'", async () => {
  const goNext = vi.fn();
  render(<WakeWordStep onb={onb} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);

  // Reference link is shown.
  expect(screen.getByRole("link", { name: "EUIPO" })).toBeDefined();

  const cta = screen.getByRole("button", { name: "onboarding.wake_word.cta" });
  expect((cta as HTMLButtonElement).disabled).toBe(true);

  fireEvent.change(screen.getByRole("textbox"), { target: { value: "Nova" } });
  expect((cta as HTMLButtonElement).disabled).toBe(true); // checkbox still unticked
  fireEvent.click(screen.getByRole("checkbox"));
  expect((cta as HTMLButtonElement).disabled).toBe(false);

  fireEvent.click(cta);
  await waitFor(() => expect(saveWakeWord).toHaveBeenCalled());
  expect(saveWakeWord.mock.calls[0][0].phrase).toBe("Hey Nova");
  expect((onb as never as { acknowledgeWakeWord: ReturnType<typeof vi.fn> }).acknowledgeWakeWord).toHaveBeenCalled();
  expect(goNext).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- WakeWordStep`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/steps/WakeWordStep.tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useWakeWord } from "@/hooks/useWakeWord";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function WakeWordStep({ onb, goNext }: StepProps) {
  const t = useT();
  const { saveWakeWord } = useWakeWord();
  const [word, setWord] = useState("");
  const [ack, setAck] = useState(false);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const trimmed = word.trim();
  const canSave = trimmed.length >= 2 && ack && !busy;
  const refs = onb.state?.legal_references ?? [];

  async function onSave() {
    if (!canSave) return;
    setBusy(true);
    setErr(null);
    try {
      await onb.acknowledgeWakeWord();
      await saveWakeWord({ phrase: `Hey ${trimmed}`, engine: "auto", persist: true });
      goNext();
    } catch (e) {
      setErr((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.wake_word.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.wake_word.body")}</p>

      <div className="flex items-center gap-2">
        <span className="rounded-md bg-muted px-3 py-2 text-sm font-medium">{t("onboarding.wake_word.prefix")}</span>
        <input
          aria-label={t("onboarding.wake_word.input_label")}
          type="text" value={word} maxLength={60} autoFocus
          onChange={(e) => setWord(e.target.value)}
          placeholder={t("onboarding.wake_word.placeholder")}
          className="w-full rounded-md border border-input bg-background px-3 py-2 text-sm focus:outline-none focus:ring-1 focus:ring-primary"
        />
      </div>

      <p className="text-xs text-muted-foreground">{t("onboarding.wake_word.notice")}</p>
      <div className="text-xs">
        <div className="font-medium">{t("onboarding.wake_word.references_title")}</div>
        <ul className="mt-1 list-disc pl-4">
          {refs.map((r) => (
            <li key={r.url}>
              <a href={r.url} target="_blank" rel="noreferrer" className="text-primary underline">{r.label}</a>
            </li>
          ))}
        </ul>
        <p className="mt-1 text-muted-foreground">{t("onboarding.wake_word.references_caveat")}</p>
      </div>

      <label className="flex items-start gap-2 text-sm">
        <input type="checkbox" checked={ack} onChange={(e) => setAck(e.target.checked)} className="mt-1" />
        {t("onboarding.wake_word.ack_label")}
      </label>

      {err && <p className="text-xs text-amber-500">{err}</p>}

      <Button className="w-full" disabled={!canSave} onClick={onSave}>
        {busy ? t("onboarding.wake_word.saving") : t("onboarding.wake_word.cta")}
      </Button>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- WakeWordStep`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/WakeWordStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/WakeWordStep.test.tsx
git commit -m "feat(onboarding): WakeWordStep (Hey-composer + refs + responsibility ack)"
```

---

### Task 14: ApiKeysStep (skippable, "what works now" line)

**Files:**
- Modify/Create: `src/components/onboarding/steps/ApiKeysStep.tsx`
- Test: `src/components/onboarding/steps/ApiKeysStep.test.tsx` (create)

**Interfaces:** Consumes `StepProps`. Lists a small set of provider-class key fields (Brain/STT/TTS), each POSTing to `/api/secrets/{key}` on blur (masked input), a static "what works now" line (`onboarding.api_keys.works_now`), a `goNext` button, and a `skip` link. Keys never echoed; values masked via `type="password"`.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/steps/ApiKeysStep.test.tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { ApiKeysStep } from "./ApiKeysStep";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("saves a key on blur and can skip", async () => {
  const calls: string[] = [];
  vi.stubGlobal("fetch", vi.fn().mockImplementation((u: string) => {
    calls.push(u);
    return Promise.resolve({ ok: true, json: () => Promise.resolve({ ok: true }) });
  }));
  const skip = vi.fn();
  render(<ApiKeysStep onb={{} as never} goNext={vi.fn()} goBack={vi.fn()} skip={skip} isFirst={false} isLast={false} />);

  const input = screen.getByLabelText("gemini_api_key");
  fireEvent.change(input, { target: { value: "AIzaTEST" } });
  fireEvent.blur(input);
  await waitFor(() => expect(calls.some((u) => u.includes("/api/secrets/gemini_api_key"))).toBe(true));

  fireEvent.click(screen.getByRole("button", { name: "onboarding.api_keys.skip" }));
  expect(skip).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- ApiKeysStep`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/steps/ApiKeysStep.tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

const KEYS: { key: string; labelKey: string }[] = [
  { key: "gemini_api_key", labelKey: "Gemini (Brain)" },
  { key: "openai_api_key", labelKey: "OpenAI (Brain/STT)" },
  { key: "elevenlabs_api_key", labelKey: "ElevenLabs (TTS)" },
];

async function saveSecret(key: string, value: string) {
  if (!value.trim()) return;
  await fetch(`/api/secrets/${key}`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ value }),
  }).catch(() => undefined);
}

export function ApiKeysStep({ goNext, skip }: StepProps) {
  const t = useT();
  const [vals, setVals] = useState<Record<string, string>>({});
  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.api_keys.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.api_keys.body")}</p>
      <p className="rounded-md bg-primary/10 px-3 py-2 text-xs text-primary">{t("onboarding.api_keys.works_now")}</p>
      {KEYS.map(({ key, labelKey }) => (
        <label key={key} className="text-xs font-medium text-muted-foreground">
          {labelKey}
          <input
            aria-label={key}
            type="password"
            value={vals[key] ?? ""}
            onChange={(e) => setVals((v) => ({ ...v, [key]: e.target.value }))}
            onBlur={() => void saveSecret(key, vals[key] ?? "")}
            className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
          />
        </label>
      ))}
      <Button className="w-full" onClick={goNext}>{t("onboarding.nav.next")}</Button>
      <button className="text-xs text-muted-foreground underline" onClick={skip}>
        {t("onboarding.api_keys.skip")}
      </button>
    </div>
  );
}
```

> Confirm the secret-save endpoint shape against `jarvis/ui/web/provider_routes.py` (`POST /api/secrets/{key}`); adjust the body field name if it differs from `{value}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- ApiKeysStep`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/ApiKeysStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/ApiKeysStep.test.tsx
git commit -m "feat(onboarding): ApiKeysStep (masked keys, skippable, works-now hint)"
```

---

### Task 15: MicTestStep (skippable, graceful no-mic)

**Files:**
- Modify/Create: `src/components/onboarding/steps/MicTestStep.tsx`
- Test: `src/components/onboarding/steps/MicTestStep.test.tsx` (create)

**Interfaces:** Consumes `StepProps`. On mount, probes `navigator.mediaDevices.getUserMedia({audio:true})`. On success: show "mic detected" + a `goNext`. On failure/absent: show `onboarding.mic_test.no_mic` and still allow `skip`/`goNext`. Never blocks.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/steps/MicTestStep.test.tsx
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { MicTestStep } from "./MicTestStep";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("shows the no-mic message when getUserMedia is unavailable", async () => {
  vi.stubGlobal("navigator", { mediaDevices: undefined });
  render(<MicTestStep onb={{} as never} goNext={vi.fn()} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  await waitFor(() => expect(screen.getByText("onboarding.mic_test.no_mic")).toBeDefined());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- MicTestStep`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/steps/MicTestStep.tsx
import { useEffect, useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

type MicState = "checking" | "ok" | "no-mic";

export function MicTestStep({ goNext, skip }: StepProps) {
  const t = useT();
  const [mic, setMic] = useState<MicState>("checking");

  useEffect(() => {
    let cancelled = false;
    const md = (navigator as Navigator).mediaDevices;
    if (!md || typeof md.getUserMedia !== "function") {
      setMic("no-mic");
      return;
    }
    md.getUserMedia({ audio: true })
      .then((stream) => {
        if (cancelled) return;
        stream.getTracks().forEach((tr) => tr.stop());
        setMic("ok");
      })
      .catch(() => !cancelled && setMic("no-mic"));
    return () => { cancelled = true; };
  }, []);

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.mic_test.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.mic_test.body")}</p>
      {mic === "no-mic" && <p className="text-xs text-amber-500">{t("onboarding.mic_test.no_mic")}</p>}
      <Button className="w-full" onClick={goNext}>{t("onboarding.nav.next")}</Button>
      <button className="text-xs text-muted-foreground underline" onClick={skip}>{t("onboarding.mic_test.skip")}</button>
    </div>
  );
}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- MicTestStep`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/MicTestStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/MicTestStep.test.tsx
git commit -m "feat(onboarding): MicTestStep with graceful no-mic fallback"
```

---

### Task 16: PersonaThemeStep (skippable)

**Files:**
- Modify/Create: `src/components/onboarding/steps/PersonaThemeStep.tsx`
- Test: `src/components/onboarding/steps/PersonaThemeStep.test.tsx` (create)

**Interfaces:** Consumes `StepProps`. A name input that PUTs `/api/settings/assistant-name` on blur, a `goNext`, and a `skip`. (Theme picker can reuse the existing overlay-style setting later; keep this step minimal — name only — to ship.)

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/steps/PersonaThemeStep.test.tsx
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { PersonaThemeStep } from "./PersonaThemeStep";
afterEach(() => { cleanup(); vi.restoreAllMocks(); });

it("saves the assistant name on blur", async () => {
  const calls: Array<[string, RequestInit | undefined]> = [];
  vi.stubGlobal("fetch", vi.fn().mockImplementation((u: string, i?: RequestInit) => {
    calls.push([u, i]); return Promise.resolve({ ok: true, json: () => Promise.resolve({}) });
  }));
  render(<PersonaThemeStep onb={{} as never} goNext={vi.fn()} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast={false} />);
  const input = screen.getByLabelText("onboarding.persona.name_label");
  fireEvent.change(input, { target: { value: "Nova" } });
  fireEvent.blur(input);
  await waitFor(() => expect(calls.some(([u, i]) => u === "/api/settings/assistant-name" && i?.method === "PUT")).toBe(true));
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- PersonaThemeStep`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/steps/PersonaThemeStep.tsx
import { useState } from "react";
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function PersonaThemeStep({ goNext, skip }: StepProps) {
  const t = useT();
  const [name, setName] = useState("");

  function saveName() {
    if (!name.trim()) return;
    void fetch("/api/settings/assistant-name", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name: name.trim() }),
    }).catch(() => undefined);
  }

  return (
    <div className="flex flex-col gap-4">
      <h2 className="font-display text-lg font-semibold">{t("onboarding.persona.title")}</h2>
      <label className="text-xs font-medium text-muted-foreground">
        {t("onboarding.persona.name_label")}
        <input
          aria-label={t("onboarding.persona.name_label")}
          type="text" value={name} maxLength={40}
          onChange={(e) => setName(e.target.value)} onBlur={saveName}
          className="mt-1 w-full rounded-md border border-input bg-background px-3 py-2 text-sm"
        />
      </label>
      <Button className="w-full" onClick={goNext}>{t("onboarding.nav.next")}</Button>
      <button className="text-xs text-muted-foreground underline" onClick={skip}>{t("onboarding.persona.skip")}</button>
    </div>
  );
}
```

> Confirm `PUT /api/settings/assistant-name` body shape against `settings_routes.py:569`; adjust the field name if it differs from `{name}`.

- [ ] **Step 4: Run test to verify it passes**

Run: `cd jarvis/ui/web/frontend && npm run test -- PersonaThemeStep`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/PersonaThemeStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/PersonaThemeStep.test.tsx
git commit -m "feat(onboarding): PersonaThemeStep (assistant name, skippable)"
```

---

### Task 17: FinishStep + final integration check

**Files:**
- Modify/Create: `src/components/onboarding/steps/FinishStep.tsx`
- Test: `src/components/onboarding/steps/FinishStep.test.tsx` (create)

**Interfaces:** Consumes `StepProps`. Shows a summary + example commands; the primary button calls `goNext` (which, being last, triggers `onb.complete()` in the flow). Lists `onb.state.skipped_steps` under `onboarding.finish.skipped_title` when non-empty.

- [ ] **Step 1: Write the failing test**

```tsx
// src/components/onboarding/steps/FinishStep.test.tsx
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
vi.mock("@/i18n", () => ({ useT: () => (k: string) => k }));
import { FinishStep } from "./FinishStep";
afterEach(cleanup);

it("calls goNext (=complete) on the start CTA", () => {
  const goNext = vi.fn();
  render(<FinishStep onb={{ state: { skipped_steps: [] } } as never} goNext={goNext} goBack={vi.fn()} skip={vi.fn()} isFirst={false} isLast />);
  fireEvent.click(screen.getByRole("button", { name: "onboarding.finish.start_cta" }));
  expect(goNext).toHaveBeenCalled();
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- FinishStep`
Expected: FAIL.

- [ ] **Step 3: Write minimal implementation**

```tsx
// src/components/onboarding/steps/FinishStep.tsx
import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import type { StepProps } from "../OnboardingFlow";

export function FinishStep({ onb, goNext }: StepProps) {
  const t = useT();
  const skipped = onb.state?.skipped_steps ?? [];
  return (
    <div className="flex flex-col gap-4 text-center">
      <h2 className="font-display text-xl font-semibold">{t("onboarding.finish.title")}</h2>
      <p className="text-sm text-muted-foreground">{t("onboarding.finish.body")}</p>
      {skipped.length > 0 && (
        <div className="text-xs text-muted-foreground">
          <div className="font-medium">{t("onboarding.finish.skipped_title")}</div>
          <ul className="mt-1">{skipped.map((s) => <li key={s}>{s}</li>)}</ul>
        </div>
      )}
      <Button className="w-full" onClick={goNext}>{t("onboarding.finish.start_cta")}</Button>
    </div>
  );
}
```

- [ ] **Step 4: Run the full frontend suite + typecheck + build**

Run: `cd jarvis/ui/web/frontend && npm run test && npx tsc --noEmit && npm run build`
Expected: all green (every step component now exists, the flow registry resolves, the gate mounts).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/FinishStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/FinishStep.test.tsx
git commit -m "feat(onboarding): FinishStep + flow integration green"
```

---

## Final verification (after Task 17)

- [ ] **Backend:** `pytest tests/unit/setup/ tests/unit/ui/test_onboarding_routes.py -v` → all green.
- [ ] **Frontend:** `cd jarvis/ui/web/frontend && npm run test` → all green; `npx tsc --noEmit` clean; `npm run build` succeeds.
- [ ] **Lint:** `ruff check jarvis/setup/ jarvis/ui/web/onboarding_routes.py jarvis/__main__.py` clean.
- [ ] **Manual fresh-run:** `python -m jarvis --reset-onboarding`, restart the desktop app (`POST /api/settings/restart-app`), confirm the guide appears; or for a non-destructive UI loop run the dev server with `JARVIS_FORCE_ONBOARDING=1` (backend) / append `?onboarding=force` once Task supports it.
- [ ] **Editable install note:** after backend changes, `pip install -e . --no-deps` is NOT required (pure-Python edits load live), but the app must be restarted via `POST /api/settings/restart-app` to pick them up.

> **`?onboarding=force` (dev convenience):** the spec lists a query-param force for the fastest UI loop. If you want it, add to `OnboardingGate`: `const forced = new URLSearchParams(window.location.search).get("onboarding") === "force";` and treat `forced` like `!completed`. This is optional polish, not required for the flow to work — `JARVIS_FORCE_ONBOARDING=1` already covers the backend path.

---

## Self-Review notes (author)

- **Spec coverage:** welcome+clip (T9/T10), terms gate first-after-welcome (T11 + ordering in T8 registry/ONBOARDING_STEPS), language (T12), wake-word free-choice + refs + ack, no denylist (T13 + T2 refs + T3 no rejection), API keys skippable (T14), mic test graceful (T15), persona/name (T16), finish (T17), data model + migration + force (T1/T3), reset CLI (T4), i18n en/de/es (T5), fail-open gate (T7). All spec sections map to a task.
- **No placeholders:** every code step shows real code; the only "confirm endpoint shape" notes (T14 secret body, T16 name body) are explicit verification asks against named files, not missing logic.
- **Type consistency:** `StepProps` (T8) is consumed identically by T10–T17; `OnboardingState` (T6) matches the route payload (T3); step keys match `ONBOARDING_STEPS` (T2) and the `REGISTRY` (T8).
