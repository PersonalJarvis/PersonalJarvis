# Install & First-Run Onboarding Redesign — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The install one-liner downloads *everything* silently (deps, voice
models, worker CLI, shortcuts) and launches the app last; the first launch
reliably shows the desktop onboarding once; updates never re-trigger it.

**Architecture:** Remove the wizard from the install path and re-arm the
existing desktop onboarding by never writing completion markers at install
time. Serve the onboarding API from the serve-first bootstrap (stdlib-only
fast path) so the gate renders during warmup. Add a shared `--prefetch` CLI
the installer calls to download voice models. Persist Terms from the RiskGate
and add the autostart toggle to FinishStep.

**Tech Stack:** Python 3.11 (FastAPI, raw ASGI, Rich), React/TS (vitest),
GitHub Actions.

**Spec:** `docs/superpowers/specs/2026-07-06-onboarding-install-redesign-design.md`

**Spec deviation (documented per plan-wins rule):** Spec §E item 3 (CI asserts
the served `index.html` matches a freshly built bundle) is replaced by an
installer-side UI-bundle integrity check (Task 8) + a smoke assertion on its
output. CI clones the dev repo, which ships no `dist/`, so a hash comparison
is impossible there; the integrity check catches the same failure class
(missing/stale assets) on every real install instead.

## Global Constraints

- English-only artifacts — all code, comments, log messages, docs (CLAUDE.md §1).
- New user-facing frontend strings need i18n keys in **all three** locales
  (`en.json`, `de.json`, `es.json`).
- Base install stays torch-free/universal; `faster-whisper` is optional —
  prefetch must degrade to a no-op without it (CLAUDE.md §3, AP-23).
- Nothing new on the boot critical path (AP-26); fast-bootstrap stays
  dependency-light (stdlib-only imports in the fast path).
- Subprocesses pass `NO_WINDOW_CREATIONFLAGS` where spawned from app code (AP-1).
- Shared working tree: stage only your own files by explicit path (never
  `git add -A`). Conventional commits.
- Tests: pytest `asyncio_mode=auto`, fakes over `unittest.mock` where feasible;
  frontend: vitest via `npm run test` in `jarvis/ui/web/frontend/`.

---

### Task 1: Single-source setup-complete marker + English marker content

**Files:**
- Modify: `jarvis/setup/state.py` (add marker helpers)
- Modify: `jarvis/core/config.py:2634-2645` (delegate + English text)
- Test: `tests/unit/setup/test_setup_marker.py` (new)

**Interfaces:**
- Produces: `state.setup_complete_marker_path(path: Path | None = None) -> Path`,
  `state.setup_complete_marker_exists(path: Path | None = None) -> bool`
  (`path` overrides the *state file* path; the marker lives in its parent dir).
  Consumed by Task 2 (fast path) and existing `config.is_first_run`.

- [ ] **Step 1: Write the failing test**

```python
"""Tests for the single-source .setup-complete marker helpers."""
from pathlib import Path

from jarvis.setup import state as st


def test_marker_path_is_sibling_of_state_file(tmp_path: Path) -> None:
    state_file = tmp_path / "setup_state.json"
    assert st.setup_complete_marker_path(state_file) == tmp_path / ".setup-complete"


def test_marker_exists_roundtrip(tmp_path: Path) -> None:
    state_file = tmp_path / "setup_state.json"
    assert st.setup_complete_marker_exists(state_file) is False
    (tmp_path / ".setup-complete").write_text("done\n", encoding="utf-8")
    assert st.setup_complete_marker_exists(state_file) is True


def test_default_marker_matches_config_data_dir() -> None:
    from jarvis.core.config import DATA_DIR

    assert st.setup_complete_marker_path() == DATA_DIR / ".setup-complete"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/setup/test_setup_marker.py -v`
Expected: FAIL — `AttributeError: module ... has no attribute 'setup_complete_marker_path'`

- [ ] **Step 3: Implement in `jarvis/setup/state.py`** (after `state_path`, before `load_setup_state`; extend `__all__`)

```python
def setup_complete_marker_path(path: Path | None = None) -> Path:
    """Location of the legacy ``.setup-complete`` marker.

    Lives next to the setup-state file (the ``data/`` dir) so the fast-boot
    onboarding path and ``jarvis.core.config.is_first_run`` agree on ONE file
    without importing the heavy config module. ``path`` is the state-file
    override used by tests.
    """
    return state_path(path).parent / ".setup-complete"


def setup_complete_marker_exists(path: Path | None = None) -> bool:
    """True when the legacy wizard/setup completion marker is present."""
    return setup_complete_marker_path(path).exists()
```

- [ ] **Step 4: Delegate in `jarvis/core/config.py` and fix the German string**

Replace `is_first_run` / `mark_setup_complete` bodies:

```python
def is_first_run() -> bool:
    """Return True when the user has not yet completed the setup wizard."""
    from jarvis.setup.state import setup_complete_marker_exists

    return not setup_complete_marker_exists()


def mark_setup_complete() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    (DATA_DIR / ".setup-complete").write_text(
        f"Setup completed on Python {sys.version.split()[0]}\n",
        encoding="utf-8",
    )
```

(`jarvis.setup.state` imports only stdlib, so the local import adds no weight;
it stays function-local to keep config's import graph unchanged.)

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/unit/setup/ -v`
Expected: PASS (including existing `test_onboarding_meta.py`)

- [ ] **Step 6: Commit**

```bash
git add jarvis/setup/state.py jarvis/core/config.py tests/unit/setup/test_setup_marker.py
git commit -m "refactor(setup): single-source .setup-complete marker helpers"
```

---

### Task 2: Stdlib-only onboarding fast-path ASGI handler

**Files:**
- Create: `jarvis/setup/onboarding_fastpath.py`
- Modify: `jarvis/ui/web/onboarding_routes.py` (reuse the shared payload builder)
- Test: `tests/unit/setup/test_onboarding_fastpath.py` (new)

**Interfaces:**
- Produces: `state_payload(path: Path | None = None) -> dict` (same shape as
  today's `GET /api/onboarding/state`), and
  `async handle(scope: dict, receive, send) -> bool` — returns True iff the
  request was an onboarding route and a response was sent. Consumed by Task 3.
- Consumes: Task 1 helpers; `jarvis.setup.onboarding_meta` constants
  (verify that module imports only stdlib — it must stay that way).

- [ ] **Step 1: Write the failing tests** (drive `handle` directly as ASGI — collect sent messages)

```python
"""The fast-boot onboarding handler: stdlib-only, same payload as the API."""
import json
from pathlib import Path

from jarvis.setup import onboarding_fastpath as fp
from jarvis.setup import state as st


def _collector():
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    return sent, send


async def _receive_empty():
    return {"type": "http.request", "body": b"", "more_body": False}


def _scope(method: str, path: str) -> dict:
    return {"type": "http", "method": method, "path": path}


def _body_json(sent: list[dict]) -> dict:
    return json.loads(sent[1]["body"].decode("utf-8"))


async def test_state_incomplete_on_fresh_install(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        sent, send = _collector()
        handled = await fp.handle(_scope("GET", "/api/onboarding/state"), _receive_empty, send)
        assert handled is True
        assert sent[0]["status"] == 200
        payload = _body_json(sent)
        assert payload["completed"] is False
        assert payload["steps"]  # canonical step list present
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_state_completed_via_marker(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    (tmp_path / ".setup-complete").write_text("done\n", encoding="utf-8")
    try:
        sent, send = _collector()
        await fp.handle(_scope("GET", "/api/onboarding/state"), _receive_empty, send)
        assert _body_json(sent)["completed"] is True
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_accept_terms_persists(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        sent, send = _collector()
        handled = await fp.handle(
            _scope("POST", "/api/onboarding/accept-terms"), _receive_empty, send
        )
        assert handled is True and sent[0]["status"] == 200
        s = st.get_onboarding_state(tmp_path / "setup_state.json")
        assert s["terms_accepted_at"] is not None
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_complete_persists_and_next_state_is_completed(tmp_path: Path) -> None:
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        sent, send = _collector()
        await fp.handle(_scope("POST", "/api/onboarding/complete"), _receive_empty, send)
        sent2, send2 = _collector()
        await fp.handle(_scope("GET", "/api/onboarding/state"), _receive_empty, send2)
        assert _body_json(sent2)["completed"] is True
    finally:
        fp._STATE_PATH_OVERRIDE = None


async def test_non_onboarding_path_not_handled() -> None:
    sent, send = _collector()
    handled = await fp.handle(_scope("GET", "/api/health"), _receive_empty, send)
    assert handled is False and sent == []
```

Import-light guard (a fresh subprocess, so other tests' imports cannot
pollute the verdict — the fast path must never pull fastapi/pydantic/config,
AP-26):

```python
def test_fastpath_module_is_import_light() -> None:
    import subprocess
    import sys

    code = (
        "import sys; import jarvis.setup.onboarding_fastpath; "
        "banned = [m for m in ('fastapi', 'pydantic', 'jarvis.core.config') "
        "if m in sys.modules]; "
        "sys.exit(1 if banned else 0)"
    )
    rc = subprocess.run([sys.executable, "-c", code]).returncode
    assert rc == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/unit/setup/test_onboarding_fastpath.py -v`
Expected: FAIL — `ModuleNotFoundError: jarvis.setup.onboarding_fastpath`

- [ ] **Step 3: Implement `jarvis/setup/onboarding_fastpath.py`**

```python
"""Stdlib-only onboarding API for the serve-first fast-boot window.

The desktop onboarding gate must render from the FIRST second of a fresh
install, but the real FastAPI app (which mounts jarvis/ui/web/
onboarding_routes.py) only registers after the heavy warmup. This module
answers the same /api/onboarding/* surface as a raw ASGI handler with ZERO
heavy imports (no fastapi/pydantic/config), so jarvis.ui.web.fast_bootstrap
can delegate to it while warming (AP-26: nothing heavy on the boot path).

The real routes stay authoritative once the app is up — the bootstrap only
consults this handler before ``set_app``. Both layers share the payload
builder below and the jarvis.setup.state store, so they can never disagree.
"""
from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from jarvis.setup import state as st
from jarvis.setup.onboarding_meta import (
    CURRENT_TERMS_VERSION,
    ONBOARDING_STEPS,
    WAKE_WORD_LEGAL_REFERENCES,
    read_terms_text,
)

log = logging.getLogger(__name__)

# Tests override this to redirect the state file (mirrors onboarding_routes).
_STATE_PATH_OVERRIDE: Path | None = None

_PREFIX = "/api/onboarding"


def _path() -> Path | None:
    return _STATE_PATH_OVERRIDE


def state_payload(path: Path | None = None) -> dict:
    """Build the GET /state payload. Never raises — fails open to 'incomplete'.

    Single source of truth shared with jarvis.ui.web.onboarding_routes: the
    legacy ``.setup-complete`` marker is resolved via jarvis.setup.state so
    this stays importable without the heavy config module.
    """
    try:
        s = st.get_onboarding_state(path)
    except Exception as exc:  # noqa: BLE001 — UI must keep working
        log.warning("onboarding_get_state_failed: %s", exc, exc_info=True)
        s = {
            "completed_at": None, "current_step": None, "skipped_steps": [],
            "terms_accepted_at": None, "terms_version": None,
            "wake_word_acknowledged_at": None,
        }

    legacy_done = False
    try:
        legacy_done = st.setup_complete_marker_exists(path)
    except Exception as exc:  # noqa: BLE001
        log.debug("onboarding: marker probe failed: %s", exc)

    completed = (s["completed_at"] is not None) or legacy_done
    if os.environ.get("JARVIS_FORCE_ONBOARDING"):
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


async def _read_body(receive: Any) -> bytes:
    body = b""
    while True:
        message = await receive()
        body += message.get("body", b"")
        if not message.get("more_body"):
            return body


async def _send_json(send: Any, payload: dict, status: int = 200) -> None:
    body = json.dumps(payload).encode("utf-8")
    await send({
        "type": "http.response.start",
        "status": status,
        "headers": [
            (b"content-type", b"application/json"),
            (b"cache-control", b"no-store, max-age=0"),
        ],
    })
    await send({"type": "http.response.body", "body": body})


async def handle(scope: dict, receive: Any, send: Any) -> bool:
    """Answer an /api/onboarding/* request; True iff handled.

    Mirrors onboarding_routes.py: every write is fail-open (state helpers
    never raise) and unknown sub-paths return 404 so a typo is visible.
    """
    if scope.get("type") != "http":
        return False
    path = scope.get("path", "")
    if not path.startswith(_PREFIX):
        return False
    method, sub = scope.get("method", "GET"), path[len(_PREFIX):]

    if method == "GET" and sub == "/state":
        await _send_json(send, state_payload(_path()))
        return True
    if method == "GET" and sub == "/terms":
        await _send_json(send, {"version": CURRENT_TERMS_VERSION, "text": read_terms_text()})
        return True
    if method == "POST" and sub == "/step":
        raw = await _read_body(receive)
        try:
            data = json.loads(raw.decode("utf-8")) if raw else {}
        except (ValueError, UnicodeDecodeError):
            data = {}
        step = data.get("step")
        if isinstance(step, str) and step:
            skipped = data.get("skipped")
            st.set_onboarding_step(
                step,
                skipped=list(skipped) if isinstance(skipped, list) else None,
                path=_path(),
            )
            await _send_json(send, {"ok": True})
        else:
            await _send_json(send, {"ok": False, "error": "missing step"}, status=422)
        return True
    if method == "POST" and sub == "/accept-terms":
        st.accept_terms(CURRENT_TERMS_VERSION, path=_path())
        await _send_json(send, {"ok": True, "version": CURRENT_TERMS_VERSION})
        return True
    if method == "POST" and sub == "/acknowledge-wake-word":
        st.acknowledge_wake_word(_path())
        await _send_json(send, {"ok": True})
        return True
    if method == "POST" and sub == "/complete":
        st.mark_onboarding_complete(_path())
        await _send_json(send, {"ok": True})
        return True

    await _send_json(send, {"ok": False, "error": "unknown onboarding route"}, status=404)
    return True


__all__ = ["handle", "state_payload"]
```

- [ ] **Step 4: De-duplicate `onboarding_routes.py`** — replace its
`_safe_state_payload` body with a delegation (keep `_STATE_PATH_OVERRIDE`
behavior: pass `_path()` through), and route the legacy probe through the same
helper by deleting the local `is_first_run` import:

```python
from jarvis.setup.onboarding_fastpath import state_payload as _shared_state_payload


def _safe_state_payload() -> dict:
    return _shared_state_payload(_path())
```

Remove the now-unused imports (`os`, `is_first_run`, `_force_onboarding`,
and the meta constants that are no longer referenced directly — keep
`CURRENT_TERMS_VERSION` and `read_terms_text` for `/terms`, `accept-terms`).

- [ ] **Step 5: Run tests** — new file + existing route tests

Run: `python -m pytest tests/unit/setup/test_onboarding_fastpath.py tests/unit/ -k onboarding -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add jarvis/setup/onboarding_fastpath.py jarvis/ui/web/onboarding_routes.py tests/unit/setup/test_onboarding_fastpath.py
git commit -m "feat(onboarding): stdlib-only fast-path handler shared with the API routes"
```

---

### Task 3: Serve onboarding from the fast-boot bootstrap

**Files:**
- Modify: `jarvis/ui/web/fast_bootstrap.py:91-128` (warming branch)
- Test: `tests/unit/` — locate the existing fast-bootstrap test module via
  `rg "FastBootstrap" tests/` and add cases there (create
  `tests/unit/ui/test_fast_bootstrap_onboarding.py` if none fits).

**Interfaces:**
- Consumes: `jarvis.setup.onboarding_fastpath.handle` (Task 2).

- [ ] **Step 1: Write the failing test** (drive the bootstrap ASGI app directly, before `set_app`)

```python
"""While warming, the bootstrap must answer /api/onboarding/* itself."""
import json

from jarvis.ui.web.fast_bootstrap import FastBootstrap


def _collector():
    sent: list[dict] = []

    async def send(msg: dict) -> None:
        sent.append(msg)

    return sent, send


async def _receive_empty():
    return {"type": "http.request", "body": b"", "more_body": False}


async def test_onboarding_state_answers_while_warming(tmp_path, monkeypatch) -> None:
    from jarvis.setup import onboarding_fastpath as fp

    monkeypatch.setattr(fp, "_STATE_PATH_OVERRIDE", tmp_path / "setup_state.json")
    boot = FastBootstrap(dist_dir=tmp_path / "no-dist")
    sent, send = _collector()
    await boot.app(
        {"type": "http", "method": "GET", "path": "/api/onboarding/state"},
        _receive_empty,
        send,
    )
    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"])["completed"] is False


async def test_other_api_routes_still_held(tmp_path) -> None:
    boot = FastBootstrap(hold_timeout=0.05, dist_dir=tmp_path / "no-dist")
    sent, send = _collector()
    await boot.app(
        {"type": "http", "method": "GET", "path": "/api/settings"},
        _receive_empty,
        send,
    )
    assert sent[0]["status"] == 503  # warming hold unchanged
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/unit/ui/test_fast_bootstrap_onboarding.py -v`
Expected: first test FAILS (503 instead of 200), second PASSES.

- [ ] **Step 3: Implement** — in `_asgi`, inside the warming section, directly
after the `/api/health` fast answer (before the websocket branch):

```python
        # First-run onboarding must render from the first second — the gate's
        # state/terms/step/complete calls are answered here (stdlib-only
        # handler, shared with the real routes) instead of being held. Once
        # set_app runs, the delegation branch above owns these paths again.
        if kind == "http" and scope.get("path", "").startswith("/api/onboarding"):
            from jarvis.setup.onboarding_fastpath import handle as _onboarding_handle

            if await _onboarding_handle(scope, receive, send):
                return
```

(Lazy function-level import keeps the module dependency-light; the module it
imports is stdlib-only by Task 2's import-light test.)

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/unit/ui/test_fast_bootstrap_onboarding.py -v` then
`python -m pytest tests/unit -k "fast_bootstrap or bootstrap" -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/fast_bootstrap.py tests/unit/ui/test_fast_bootstrap_onboarding.py
git commit -m "feat(boot): answer onboarding API during warmup so the gate shows on first launch"
```

---

### Task 4: Frontend — onboarding fetch retries while the backend warms

**Files:**
- Modify: `jarvis/ui/web/frontend/src/hooks/useOnboarding.ts`
- Test: `jarvis/ui/web/frontend/src/hooks/useOnboarding.test.ts` (new; follow
  the existing vitest setup used by `OnboardingGate.test.tsx`)

**Interfaces:**
- Produces: unchanged hook API; `refetch` now retries transient failures.

- [ ] **Step 1: Write the failing test**

```ts
import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useOnboarding } from "./useOnboarding";

describe("useOnboarding warmup retry", () => {
  beforeEach(() => vi.useFakeTimers({ shouldAdvanceTime: true }));
  afterEach(() => {
    vi.useRealTimers();
    vi.restoreAllMocks();
  });

  it("retries a 503 until the backend answers, then resolves state", async () => {
    const payload = {
      completed: false, current_step: null, skipped_steps: [],
      terms: { accepted: false, accepted_version: null, current_version: "1.0" },
      wake_word_acknowledged: false, legal_references: [], steps: ["welcome"],
    };
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(new Response("warming", { status: 503 }))
      .mockResolvedValueOnce(new Response("warming", { status: 503 }))
      .mockResolvedValue(
        new Response(JSON.stringify(payload), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
      );
    vi.stubGlobal("fetch", fetchMock);

    const { result } = renderHook(() => useOnboarding());
    await waitFor(() => expect(result.current.state?.completed).toBe(false), {
      timeout: 15000,
    });
    expect(result.current.error).toBeNull();
    expect(fetchMock.mock.calls.length).toBeGreaterThanOrEqual(3);
  });

  it("gives up after the bounded retry window (fail-open preserved)", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn().mockResolvedValue(new Response("warming", { status: 503 })),
    );
    const { result } = renderHook(() => useOnboarding());
    await waitFor(() => expect(result.current.error).not.toBeNull(), {
      timeout: 60000,
    });
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run (in `jarvis/ui/web/frontend/`): `npm run test -- useOnboarding`
Expected: FAIL (first test: `error` set after single 503, state never resolves)

- [ ] **Step 3: Implement retry in `refetch`** (replace the existing callback;
keep everything else untouched):

```ts
// Bounded warmup retry: on a fresh machine the serve-first backend answers
// /api/onboarding/state from the bootstrap immediately, but a slow disk or a
// dev server without the fast path can still 503 briefly. Retrying keeps the
// first-run gate from failing open (= never showing) on the one boot where it
// matters most. ~30 s total, then fail-open as before (never trap the user).
const RETRY_DELAYS_MS = [500, 1000, 1500, 2000, 3000, 3000, 4000, 5000, 5000, 5000];

const refetch = useCallback(async () => {
  setError(null);
  for (let attempt = 0; ; attempt++) {
    try {
      const res = await fetch("/api/onboarding/state");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      setState((await res.json()) as OnboardingState);
      setLoading(false);
      return;
    } catch (e) {
      const delay = RETRY_DELAYS_MS[attempt];
      if (delay === undefined) {
        setError((e as Error).message);
        setLoading(false);
        return;
      }
      await new Promise((r) => setTimeout(r, delay));
    }
  }
}, []);
```

- [ ] **Step 4: Run tests**

Run: `npm run test -- useOnboarding` then `npm run test -- Onboarding`
Expected: PASS (gate tests unaffected)

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/hooks/useOnboarding.ts jarvis/ui/web/frontend/src/hooks/useOnboarding.test.ts
git commit -m "fix(onboarding): retry state fetch during backend warmup instead of failing open"
```

---

### Task 4b: Global "Jarvis is starting…" banner while the backend warms (spec B3)

**Files:**
- Create: `jarvis/ui/web/frontend/src/hooks/useBackendWarming.ts`
- Modify: `jarvis/ui/web/frontend/src/App.tsx` (mount the banner)
- Modify: i18n locales (3 files)
- Test: `jarvis/ui/web/frontend/src/hooks/useBackendWarming.test.ts` (new)

**Interfaces:**
- Consumes: `GET /api/health` — the fast-boot bootstrap answers
  `{"ok": true, "warming": true}` while warming; the real app's health payload
  carries no `warming: true`.
- Produces: `useBackendWarming(): boolean` — true only while the bootstrap
  reports warming; polls every 2 s and stops permanently once warming ends.

- [ ] **Step 1: Write the failing test**

```ts
import { renderHook, waitFor } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import { useBackendWarming } from "./useBackendWarming";

it("reports warming until health stops saying so, then stops polling", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ ok: true, warming: true }), { status: 200 }))
    .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  const { result } = renderHook(() => useBackendWarming());
  await waitFor(() => expect(result.current).toBe(true));
  await waitFor(() => expect(result.current).toBe(false), { timeout: 10000 });
});

it("treats a failed health probe as not-warming (fail quiet)", async () => {
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(new Error("net")));
  const { result } = renderHook(() => useBackendWarming());
  await waitFor(() => expect(result.current).toBe(false));
});
```

- [ ] **Step 2: Run to verify failure** — `npm run test -- useBackendWarming`
→ FAIL (module missing).

- [ ] **Step 3: Implement the hook**

```ts
import { useEffect, useState } from "react";

/**
 * True while the serve-first bootstrap is still warming the real backend
 * (GET /api/health answers {warming: true} in that window — see
 * jarvis/ui/web/fast_bootstrap.py). Lets the shell show one honest global
 * "starting" banner instead of empty feature lists. Polls every 2 s and
 * stops for good once warming ends (the flag can never flip back on).
 */
export function useBackendWarming(): boolean {
  const [warming, setWarming] = useState(false);

  useEffect(() => {
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | undefined;

    const probe = async () => {
      try {
        const res = await fetch("/api/health");
        const body = res.ok ? ((await res.json()) as { warming?: boolean }) : {};
        if (cancelled) return;
        if (body.warming === true) {
          setWarming(true);
          timer = setTimeout(() => void probe(), 2000);
        } else {
          setWarming(false); // real app is up — stop polling permanently
        }
      } catch {
        if (!cancelled) setWarming(false); // fail quiet — never block the UI
      }
    };

    void probe();
    return () => {
      cancelled = true;
      if (timer !== undefined) clearTimeout(timer);
    };
  }, []);

  return warming;
}
```

- [ ] **Step 4: Mount in `App.tsx`** — inside the root layout, above the main
content (match the file's existing structure/classes):

```tsx
const warming = useBackendWarming();
```

```tsx
{warming && (
  <div className="fixed inset-x-0 top-0 z-40 bg-primary/90 px-4 py-1.5 text-center text-xs font-medium text-primary-foreground">
    {t("app.backend_warming")}
  </div>
)}
```

i18n key (3 locales): `"app.backend_warming": "Jarvis is starting — features appear as they come online…"`

- [ ] **Step 5: Run tests** — `npm run test -- useBackendWarming` + `npm run test -- App` → PASS

- [ ] **Step 6: Commit**

```bash
git add jarvis/ui/web/frontend/src/hooks/useBackendWarming.ts jarvis/ui/web/frontend/src/hooks/useBackendWarming.test.ts jarvis/ui/web/frontend/src/App.tsx jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(ui): honest global starting banner while the backend warms"
```

---

### Task 5: RiskGate persists Terms acceptance + terms text reachable

**Files:**
- Modify: `jarvis/ui/web/frontend/src/components/onboarding/RiskGate.tsx`
- Modify: `jarvis/ui/web/frontend/src/components/onboarding/OnboardingGate.tsx:59-60`
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/{en,de,es}.json`
- Test: extend `OnboardingGate.test.tsx`

**Interfaces:**
- Consumes: `onb.acceptTerms()` (existing hook), `GET /api/onboarding/terms`.
- Produces: RiskGate prop change: `onAccept: () => void` stays; new optional
  prop `onViewTermsFetch?: () => Promise<string>` is NOT added — the fetch is
  inline (YAGNI).

- [ ] **Step 1: Write the failing test** (in `OnboardingGate.test.tsx`, using
the file's existing mock helpers for `useOnboarding`)

```tsx
it("persists terms acceptance when the risk gate is accepted", async () => {
  const acceptTerms = vi.fn().mockResolvedValue(undefined);
  mockOnboarding({ completed: false }, { acceptTerms });
  render(<OnboardingGate />);
  await userEvent.click(await screen.findByRole("checkbox"));
  await userEvent.click(screen.getByRole("button", { name: /proceed|weiter|continuar/i })); // i18n-allow: localized proceed-button labels under test
  expect(acceptTerms).toHaveBeenCalledTimes(1);
});
```

(Adapt `mockOnboarding` to the file's actual helper — it already mocks the
hook for the gate tests; extend it to accept action overrides.)

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- OnboardingGate`
Expected: FAIL — `acceptTerms` never called.

- [ ] **Step 3: Implement**

In `OnboardingGate.tsx` (risk branch):

```tsx
<RiskGate
  onAccept={() => {
    // Persist the Terms record (fail-open: a warming/erroring backend must
    // never block the gate; the fast path usually answers immediately).
    void onb.acceptTerms().catch(() => undefined);
    setRiskAck(true);
  }}
/>
```

In `RiskGate.tsx`, add a collapsible full-terms section under the liability
paragraph (lazy fetch on first expand):

```tsx
const [terms, setTerms] = useState<string | null>(null);
const [showTerms, setShowTerms] = useState(false);

const toggleTerms = async () => {
  setShowTerms((v) => !v);
  if (terms === null) {
    try {
      const res = await fetch("/api/onboarding/terms");
      if (res.ok) setTerms(((await res.json()) as { text: string }).text);
      else setTerms("");
    } catch {
      setTerms("");
    }
  }
};
```

```tsx
<button type="button" className="self-start text-xs underline text-muted-foreground" onClick={() => void toggleTerms()}>
  {t("onboarding.risk.view_terms")}
</button>
{showTerms && (
  <pre className="max-h-40 overflow-y-auto whitespace-pre-wrap rounded-md border border-border bg-muted/30 p-3 text-xs text-muted-foreground">
    {terms ?? t("onboarding.risk.terms_loading")}
  </pre>
)}
```

i18n keys (all three locales; English source shown — translate de/es):

```json
"onboarding.risk.view_terms": "View the full Terms of Use",
"onboarding.risk.terms_loading": "Loading terms…"
```

- [ ] **Step 4: Run tests**

Run: `npm run test -- Onboarding` and the i18n parity test
(`npm run test -- i18n`)
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/RiskGate.tsx jarvis/ui/web/frontend/src/components/onboarding/OnboardingGate.tsx jarvis/ui/web/frontend/src/components/onboarding/OnboardingGate.test.tsx jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(onboarding): persist Terms acceptance from the risk gate + view full terms"
```

---

### Task 6: Autostart toggle in FinishStep

**Files:**
- Modify: `jarvis/ui/web/frontend/src/components/onboarding/steps/FinishStep.tsx`
- Modify: i18n locales (3 files)
- Test: `jarvis/ui/web/frontend/src/components/onboarding/steps/FinishStep.test.tsx` (new)

**Interfaces:**
- Consumes: existing REST surface in `jarvis/ui/web/settings_routes.py:850+` —
  verify exact paths with `rg "autostart" jarvis/ui/web/settings_routes.py`
  (GET returns `{enabled, supported}`-shaped state; PUT toggles). Use the real
  route paths found there.

- [ ] **Step 1: Write the failing test**

```tsx
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it, vi } from "vitest";
import { FinishStep } from "./FinishStep";

const stepProps = {
  onb: { state: { skipped_steps: [] } } as never,
  goNext: vi.fn(), goBack: vi.fn(), skip: vi.fn(), isFirst: false, isLast: true,
};

it("shows the autostart toggle when supported and PUTs on change", async () => {
  const fetchMock = vi.fn()
    .mockResolvedValueOnce(new Response(JSON.stringify({ enabled: true, supported: true }), { status: 200 }))
    .mockResolvedValue(new Response(JSON.stringify({ ok: true }), { status: 200 }));
  vi.stubGlobal("fetch", fetchMock);
  render(<FinishStep {...stepProps} />);
  const toggle = await screen.findByRole("checkbox");
  await userEvent.click(toggle);
  await waitFor(() =>
    expect(fetchMock.mock.calls.some(([, init]) => (init as RequestInit)?.method === "PUT")).toBe(true),
  );
});

it("hides the toggle when autostart is unsupported (headless)", async () => {
  vi.stubGlobal("fetch", vi.fn().mockResolvedValue(
    new Response(JSON.stringify({ enabled: false, supported: false }), { status: 200 }),
  ));
  render(<FinishStep {...stepProps} />);
  await waitFor(() => expect(screen.queryByRole("checkbox")).toBeNull());
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `npm run test -- FinishStep` — Expected: FAIL (no checkbox rendered).

- [ ] **Step 3: Implement** — add to `FinishStep` (adjust route paths/response
field names to what `settings_routes.py` actually serves):

```tsx
const [autostart, setAutostart] = useState<{ enabled: boolean; supported: boolean } | null>(null);

useEffect(() => {
  void (async () => {
    try {
      const res = await fetch("/api/settings/autostart");
      if (res.ok) setAutostart(await res.json());
    } catch {
      // capability probe is best-effort — hide the toggle on failure
    }
  })();
}, []);

const toggleAutostart = async (enabled: boolean) => {
  setAutostart((s) => (s ? { ...s, enabled } : s));
  try {
    await fetch("/api/settings/autostart", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ enabled }),
    });
  } catch {
    // keep the optimistic value; Settings remains the recovery path
  }
};
```

```tsx
{autostart?.supported && (
  <label className="flex items-center justify-between gap-2 rounded-lg border border-border px-3 py-2 text-sm">
    <span>{t("onboarding.finish.autostart_label")}</span>
    <input
      type="checkbox"
      checked={autostart.enabled}
      onChange={(e) => void toggleAutostart(e.target.checked)}
    />
  </label>
)}
```

i18n key (3 locales): `"onboarding.finish.autostart_label": "Start Jarvis automatically at login"`

- [ ] **Step 4: Run tests** — `npm run test -- FinishStep` + i18n parity → PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/frontend/src/components/onboarding/steps/FinishStep.tsx jarvis/ui/web/frontend/src/components/onboarding/steps/FinishStep.test.tsx jarvis/ui/web/frontend/src/i18n/locales/en.json jarvis/ui/web/frontend/src/i18n/locales/de.json jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(onboarding): autostart toggle on the finish step (capability-gated)"
```

---

### Task 7: `jarvis --prefetch` — shared voice-model prefetch

**Files:**
- Create: `jarvis/setup/prefetch.py`
- Modify: `jarvis/__main__.py` (new `--prefetch` flag + dispatch)
- Test: `tests/unit/setup/test_prefetch.py` (new)

**Interfaces:**
- Produces: `prefetch_all(echo: Callable[[str], None] = print) -> int`
  (0 = everything present/downloaded, 1 = something failed — callers keep
  going; the runtime lazy-download remains the safety net). Consumed by
  Task 8 (`installer.py`) and `python -m jarvis --prefetch`.
- Consumes: `jarvis.assets.bundled_wakeword_models()`,
  `jarvis.core.config.load_config()` (heavy import is fine here — this is a
  dedicated CLI, not the boot path).

- [ ] **Step 1: Write the failing tests** (inject fakes; no network in unit tests)

```python
"""prefetch_all: resolves the same models the runtime uses; degrades cleanly."""
from jarvis.setup import prefetch


def test_reports_bundled_wakeword(monkeypatch) -> None:
    lines: list[str] = []
    monkeypatch.setattr(prefetch, "_wakeword_bundle_present", lambda: True)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: False)
    rc = prefetch.prefetch_all(echo=lines.append)
    assert rc == 0
    assert any("wake-word models" in line for line in lines)
    assert any("skipped" in line.lower() for line in lines)  # whisper skipped


def test_downloads_wake_model_when_faster_whisper_present(monkeypatch) -> None:
    downloaded: list[str] = []
    monkeypatch.setattr(prefetch, "_wakeword_bundle_present", lambda: True)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(prefetch, "_download_whisper_model", downloaded.append)
    monkeypatch.setattr(
        prefetch, "_whisper_models_needed", lambda: ["base"]
    )
    rc = prefetch.prefetch_all(echo=lambda _line: None)
    assert rc == 0
    assert downloaded == ["base"]


def test_download_failure_is_nonfatal(monkeypatch) -> None:
    def _boom(_name: str) -> None:
        raise OSError("mirror down")

    monkeypatch.setattr(prefetch, "_wakeword_bundle_present", lambda: True)
    monkeypatch.setattr(prefetch, "_faster_whisper_available", lambda: True)
    monkeypatch.setattr(prefetch, "_download_whisper_model", _boom)
    monkeypatch.setattr(prefetch, "_whisper_models_needed", lambda: ["base"])
    lines: list[str] = []
    rc = prefetch.prefetch_all(echo=lines.append)
    assert rc == 1
    assert any("first launch" in line for line in lines)  # honest fallback note
```

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/unit/setup/test_prefetch.py -v` → `ModuleNotFoundError`

- [ ] **Step 3: Implement `jarvis/setup/prefetch.py`**

```python
"""Download-everything prefetch shared by the installer and the app.

Called by ``python -m jarvis --prefetch`` (which install/installer.py invokes)
so that when the install command finishes, the first launch has nothing left
to download. Resolution reuses the SAME config defaults the runtime reads, so
the installer and the app can never disagree about which models are needed.

Every step is best-effort: a failed download prints an honest note and the
runtime's lazy download remains the safety net — a flaky mirror must never
brick an install (CLAUDE.md §3). Works headless (no audio/GPU touched).
"""
from __future__ import annotations

import importlib.util
from collections.abc import Callable


def _wakeword_bundle_present() -> bool:
    """The always-on neural wake models ship in-repo (jarvis/assets/wakeword)."""
    try:
        import jarvis.assets

        return jarvis.assets.bundled_wakeword_models() is not None
    except Exception:  # noqa: BLE001 — a probe must never crash the prefetch
        return False


def _faster_whisper_available() -> bool:
    """True when the optional local-Whisper stack is installed."""
    return importlib.util.find_spec("faster_whisper") is not None


def _whisper_models_needed() -> list[str]:
    """The faster-whisper model names the CURRENT config would load at runtime.

    Mirrors jarvis/plugins/stt: the wake-match model always (stt.wake_model,
    default "base"); the utterance model only when the local provider is
    selected. Order = download order (small first).
    """
    from jarvis.core.config import load_config

    cfg = load_config()
    models = [cfg.stt.wake_model]
    if cfg.stt.provider == "faster-whisper" and cfg.stt.model not in models:
        models.append(cfg.stt.model)
    return models


def _download_whisper_model(name: str) -> None:
    """Fetch one faster-whisper model into the standard HuggingFace cache —
    the exact cache ``WhisperModel(name)`` resolves at runtime."""
    from faster_whisper.utils import download_model

    download_model(name)


def prefetch_all(echo: Callable[[str], None] = print) -> int:
    """Prefetch every artifact the default voice path needs. 0 = complete."""
    failed = False

    if _wakeword_bundle_present():
        echo("wake-word models: bundled with the app - nothing to download")
    else:
        echo("wake-word models: bundle missing - openWakeWord will auto-download on first use")

    if not _faster_whisper_available():
        echo("local Whisper models: skipped (faster-whisper not installed - cloud STT is the default)")
        return 0

    for name in _whisper_models_needed():
        echo(f"downloading speech model '{name}' (one-time, cached for every later start)")
        try:
            _download_whisper_model(name)
            echo(f"speech model '{name}': ready")
        except Exception as exc:  # noqa: BLE001 — honest note, never fatal
            failed = True
            echo(
                f"speech model '{name}' could not be downloaded ({exc}); "
                "it will download on first launch instead"
            )
    return 1 if failed else 0


__all__ = ["prefetch_all"]
```

- [ ] **Step 4: Wire `--prefetch` in `jarvis/__main__.py`** — add next to the
other flags (`--check` etc.):

```python
    parser.add_argument(
        "--prefetch",
        action="store_true",
        help="Download all voice models the current config needs, then exit. "
             "Used by the installer so the first launch has nothing left to fetch.",
    )
```

and in the dispatch section (before the wizard/app branch):

```python
    if args.prefetch:
        from jarvis.setup.prefetch import prefetch_all

        return prefetch_all()
```

- [ ] **Step 5: Run tests + a real smoke**

Run: `python -m pytest tests/unit/setup/test_prefetch.py -v` → PASS
Run: `python -m jarvis --prefetch` → prints the wake-bundle line; downloads
`base` if faster-whisper is installed locally; exit code 0.

- [ ] **Step 6: Commit**

```bash
git add jarvis/setup/prefetch.py jarvis/__main__.py tests/unit/setup/test_prefetch.py
git commit -m "feat(setup): jarvis --prefetch downloads all voice models the config needs"
```

---

### Task 8: Installer rebuild — no wizard, prefetch, worker CLI, shortcut, launch last

**Files:**
- Modify: `install/installer.py`
- Test: `tests/unit/install/test_installer_flow.py` (new — check
  `tests/` for an existing installer test module first and extend it instead)

**Interfaces:**
- Consumes: `python -m jarvis --prefetch` (Task 7),
  `jarvis.setup.dependencies.check_npm/check_claude_cli/install_claude_cli`,
  `jarvis.ui.icon_utils.ensure_start_menu_shortcut`.

- [ ] **Step 1: Write the failing tests** (pure-logic tests on the step plan;
subprocess calls are monkeypatched)

```python
"""Installer flow: no wizard, explanatory steps, launch is the LAST action."""
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
spec = importlib.util.spec_from_file_location("installer", REPO / "install" / "installer.py")
installer = importlib.util.module_from_spec(spec)
spec.loader.exec_module(installer)


def test_no_wizard_invocation_anywhere() -> None:
    source = (REPO / "install" / "installer.py").read_text(encoding="utf-8")
    assert "--wizard" not in source.replace("--no-wizard", "")


def test_dry_run_order_launch_last(monkeypatch, capsys) -> None:
    monkeypatch.setattr(installer, "write_managed_marker", lambda: None)
    rc = installer.main(["--dry-run", "--headless"])
    out = capsys.readouterr().out
    assert rc == 0
    # The launch step must come after every prepare step AND after the summary.
    assert out.rindex("Launch") > out.rindex("Voice models")
    assert out.rindex("Launch") > out.rindex("Done")


def test_update_run_is_detected(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(installer, "repo_root", lambda: tmp_path)
    (tmp_path / ".jarvis-managed-install").write_text("{}", encoding="utf-8")
    assert installer.is_update_run() is True
```

(Adjust the order assertion to the final output format — the invariant to
pin: the launch step is emitted after models/worker-cli/shortcut/summary.)

- [ ] **Step 2: Run to verify failure** — `python -m pytest tests/unit/install/ -v`
Expected: FAIL (`is_update_run` missing; wizard string present).

- [ ] **Step 3: Rewrite `installer.py`** — concrete changes:

1. **Delete** `step_wizard` (lines 243-255). Keep `--no-wizard` as a
   deprecated no-op:

```python
    parser.add_argument("--no-wizard", action="store_true",
                        help="deprecated: the installer never runs the terminal "
                             "wizard anymore (setup happens in the app)")
```

2. **Add update detection** (before `write_managed_marker` overwrites it):

```python
def is_update_run() -> bool:
    """True when this checkout was already installer-managed (re-run = update)."""
    return (repo_root() / ".jarvis-managed-install").exists()
```

3. **Add `step_models`:**

```python
def step_models(*, dry_run: bool) -> None:
    step("Voice models")
    note("downloading everything the voice pipeline needs, so the first")
    note("launch is ready immediately - nothing is fetched at startup")
    cmd = [str(venv_python()), "-m", "jarvis", "--prefetch"]
    if dry_run:
        console.print(f"[muted]      (dry-run) {' '.join(cmd)}[/]")
        return
    rc = run(cmd, cwd=repo_root(), check=False)
    if rc == 0:
        ok("all voice models are on disk")
    else:
        console.print("[bad]      Some models could not be downloaded - the app "
                      "will fetch them on first launch instead.[/]")
```

4. **Add `step_worker_cli`** (best-effort, never fatal):

```python
def step_worker_cli(*, dry_run: bool) -> None:
    step("Jarvis-Agent worker CLI")
    note("the coding-agent worker Jarvis delegates missions to (needs Node.js)")
    if dry_run:
        console.print("[muted]      (dry-run) npm i -g @anthropic-ai/claude-code[/]")
        return
    probe = (
        "from jarvis.setup.dependencies import check_claude_cli, check_npm, install_claude_cli\n"
        "import json, sys\n"
        "if check_claude_cli().present:\n"
        "    print('present'); sys.exit(0)\n"
        "if not check_npm().present:\n"
        "    print('no-npm'); sys.exit(0)\n"
        "ok, _ = install_claude_cli()\n"
        "print('installed' if ok else 'failed')\n"
    )
    result = subprocess.run(
        [str(venv_python()), "-c", probe], cwd=repo_root(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    verdict = (result.stdout or "").strip().splitlines()[-1] if result.stdout else "failed"
    if verdict == "present":
        ok("worker CLI already installed")
    elif verdict == "installed":
        ok("worker CLI installed (npm)")
    elif verdict == "no-npm":
        note("Node.js/npm not found - the Jarvis-Agent worker can be added later in-app")
    else:
        note("worker CLI install failed - it can be added later in-app")
```

5. **Add `step_shortcut`** (Windows-only, best-effort):

```python
def step_shortcut(*, dry_run: bool) -> None:
    if sys.platform != "win32":
        return
    step("Start Menu & taskbar identity")
    note("so the very first launch shows the Jarvis name + icon, not a generic Python entry")
    if dry_run:
        console.print("[muted]      (dry-run) ensure_start_menu_shortcut()[/]")
        return
    probe = (
        "from jarvis.ui.icon_utils import ensure_start_menu_shortcut\n"
        "print('ok' if ensure_start_menu_shortcut() else 'skipped')\n"
    )
    result = subprocess.run(
        [str(venv_python()), "-c", probe], cwd=repo_root(),
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )
    if (result.stdout or "").strip().endswith("ok"):
        ok("shortcut in place")
    else:
        note("could not create the shortcut - the app will retry on first launch")
```

6. **Add `step_ui_bundle_check`** (the spec-deviation integrity check):

```python
def step_ui_bundle_check() -> None:
    """Honest packaging check: the shipped UI build must be present + intact.

    The public snapshot ships a prebuilt jarvis/ui/web/dist; a dev clone may
    not have one. Missing or torn builds are the 'old/broken app' symptom, so
    say it out loud instead of letting the first launch look broken.
    """
    step("UI bundle")
    dist = repo_root() / "jarvis" / "ui" / "web" / "dist"
    index = dist / "index.html"
    if not index.is_file():
        note("no prebuilt UI found (dev clone?) - the app will serve a minimal page")
        note("public installs always ship the UI; if you used the one-liner, please report this")
        return
    html = index.read_text(encoding="utf-8", errors="replace")
    import re
    missing = [
        ref for ref in re.findall(r'(?:src|href)="/?(assets/[^"]+)"', html)
        if not (dist / ref).is_file()
    ]
    if missing:
        console.print(f"[bad]      UI build is incomplete ({missing[0]} missing) - "
                      "please report this; the app may look broken.[/]")
    else:
        ok("UI build present and intact")
```

7. **Rewrite `step_summary`** (explain what happens next; update-aware) and
   **flip the order** in `main()` so the summary prints BEFORE the launch and
   the launch is the last action:

```python
def step_summary(*, no_launch: bool, update: bool) -> None:
    console.print()
    setup_line = (
        "Your setup and settings are kept - no re-onboarding."
        if update
        else "The app opens with a one-time setup guide\n"
             "  (language, wake word, API keys). It never shows again after that."
    )
    console.print(Panel.fit(
        "[ok]Personal Jarvis is " + ("updated" if update else "installed") + ".[/]\n\n"
        f"[muted]Repo[/]   {repo_root()}\n"
        f"[muted]Venv[/]   {venv_python().parent.parent}\n\n"
        f"[brand.bold]{'Update' if update else 'What happens next'}[/]\n"
        f"  {setup_line}\n\n"
        "[brand.bold]Re-run anytime[/]\n"
        "  • Windows:  [brand]run.bat[/]\n"
        "  • macOS/Linux:  [brand]python -m jarvis.ui.web.launcher[/]\n\n"
        "[brand.bold]Update later[/]\n"
        "  Re-run the same install one-liner - it updates in place and keeps\n"
        "  your setup.",
        border_style="brand",
        title="[brand.bold]✓ Done[/]",
        title_align="left",
    ))
    if not no_launch:
        console.print("\n[muted]Launching the Desktop App now…[/]")
```

`main()` tail becomes:

```python
    update = is_update_run()
    step_preflight()
    if not args.dry_run:
        write_managed_marker()
    if not os.environ.get("JARVIS_INSTALL_NO_PIP"):
        step_pip_install(...)          # unchanged args
    step_models(dry_run=args.dry_run)
    step_worker_cli(dry_run=args.dry_run)
    step_shortcut(dry_run=args.dry_run)
    step_ui_bundle_check()
    step_summary(no_launch=args.no_launch, update=update)
    if not args.no_launch:
        step_launch(headless=args.headless, dry_run=args.dry_run)
    return 0
```

Also update the module docstring step list (remove wizard, add models/worker
CLI/shortcut/UI check, launch last) and the exit-code table (drop 3=wizard).

- [ ] **Step 4: Run tests + dry-run smoke**

Run: `python -m pytest tests/unit/install/ -v` → PASS
Run: `python install/installer.py --dry-run --headless` → step list shows
Environment → Installing → Voice models → Jarvis-Agent worker CLI →
UI bundle → Done-panel; no wizard; no prompts.

- [ ] **Step 5: Commit**

```bash
git add install/installer.py tests/unit/install/test_installer_flow.py
git commit -m "feat(install): non-interactive installer - prefetch everything, explain steps, launch last"
```

---

### Task 9: `python -m jarvis` no longer auto-runs the wizard

**Files:**
- Modify: `jarvis/__main__.py:493-496` + module docstring line 4
- Test: extend the existing `__main__` dispatch tests (locate via
  `rg "cmd_wizard|args.wizard" tests/`; create
  `tests/unit/test_main_dispatch_wizard.py` if none)

- [ ] **Step 1: Write the failing test** — extract the branch condition into a
pure helper `_should_run_wizard(wizard_flag: bool) -> bool` and pin it:

```python
"""Bare `python -m jarvis` must start the app, never the terminal wizard."""
import jarvis.__main__ as main_mod


def test_wizard_only_on_explicit_flag() -> None:
    # First-run state must NOT factor in anymore: setup lives in the app.
    assert main_mod._should_run_wizard(False) is False
    assert main_mod._should_run_wizard(True) is True


def test_dispatch_source_no_longer_consults_first_run() -> None:
    import inspect

    src = inspect.getsource(main_mod)
    # The old auto-wizard trigger was `args.wizard or cfg.is_first_run()`.
    assert "args.wizard or cfg.is_first_run()" not in src
```

- [ ] **Step 2: Run to verify failure** — the helper does not exist yet.

- [ ] **Step 3: Implement** — replace lines 493-496:

```python
def _should_run_wizard(wizard_flag: bool) -> bool:
    """Setup lives in the desktop/browser onboarding (first-launch guide);
    the terminal wizard is an explicit opt-in for SSH-only setups."""
    return wizard_flag
```

and in the dispatch:

```python
    if _should_run_wizard(args.wizard):
        return _cmd_wizard()
```

Update the usage docstring (line 4): `python -m jarvis  # Starts the tray app
(first-run setup happens in the app)`.

- [ ] **Step 4: Run tests** — new test + `python -m pytest tests/unit -k "main or wizard" -v` → PASS

- [ ] **Step 5: Commit**

```bash
git add jarvis/__main__.py tests/unit/test_main_dispatch_wizard.py
git commit -m "feat(cli): bare python -m jarvis starts the app; wizard is explicit opt-in only"
```

---

### Task 10: Pin the no-re-onboarding-after-update contract

**Files:**
- Test: `tests/unit/setup/test_onboarding_fastpath.py` (extend)
- Test: `jarvis/ui/web/frontend/src/components/onboarding/OnboardingGate.test.tsx` (extend)

- [ ] **Step 1: Backend guard** — add to `test_onboarding_fastpath.py`:

```python
async def test_completed_survives_any_version_bump(tmp_path, monkeypatch) -> None:
    """The update contract: NOTHING version-shaped may re-open the gate.

    Completed markers set + a terms-version bump => still completed. If someone
    ever wires a version comparison into `completed`, this fails.
    """
    fp._STATE_PATH_OVERRIDE = tmp_path / "setup_state.json"
    try:
        st.accept_terms("0.1-ancient", path=tmp_path / "setup_state.json")
        st.mark_onboarding_complete(path=tmp_path / "setup_state.json")
        sent, send = _collector()
        await fp.handle(_scope("GET", "/api/onboarding/state"), _receive_empty, send)
        payload = _body_json(sent)
        assert payload["completed"] is True
        assert payload["terms"]["accepted_version"] != payload["terms"]["current_version"]
    finally:
        fp._STATE_PATH_OVERRIDE = None
```

- [ ] **Step 2: Frontend guard** — add to `OnboardingGate.test.tsx`:

```tsx
it("stays hidden when completed, even with an outdated accepted terms version", async () => {
  mockOnboarding({
    completed: true,
    terms: { accepted: true, accepted_version: "0.1", current_version: "9.9" },
  });
  const { container } = render(<OnboardingGate />);
  await waitFor(() => expect(container).toBeEmptyDOMElement());
});
```

- [ ] **Step 3: Run both** — `python -m pytest tests/unit/setup/test_onboarding_fastpath.py -v`
and `npm run test -- OnboardingGate` → PASS (they should pass immediately —
these are regression pins, not new behavior; if one fails, that is a real bug
to fix before proceeding).

- [ ] **Step 4: Commit**

```bash
git add tests/unit/setup/test_onboarding_fastpath.py jarvis/ui/web/frontend/src/components/onboarding/OnboardingGate.test.tsx
git commit -m "test(onboarding): pin the no-re-onboarding-after-update contract"
```

---

### Task 11: Extend the fresh-install smoke workflow

**Files:**
- Modify: `.github/workflows/fresh-install-smoke.yml`

- [ ] **Step 1: Update the installer invocation** — replace
`python install/installer.py --headless --no-launch --no-wizard` with
`python install/installer.py --headless --no-launch` and capture output:

```bash
          python install/installer.py --headless --no-launch | tee install.log
          # The installer must never prompt: fail if anything interactive leaked in.
          ! grep -Ei "input\(|\? \[y/n\]|press enter" install.log
```

- [ ] **Step 2: Add an onboarding-contract step** after the existing boot
probe (replace the probe's inner Python with this extended version):

```python
          import sys, time, json, http.client
          port = int(sys.argv[1])

          def req(method, path):
              c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
              c.request(method, path)
              r = c.getresponse()
              return r.status, r.read()

          # 1) Onboarding state must answer 200 fast (fast path), completed=false.
          deadline = time.time() + 15
          state = None
          while time.time() < deadline:
              try:
                  status, body = req("GET", "/api/onboarding/state")
                  if status == 200:
                      state = json.loads(body)
                      break
              except Exception:
                  pass
              time.sleep(0.5)
          assert state is not None, "onboarding state did not answer within 15 s of boot"
          assert state["completed"] is False, f"fresh install must be incomplete: {state}"

          # 2) Complete it; the state must flip durably (same store the real app reads).
          status, _ = req("POST", "/api/onboarding/complete")
          assert status == 200, f"complete failed: {status}"
          status, body = req("GET", "/api/onboarding/state")
          assert json.loads(body)["completed"] is True, "completed did not persist"
          print("onboarding contract OK")
          sys.exit(0)
```

- [ ] **Step 3: Add a restart leg** (kill the server, boot a second process,
assert `completed` stays true — the no-re-onboarding contract end-to-end):

```bash
          kill "$BOOTPID" 2>/dev/null || true
          sleep 2
          python -m jarvis.ui.web.launcher --headless > boot2.log 2>&1 &
          BOOTPID2=$!
          python - "$PORT" <<'PYEOF'
          import sys, time, json, http.client
          port = int(sys.argv[1])
          for _ in range(30):
              try:
                  c = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
                  c.request("GET", "/api/onboarding/state")
                  r = c.getresponse()
                  if r.status == 200:
                      assert json.loads(r.read())["completed"] is True, "onboarding re-armed after restart!"
                      print("restart contract OK"); sys.exit(0)
              except AssertionError:
                  raise
              except Exception:
                  pass
              time.sleep(1)
          sys.exit(7)
          PYEOF
          RC=$?
          kill "$BOOTPID2" 2>/dev/null || true
          exit $RC
```

- [ ] **Step 4: Validate the workflow** — `python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('.github/workflows/fresh-install-smoke.yml').read_text(encoding='utf-8'))"` → no error.
(A full CI run happens on the next push to main touching these paths.)

- [ ] **Step 5: Commit**

```bash
git add .github/workflows/fresh-install-smoke.yml
git commit -m "ci(smoke): assert first-boot onboarding contract + no re-onboarding after restart"
```

---

### Task 12: Docs + Stage-1 messaging

**Files:**
- Modify: `install/README.md` (drop the stale "one-liner not active" §18-23 if
  the public repo is live — verify against root `README.md`; align both),
  update the flow description (no wizard; app-first onboarding; update
  semantics).
- Modify: `README.md:97-116` install section — one sentence added: the app
  walks you through setup on first launch; re-running the one-liner updates
  in place and never re-runs setup.
- Modify: `install/install.ps1` / `install/install.sh` final echo lines — if
  they mention the wizard, reword to "the app guides you through setup on
  first launch" (verify with `rg -i "wizard" install/`).

- [ ] **Step 1: Apply the edits** (English; keep the existing tone/format of
each file).
- [ ] **Step 2: Grep-check** — `rg -i "wizard" install/ README.md` shows no
claim that install runs a wizard.
- [ ] **Step 3: Commit**

```bash
git add install/README.md README.md install/install.ps1 install/install.sh
git commit -m "docs(install): install downloads everything; setup happens in-app on first launch"
```

---

### Task 13: Full verification sweep

- [ ] **Step 1: Backend** — `python -m pytest tests/unit -q` (full unit suite)
and `ruff check jarvis/ install/ && mypy jarvis/` → all green.
- [ ] **Step 2: Frontend** — in `jarvis/ui/web/frontend/`: `npm run test` and
`npm run build` → green build.
- [ ] **Step 3: Cross-platform / non-maintainer paths (§3 definition of done):**
  - Headless Linux: if Docker is available, run the smoke inside
    `python:3.11-slim` (venv → `pip install rich packaging` →
    `python install/installer.py --headless --no-launch` → boot →
    onboarding-state probe, mirroring Task 11). Otherwise state honestly that
    the Linux leg ran only in CI.
  - Windows (this machine): `python install/installer.py --dry-run` +
    `python -m jarvis --prefetch` + boot via `run.bat`-equivalent and probe
    `/api/onboarding/state` while warming (must be 200 within seconds).
  - Fresh-first-launch simulation: `python -m jarvis --reset-onboarding`,
    boot headless, `GET /api/onboarding/state` → `completed:false`;
    `POST /complete`; restart; → `completed:true`.
- [ ] **Step 4: Fix anything red; re-run until green. Commit fixes as `fix:`.**
