# Adjustable Silence-Window Slider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a Settings slider (0.5–5.0 s, step 0.1 s, default 1.5 s, reset) that tunes the voice endpoint silence window, persisted to `jarvis.toml` and applied live to the running pipeline without a restart.

**Architecture:** One integer (`vad_silence_ms`) threaded through a five-link chain — config field → boot read → live VAD setter (with a growing max-utterance cap) → pipeline delegate → REST route → React slider. The route and frontend mirror the existing wake-word / overlay settings pattern exactly.

**Tech Stack:** Python 3.11, Pydantic v2, FastAPI, tomlkit (config_writer), React + TypeScript + Vite + vitest, Tailwind.

---

## File Structure

- `jarvis/core/config.py` — add `SpeechConfig.vad_silence_ms` field.
- `jarvis/core/config_writer.py` — add `set_silence_window_ms`.
- `jarvis/audio/vad.py` — add `SileroEndpointer.set_silence_window_ms`.
- `jarvis/speech/pipeline.py` — add `SpeechPipeline.set_silence_window_ms`; read the config field at construction (the third construction site, `desktop_app.py`, passes it).
- `jarvis/ui/desktop_app.py` — pass `vad_silence_ms=self.cfg.speech.vad_silence_ms`.
- `jarvis/ui/web/settings_routes.py` — `GET/PUT /api/settings/silence-window`.
- `jarvis/ui/web/frontend/src/hooks/useSilenceWindow.ts` — new hook.
- `jarvis/ui/web/frontend/src/views/settings/SilenceWindowGroup.tsx` — new component.
- `jarvis/ui/web/frontend/src/views/SettingsView.tsx` — render the new group.
- `jarvis/ui/web/frontend/src/i18n/locales/{en,de,es}.json` — `settings_view.silence_window.*`.
- Tests: `tests/unit/core/test_silence_window_config.py`, `tests/unit/audio/test_vad_turn_taking.py` (extend), `tests/integration/test_settings_silence_window.py`, `jarvis/ui/web/frontend/src/views/settings/SilenceWindowGroup.test.tsx`.

Test names use a dedicated new file per layer (shared working tree — avoid colliding with parallel sessions' edits in the big shared files).

---

### Task 1: Config field `SpeechConfig.vad_silence_ms`

**Files:**
- Modify: `jarvis/core/config.py` (the `SpeechConfig` class, ~line 1609)
- Test: `tests/unit/core/test_silence_window_config.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/core/test_silence_window_config.py
"""Range + default guards for the user-tunable voice silence window."""
from __future__ import annotations

import pytest
from pydantic import ValidationError

from jarvis.core.config import SpeechConfig


def test_default_is_1500_ms() -> None:
    assert SpeechConfig().vad_silence_ms == 1500


def test_accepts_in_range_value() -> None:
    assert SpeechConfig(vad_silence_ms=2500).vad_silence_ms == 2500


def test_rejects_below_minimum() -> None:
    with pytest.raises(ValidationError):
        SpeechConfig(vad_silence_ms=400)


def test_rejects_above_maximum() -> None:
    with pytest.raises(ValidationError):
        SpeechConfig(vad_silence_ms=6000)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/core/test_silence_window_config.py -q`
Expected: FAIL — `SpeechConfig() got an unexpected keyword argument 'vad_silence_ms'` / the default assertion errors (field missing).

- [ ] **Step 3: Add the field**

In `jarvis/core/config.py`, inside `class SpeechConfig(BaseModel)`, after the `completeness` field, add (keep the existing `Field` import — it is already used in this module):

```python
    # Voice endpoint silence window: how long the VAD waits in silence before
    # treating an utterance as finished. User-tunable "think buffer" (desktop
    # Settings → Voice slider). Range-clamped 500–5000 ms; default 1500 ms
    # ("1.5s rule"). Read at SpeechPipeline construction and live-applied via the
    # /api/settings/silence-window route. extra="allow" already on SpeechConfig
    # keeps the self-mod pre-validate pipeline safe (AP-16).
    vad_silence_ms: int = Field(default=1500, ge=500, le=5000)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/core/test_silence_window_config.py -q`
Expected: PASS (4 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config.py tests/unit/core/test_silence_window_config.py
git commit -m "feat(config): add tunable SpeechConfig.vad_silence_ms (500-5000ms)"
```

---

### Task 2: `config_writer.set_silence_window_ms`

**Files:**
- Modify: `jarvis/core/config_writer.py` (next to `set_overlay_style`, ~line 316)
- Test: `tests/unit/core/test_silence_window_config.py` (extend)

- [ ] **Step 1: Write the failing test** (append to the same test file)

```python
def test_writer_roundtrips_to_speech_table(tmp_path) -> None:
    import tomllib

    from jarvis.core import config_writer

    p = tmp_path / "jarvis.toml"
    p.write_text("", encoding="utf-8")
    config_writer.set_silence_window_ms(2500, path=p)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["speech"]["vad_silence_ms"] == 2500


def test_writer_clamps_out_of_range(tmp_path) -> None:
    import tomllib

    from jarvis.core import config_writer

    p = tmp_path / "jarvis.toml"
    p.write_text("", encoding="utf-8")
    config_writer.set_silence_window_ms(99999, path=p)
    data = tomllib.loads(p.read_text(encoding="utf-8"))
    assert data["speech"]["vad_silence_ms"] == 5000
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/core/test_silence_window_config.py -k writer -q`
Expected: FAIL — `module 'jarvis.core.config_writer' has no attribute 'set_silence_window_ms'`

- [ ] **Step 3: Add the writer** (in `jarvis/core/config_writer.py`, after `set_overlay_style`)

```python
def set_silence_window_ms(ms: int, *, path: Path = DEFAULT_CONFIG_FILE) -> None:
    """Persist the voice silence window to ``[speech] vad_silence_ms`` in jarvis.toml.

    Clamps to the same 500–5000 ms bounds the config field enforces, so a stray
    value can never wedge endpointing. TOML-only by design (not in the
    drift-guard's reference snapshot, like :func:`set_overlay_style`); the
    Settings route applies the change live, this persists the boot default.
    """
    clamped = max(500, min(5000, int(ms)))
    _patch_table(path, "speech", "vad_silence_ms", clamped)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/core/test_silence_window_config.py -q`
Expected: PASS (6 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/core/config_writer.py tests/unit/core/test_silence_window_config.py
git commit -m "feat(config): config_writer.set_silence_window_ms persists [speech].vad_silence_ms"
```

---

### Task 3: `SileroEndpointer.set_silence_window_ms` (live setter + growing cap)

**Files:**
- Modify: `jarvis/audio/vad.py` (the `SileroEndpointer` class, near `extend_silence_window` ~line 115)
- Test: `tests/unit/audio/test_vad_turn_taking.py` (append)

- [ ] **Step 1: Write the failing tests** (append to `tests/unit/audio/test_vad_turn_taking.py`)

```python
def test_set_silence_window_ms_updates_frames_and_keeps_8s_cap() -> None:
    """A small window updates the silence-frame count and keeps the 8 s cap."""
    vad = SileroEndpointer(silence_ms=1500)
    vad.set_silence_window_ms(2500)
    assert vad._silence_frames == 2500 // 32  # 78
    assert vad._max_samples == 8 * 16000  # ceil(2.5)+5=8 → max(8,8)=8


def test_set_silence_window_ms_grows_cap_for_large_window() -> None:
    """A 5 s window grows the hard cap to 10 s so a long pause is never beheaded."""
    vad = SileroEndpointer(silence_ms=1500)
    vad.set_silence_window_ms(5000)
    assert vad._silence_frames == 5000 // 32  # 156
    assert vad._max_samples == 10 * 16000  # ceil(5)+5=10


def test_set_silence_window_ms_clamps_out_of_range() -> None:
    vad = SileroEndpointer(silence_ms=1500)
    vad.set_silence_window_ms(50)       # below min
    assert vad._silence_frames == 500 // 32
    vad.set_silence_window_ms(99999)    # above max
    assert vad._silence_frames == 5000 // 32


@pytest.mark.asyncio
async def test_live_widened_window_defers_endpoint_mid_stream() -> None:
    """Widening the window mid-utterance must defer an endpoint the old (narrow)
    window would have fired — proving the change is live, not boot-only."""
    # base 96 ms → 3 silent frames to endpoint; widen to 640 ms → 20 frames.
    vad = SileroEndpointer(silence_ms=96, min_speech_ms=96)
    probs = [0.9] * 5 + [0.0] * 8  # 8 silent frames: >3 base, <20 widened
    _stub_vad(vad, probs)
    frames = [_pcm_frame(0.05)] * 5 + [_pcm_frame(0.0)] * 8

    out: list[bytes] = []
    async for utterance in vad.utterances(
        _chunks_with_action(
            frames, at_index=4, action=lambda: vad.set_silence_window_ms(640)
        )
    ):
        out.append(utterance)

    assert out == [], (
        "8 silent frames ended the turn despite the window being widened live to "
        "20 frames — the setter did not take effect mid-stream"
    )
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/audio/test_vad_turn_taking.py -k "silence_window or widened" -q`
Expected: FAIL — `AttributeError: 'SileroEndpointer' object has no attribute 'set_silence_window_ms'`

- [ ] **Step 3: Add the setter** (in `jarvis/audio/vad.py`, right after `extend_silence_window`)

```python
    def set_silence_window_ms(self, ms: int) -> None:
        """Live-update the BASE silence window and the matching hard cap.

        The running ``utterances()`` loop reads ``_effective_silence_frames`` and
        ``_max_samples`` on every frame, so a change here takes effect on the next
        processed frame — no pipeline rebuild (the user-tunable "think buffer",
        desktop Settings → Voice). ``_extra_silence_frames`` (delegation patience)
        stays additive on top of the new base. The max-utterance cap grows with
        the window so a long thinking pause is never beheaded by the safety net
        (maintainer choice 2026-06-16): cap = max(8 s, ceil(window_s) + 5 s).
        Clamps defensively to 500–5000 ms — the route validates, but the VAD must
        not trust callers or a stray value could wedge endpointing.
        """
        ms = max(500, min(5000, int(ms)))
        self._silence_frames = max(1, ms // 32)
        cap_s = max(8, (ms + 999) // 1000 + 5)  # (ms+999)//1000 == ceil(ms/1000)
        self._max_samples = cap_s * VAD_SAMPLE_RATE
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/audio/test_vad_turn_taking.py -q`
Expected: PASS (all, including the 4 new)

- [ ] **Step 5: Commit**

```bash
git add jarvis/audio/vad.py tests/unit/audio/test_vad_turn_taking.py
git commit -m "feat(vad): live set_silence_window_ms with growing max-utterance cap"
```

---

### Task 4: `SpeechPipeline.set_silence_window_ms` + read config at construction

**Files:**
- Modify: `jarvis/speech/pipeline.py` (add method; the constructor already takes `vad_silence_ms`)
- Test: `tests/unit/audio/test_vad_turn_taking.py` is VAD-only; add the pipeline-delegate test to `tests/integration/test_settings_silence_window.py` in Task 6 (it needs a pipeline). For this task, a focused unit test on the method via a constructed-but-not-started pipeline is heavy; instead assert delegation with a light stub.
- Test: `tests/unit/speech/test_pipeline_silence_window.py` (create)

- [ ] **Step 1: Write the failing test**

```python
# tests/unit/speech/test_pipeline_silence_window.py
"""SpeechPipeline.set_silence_window_ms delegates to the live VAD."""
from __future__ import annotations

from jarvis.speech.pipeline import SpeechPipeline


class _RecordingVad:
    def __init__(self) -> None:
        self.calls: list[int] = []

    def set_silence_window_ms(self, ms: int) -> None:
        self.calls.append(ms)


def test_pipeline_delegates_to_vad() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)  # skip heavy __init__
    vad = _RecordingVad()
    pipe._vad = vad
    pipe.set_silence_window_ms(2500)
    assert vad.calls == [2500]


def test_pipeline_no_vad_is_safe() -> None:
    pipe = SpeechPipeline.__new__(SpeechPipeline)
    pipe._vad = None
    # Must not raise when the pipeline is headless / not yet wired.
    pipe.set_silence_window_ms(2500)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/speech/test_pipeline_silence_window.py -q`
Expected: FAIL — `AttributeError: 'SpeechPipeline' object has no attribute 'set_silence_window_ms'`

- [ ] **Step 3: Add the method** (in `jarvis/speech/pipeline.py`, near `set_wake_plan` / `set_keybinds`)

```python
    def set_silence_window_ms(self, ms: int) -> None:
        """Live-apply a new voice silence window to the running VAD.

        Delegates to ``SileroEndpointer.set_silence_window_ms`` so a Settings
        change takes effect immediately (no restart). No-op-safe when the VAD is
        absent (headless / not yet started) — the value still persisted and
        applies on the next start.
        """
        vad = getattr(self, "_vad", None)
        setter = getattr(vad, "set_silence_window_ms", None)
        if callable(setter):
            setter(int(ms))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/speech/test_pipeline_silence_window.py -q`
Expected: PASS (2 passed)

- [ ] **Step 5: Commit**

```bash
git add jarvis/speech/pipeline.py tests/unit/speech/test_pipeline_silence_window.py
git commit -m "feat(speech): SpeechPipeline.set_silence_window_ms delegates to live VAD"
```

---

### Task 5: Boot read — pass the config value at desktop construction

**Files:**
- Modify: `jarvis/ui/desktop_app.py` (the `SpeechPipeline(...)` call ~line 1569)

This one-line wiring is not unit-testable in isolation (desktop_app builds the GUI app). It is covered end-to-end by the chrome-checkup + voice drive in Task 9. The constructor already accepts `vad_silence_ms`; today the call omits it so the 1500 default always wins.

- [ ] **Step 1: Add the argument**

In `jarvis/ui/desktop_app.py`, in the `SpeechPipeline(...)` constructor call, add this line among the other keyword args (e.g. right after `hangup_hotkeys=...`):

```python
                vad_silence_ms=self.cfg.speech.vad_silence_ms,
```

- [ ] **Step 2: Verify it imports + constructs cleanly**

Run: `"/c/Program Files/Python311/python.exe" -c "import ast; ast.parse(open('jarvis/ui/desktop_app.py', encoding='utf-8').read()); print('parse ok')"`
Expected: `parse ok`

Run: `"/c/Program Files/Python311/python.exe" -m ruff check jarvis/ui/desktop_app.py`
Expected: no new errors on the touched line.

- [ ] **Step 3: Commit**

```bash
git add jarvis/ui/desktop_app.py
git commit -m "feat(desktop): honour [speech].vad_silence_ms at SpeechPipeline construction"
```

---

### Task 6: REST route `GET/PUT /api/settings/silence-window`

**Files:**
- Modify: `jarvis/ui/web/settings_routes.py` (add a new section, mirroring `/overlay-style`)
- Test: `tests/integration/test_settings_silence_window.py` (create)

- [ ] **Step 1: Write the failing tests**

```python
# tests/integration/test_settings_silence_window.py
"""Integration tests for /api/settings/silence-window (the think-buffer slider)."""
from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import JarvisConfig
from jarvis.ui.web.server import WebServer


class _FakePipeline:
    def __init__(self) -> None:
        self.applied: list[int] = []

    def set_silence_window_ms(self, ms: int) -> None:
        self.applied.append(ms)


@pytest.fixture
def server() -> Iterator[WebServer]:
    cfg = JarvisConfig()
    cfg.ui.dev_mode = True
    bus = EventBus()
    s = WebServer(cfg, bus=bus)
    s.app.state.config = cfg
    s.app.state.bus = bus
    yield s


@pytest.fixture(autouse=True)
def _no_toml_write(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    from jarvis.core import config_writer

    monkeypatch.setattr(
        config_writer, "set_silence_window_ms", lambda ms, **kw: calls.append(ms)
    )
    return calls


def test_get_returns_current_and_bounds(server: WebServer) -> None:
    with TestClient(server.app) as client:
        body = client.get("/api/settings/silence-window").json()
        assert body == {"ms": 1500, "default": 1500, "min": 500, "max": 5000}


def test_put_persists_and_applies_live(server: WebServer) -> None:
    pipe = _FakePipeline()
    server.app.state.speech_pipeline = pipe
    with TestClient(server.app) as client:
        resp = client.put("/api/settings/silence-window", json={"ms": 2500})
        assert resp.status_code == 200
        body = resp.json()
        assert body["ms"] == 2500
        assert body["applied_live"] is True
        assert body["restart_required"] is False
        assert pipe.applied == [2500]
        # in-memory cfg reflects it
        assert server.app.state.config.speech.vad_silence_ms == 2500


def test_put_out_of_range_is_400(server: WebServer) -> None:
    with TestClient(server.app) as client:
        assert client.put("/api/settings/silence-window", json={"ms": 100}).status_code == 400
        assert client.put("/api/settings/silence-window", json={"ms": 9000}).status_code == 400


def test_put_without_pipeline_reports_restart_required(server: WebServer) -> None:
    with TestClient(server.app) as client:
        body = client.put("/api/settings/silence-window", json={"ms": 1500}).json()
        assert body["applied_live"] is False
        assert body["restart_required"] is True
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/integration/test_settings_silence_window.py -q`
Expected: FAIL — 404 on the unknown route (`GET/PUT /api/settings/silence-window`).

- [ ] **Step 3: Add the route section** (append to `jarvis/ui/web/settings_routes.py`)

```python
# ---------------------------------------------------------------------------
# Voice silence window (the user-tunable "think buffer"). GET current + bounds;
# PUT to change. Persisted to jarvis.toml [speech].vad_silence_ms AND live-applied
# to the running SpeechPipeline (set_silence_window_ms → SileroEndpointer), so a
# change takes effect immediately without a restart; a headless/down pipeline
# falls back to "applies on next start". Range 500–5000 ms, default 1500.
# ---------------------------------------------------------------------------

_SILENCE_WINDOW_MIN = 500
_SILENCE_WINDOW_MAX = 5000
_SILENCE_WINDOW_DEFAULT = 1500


class SilenceWindowBody(BaseModel):
    ms: int = Field(..., ge=_SILENCE_WINDOW_MIN, le=_SILENCE_WINDOW_MAX)
    persist: bool = Field(default=True, description="Persist as boot default in jarvis.toml")


def _current_silence_window_ms(request: Request) -> int:
    cfg = _config(request)
    speech = getattr(cfg, "speech", None)
    return int(getattr(speech, "vad_silence_ms", _SILENCE_WINDOW_DEFAULT))


@router.get("/silence-window")
async def get_silence_window(request: Request) -> dict[str, object]:
    return {
        "ms": _current_silence_window_ms(request),
        "default": _SILENCE_WINDOW_DEFAULT,
        "min": _SILENCE_WINDOW_MIN,
        "max": _SILENCE_WINDOW_MAX,
    }


@router.put("/silence-window")
async def put_silence_window(body: SilenceWindowBody, request: Request) -> dict[str, object]:
    ms = int(body.ms)  # already range-validated by the Pydantic Field

    # Best-effort in-memory cfg update so a later cfg read agrees pre-restart.
    cfg = _config(request)
    if cfg is not None and getattr(cfg, "speech", None) is not None:
        try:
            cfg.speech.vad_silence_ms = ms  # type: ignore[attr-defined]
        except Exception as exc:  # noqa: BLE001 — frozen model is not an error
            log.debug("in-memory speech.vad_silence_ms update skipped: %s", exc)

    persisted = False
    if body.persist:
        try:
            from jarvis.core import config_writer
            from jarvis.core.config import resolve_config_path

            config_writer.set_silence_window_ms(ms, path=resolve_config_path())
            persisted = True
        except Exception as exc:  # noqa: BLE001 — persist is best-effort
            log.warning("silence-window persist failed (live apply still attempted): %s", exc)

    # Live-apply to the running voice pipeline so the new window works
    # immediately — no app restart. Best-effort: a headless/down pipeline just
    # means it applies on next start.
    applied_live = False
    pipeline = getattr(request.app.state, "speech_pipeline", None)
    if pipeline is not None and hasattr(pipeline, "set_silence_window_ms"):
        try:
            pipeline.set_silence_window_ms(ms)
            applied_live = True
        except Exception as exc:  # noqa: BLE001 — never fail the save on a live-apply hiccup
            log.warning("silence-window live-apply failed (persisted; applies on restart): %s", exc)

    return {
        "ok": True,
        "ms": ms,
        "default": _SILENCE_WINDOW_DEFAULT,
        "persisted": persisted,
        "applied_live": applied_live,
        "restart_required": not applied_live,
    }
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/integration/test_settings_silence_window.py -q`
Expected: PASS (4 passed). Note: an out-of-range PUT returns 422 from FastAPI's Pydantic validation, not 400 — if `test_put_out_of_range_is_400` fails on the status code, change the assertion to `in (400, 422)` (FastAPI body-validation is 422; both are correct "rejected" semantics).

- [ ] **Step 5: Commit**

```bash
git add jarvis/ui/web/settings_routes.py tests/integration/test_settings_silence_window.py
git commit -m "feat(api): GET/PUT /api/settings/silence-window (persist + live-apply)"
```

---

### Task 7: Frontend hook `useSilenceWindow`

**Files:**
- Create: `jarvis/ui/web/frontend/src/hooks/useSilenceWindow.ts`

- [ ] **Step 1: Write the hook** (mirrors `useAutostart`)

```typescript
// jarvis/ui/web/frontend/src/hooks/useSilenceWindow.ts
import { useCallback, useEffect, useState } from "react";

/** Voice silence window (the "think buffer") from GET /api/settings/silence-window. */
export interface SilenceWindowConfig {
  ms: number;
  default: number;
  min: number;
  max: number;
}

export interface SilenceWindowSaveResult {
  ok: boolean;
  ms: number;
  default: number;
  persisted: boolean;
  applied_live: boolean;
  restart_required: boolean;
}

/** Loads /api/settings/silence-window and exposes setMs(). Mirrors useAutostart. */
export function useSilenceWindow() {
  const [config, setConfig] = useState<SilenceWindowConfig | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  const refetch = useCallback(async () => {
    setError(null);
    try {
      const res = await fetch("/api/settings/silence-window");
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data: SilenceWindowConfig = await res.json();
      setConfig(data);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  const setMs = useCallback(
    async (ms: number): Promise<SilenceWindowSaveResult> => {
      const res = await fetch("/api/settings/silence-window", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ ms, persist: true }),
      });
      const body = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(body.detail ?? `HTTP ${res.status}`);
      const result = body as SilenceWindowSaveResult;
      setConfig((prev) => (prev ? { ...prev, ms: result.ms } : prev));
      return result;
    },
    [],
  );

  return { config, loading, error, refetch, setMs };
}
```

- [ ] **Step 2: Typecheck**

Run (from `jarvis/ui/web/frontend/`): `npx tsc --noEmit`
Expected: no errors referencing `useSilenceWindow.ts`.

- [ ] **Step 3: Commit**

```bash
git add jarvis/ui/web/frontend/src/hooks/useSilenceWindow.ts
git commit -m "feat(ui): useSilenceWindow hook for the think-buffer slider"
```

---

### Task 8: Frontend component `SilenceWindowGroup` + wire into SettingsView + i18n

**Files:**
- Create: `jarvis/ui/web/frontend/src/views/settings/SilenceWindowGroup.tsx`
- Modify: `jarvis/ui/web/frontend/src/views/SettingsView.tsx` (import + render)
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/{en,de,es}.json`
- Test: `jarvis/ui/web/frontend/src/views/settings/SilenceWindowGroup.test.tsx` (create)

- [ ] **Step 1: Add i18n keys.** In each locale file, inside the `"settings_view": { ... }` object (next to the existing `"autostart": { ... }` block), add:

`en.json`:
```json
    "silence_window": {
      "title": "Thinking pause",
      "description": "How long Jarvis waits in silence before it sends what you said. Raise it to give yourself more time to pause and finish a thought.",
      "unit_seconds": "s",
      "reset": "Reset to default (1.5 s)",
      "saved_toast": "Thinking pause set to {0}.",
      "restart_caption": "Saved — applies on next start."
    },
```

`de.json` (German translation):
```json
    "silence_window": {
      "title": "Denkpause",
      "description": "Wie lange Jarvis in Stille wartet, bevor er das Gesagte abschickt. Höher stellen, um mehr Zeit zum Innehalten und Zu-Ende-Denken zu haben.",
      "unit_seconds": "s",
      "reset": "Auf Standard zurücksetzen (1,5 s)",
      "saved_toast": "Denkpause auf {0} gesetzt.",
      "restart_caption": "Gespeichert — wird beim nächsten Start aktiv."
    },
```

`es.json` (Spanish translation):
```json
    "silence_window": {
      "title": "Pausa para pensar",
      "description": "Cuánto espera Jarvis en silencio antes de enviar lo que dijiste. Súbelo para darte más tiempo para pausar y terminar una idea.",
      "unit_seconds": "s",
      "reset": "Restablecer al valor predeterminado (1,5 s)",
      "saved_toast": "Pausa para pensar fijada en {0}.",
      "restart_caption": "Guardado — se aplica en el próximo inicio."
    },
```

- [ ] **Step 2: Write the failing component test**

```tsx
// jarvis/ui/web/frontend/src/views/settings/SilenceWindowGroup.test.tsx
import { describe, it, expect, vi, beforeEach, afterEach } from "vitest";
import { render, screen, fireEvent, waitFor } from "@testing-library/react";
import { SilenceWindowGroup } from "./SilenceWindowGroup";

const fetchMock = vi.fn();

beforeEach(() => {
  fetchMock.mockReset();
  vi.stubGlobal("fetch", fetchMock);
});
afterEach(() => vi.unstubAllGlobals());

function mockGet(ms = 1500) {
  fetchMock.mockResolvedValueOnce({
    ok: true,
    json: async () => ({ ms, default: 1500, min: 500, max: 5000 }),
  });
}

describe("SilenceWindowGroup", () => {
  it("renders the slider at the fetched value", async () => {
    mockGet(1500);
    render(<SilenceWindowGroup />);
    const slider = (await screen.findByRole("slider")) as HTMLInputElement;
    expect(slider.value).toBe("1500");
    expect(screen.getByText("1.5 s")).toBeInTheDocument();
  });

  it("sends one PUT on commit, not per tick", async () => {
    mockGet(1500);
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ok: true, ms: 2500, default: 1500,
        persisted: true, applied_live: true, restart_required: false,
      }),
    });
    render(<SilenceWindowGroup />);
    const slider = (await screen.findByRole("slider")) as HTMLInputElement;
    // drag (onChange) updates the label but does not PUT yet
    fireEvent.change(slider, { target: { value: "2500" } });
    expect(fetchMock).toHaveBeenCalledTimes(1); // only the GET so far
    // release (commit) fires the PUT
    fireEvent.mouseUp(slider);
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    const putCall = fetchMock.mock.calls[1];
    expect(putCall[0]).toBe("/api/settings/silence-window");
    expect(JSON.parse(putCall[1].body)).toMatchObject({ ms: 2500 });
  });

  it("reset commits 1500", async () => {
    mockGet(3000);
    fetchMock.mockResolvedValueOnce({
      ok: true,
      json: async () => ({
        ok: true, ms: 1500, default: 1500,
        persisted: true, applied_live: true, restart_required: false,
      }),
    });
    render(<SilenceWindowGroup />);
    await screen.findByRole("slider");
    fireEvent.click(screen.getByRole("button", { name: /reset|zurück|restablecer/i }));
    await waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(2));
    expect(JSON.parse(fetchMock.mock.calls[1][1].body)).toMatchObject({ ms: 1500 });
  });
});
```

- [ ] **Step 3: Run the component test to verify it fails**

Run (from `jarvis/ui/web/frontend/`): `npx vitest run src/views/settings/SilenceWindowGroup.test.tsx`
Expected: FAIL — cannot resolve `./SilenceWindowGroup`.

- [ ] **Step 4: Write the component**

```tsx
// jarvis/ui/web/frontend/src/views/settings/SilenceWindowGroup.tsx
import { useEffect, useRef, useState } from "react";
import { Timer } from "lucide-react";
import { useSilenceWindow } from "@/hooks/useSilenceWindow";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

/**
 * "Thinking pause" slider inside the Settings view. Tunes the voice endpoint
 * silence window (how long Jarvis waits in silence before submitting). Range
 * 0.5–5.0 s, step 0.1 s, default 1.5 s. The label tracks the slider live; the
 * PUT fires on release (pointer/key up) so a 0.1 s-step drag does not storm the
 * backend. The change persists to jarvis.toml and applies live to the running
 * pipeline — no restart (a headless host falls back to "applies on next start").
 */
export function SilenceWindowGroup() {
  const t = useT();
  const { config, loading, setMs } = useSilenceWindow();
  const pushToast = useEventStore((s) => s.pushToast);

  // Local slider value (ms). Mirrors the server value once GET resolves; the
  // label follows it instantly on drag while the PUT waits for commit.
  const [ms, setLocalMs] = useState(1500);
  const [saving, setSaving] = useState(false);
  // The last value we actually committed — guards against an idle mouseUp (no
  // drag) firing a redundant PUT.
  const committedRef = useRef(1500);

  useEffect(() => {
    if (config) {
      setLocalMs(config.ms);
      committedRef.current = config.ms;
    }
  }, [config]);

  const seconds = (ms / 1000).toFixed(1);

  async function commit(next: number) {
    if (next === committedRef.current) return; // no change → no PUT
    committedRef.current = next;
    setSaving(true);
    try {
      const res = await setMs(next);
      pushToast(
        "success",
        t("settings_view.silence_window.saved_toast").replace(
          "{0}",
          `${(res.ms / 1000).toFixed(1)} ${t("settings_view.silence_window.unit_seconds")}`,
        ),
      );
      if (res.restart_required) {
        pushToast("warning", t("settings_view.silence_window.restart_caption"));
      }
    } catch (e) {
      pushToast("error", (e as Error).message);
      // Revert the local value to the last known-good so the UI does not lie.
      setLocalMs(committedRef.current);
    } finally {
      setSaving(false);
    }
  }

  function onReset() {
    const def = config?.default ?? 1500;
    setLocalMs(def);
    void commit(def);
  }

  const showReset = ms !== (config?.default ?? 1500);

  return (
    <div className="mt-2 rounded-lg border border-border bg-card/60 p-4">
      <div className="flex items-start gap-3">
        <Timer className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
        <div className="min-w-0 flex-1">
          <div className="flex items-center justify-between gap-4">
            <h4 className="font-display text-sm font-semibold">
              {t("settings_view.silence_window.title")}
            </h4>
            <span className="font-mono text-sm text-primary">
              {seconds} {t("settings_view.silence_window.unit_seconds")}
            </span>
          </div>
          <p className="mt-1 text-xs text-muted-foreground">
            {t("settings_view.silence_window.description")}
          </p>

          <input
            type="range"
            min={config?.min ?? 500}
            max={config?.max ?? 5000}
            step={100}
            value={ms}
            disabled={loading || saving}
            onChange={(e) => setLocalMs(Number(e.target.value))}
            onMouseUp={() => void commit(ms)}
            onKeyUp={() => void commit(ms)}
            onTouchEnd={() => void commit(ms)}
            className="mt-4 w-full accent-primary disabled:opacity-50"
          />

          {showReset && (
            <button
              type="button"
              onClick={onReset}
              disabled={saving}
              className="mt-3 text-[11px] text-muted-foreground underline hover:text-foreground disabled:opacity-50"
            >
              {t("settings_view.silence_window.reset")}
            </button>
          )}
        </div>
      </div>
    </div>
  );
}
```

- [ ] **Step 5: Wire into SettingsView.** In `jarvis/ui/web/frontend/src/views/SettingsView.tsx`, add the import near the other settings-group imports:

```tsx
import { SilenceWindowGroup } from "@/views/settings/SilenceWindowGroup";
```

and render it right after `<WakeWordPanel />` (the Voice cluster) in the returned JSX:

```tsx
        <WakeWordPanel />
        <SilenceWindowGroup />
```

- [ ] **Step 6: Run the component test + typecheck to verify green**

Run (from `jarvis/ui/web/frontend/`): `npx vitest run src/views/settings/SilenceWindowGroup.test.tsx`
Expected: PASS (3 passed)

Run: `npx tsc --noEmit`
Expected: no new errors.

- [ ] **Step 7: Commit**

```bash
git add jarvis/ui/web/frontend/src/views/settings/SilenceWindowGroup.tsx \
        jarvis/ui/web/frontend/src/views/settings/SilenceWindowGroup.test.tsx \
        jarvis/ui/web/frontend/src/views/SettingsView.tsx \
        jarvis/ui/web/frontend/src/hooks/useSilenceWindow.ts \
        jarvis/ui/web/frontend/src/i18n/locales/en.json \
        jarvis/ui/web/frontend/src/i18n/locales/de.json \
        jarvis/ui/web/frontend/src/i18n/locales/es.json
git commit -m "feat(ui): Thinking-pause slider in Settings (live, reset, i18n)"
```

---

### Task 9: Build, restart, and verify end-to-end (chrome-checkup-loop + voice)

**Files:** none (verification only)

- [ ] **Step 1: Build the frontend so the live app serves the new slider**

Run (from `jarvis/ui/web/frontend/`): `npm run build`
Expected: build succeeds, `jarvis/ui/web/dist` updated.

- [ ] **Step 2: Restart the app so the desktop boot-read + new dist load**

Run: `curl -s -m 10 -X POST http://127.0.0.1:47821/api/settings/restart-app`
Expected: `{"ok":true,"restarting":true}`. Wait for the health endpoint to come back (poll `GET /api/health`).

- [ ] **Step 3: chrome-checkup-loop on the Settings view**

Invoke the `chrome-checkup-loop` skill: open the app, navigate to Settings, locate the "Thinking pause" slider, drag it to e.g. 3.0 s, confirm: no console errors, the PUT to `/api/settings/silence-window` returns 200, the label reads "3.0 s", reload the page and confirm the value persisted, click Reset and confirm it returns to 1.5 s, and the layout is clean. Fix anything it finds and re-run until one clean pass.

- [ ] **Step 4: Real-window voice proof (log evidence)**

Set the slider to 3.0 s, then speak a short utterance with a deliberate pause and stop. Inspect `data/jarvis_desktop.log` for the endpoint line:
Expected: `VAD endpoint: reason=silence ... silence_ms≈2976` (3000 // 32 * 32 = 2976), proving the configured window genuinely governs endpointing — not the old 1472.

- [ ] **Step 5: Full regression of the touched Python suites**

Run: `"/c/Program Files/Python311/python.exe" -m pytest tests/unit/core/test_silence_window_config.py tests/unit/audio/ tests/unit/speech/test_pipeline_silence_window.py tests/integration/test_settings_silence_window.py -q`
Expected: all green.

Run: `"/c/Program Files/Python311/python.exe" -m ruff check jarvis/core/config.py jarvis/core/config_writer.py jarvis/audio/vad.py jarvis/speech/pipeline.py jarvis/ui/desktop_app.py jarvis/ui/web/settings_routes.py`
Expected: clean on touched lines.

- [ ] **Step 6: Final commit (if any verification fixes were made)**

```bash
git add -A
git commit -m "test(silence-window): e2e verification (chrome-checkup + voice log proof)"
```

---

## Notes for the executor

- Use `"/c/Program Files/Python311/python.exe"` for pytest (the Hermes venv `python` has no pytest), per the project memory.
- The working tree is SHARED across parallel sessions. Stage only this feature's files per commit (the exact `git add` lines above), never `git add -A` except in Task 9 Step 6 where it is scoped to verification artifacts you created.
- Do NOT commit/push to any remote unless the maintainer asks — these are local commits only.
- `_patch_table`, `DEFAULT_CONFIG_FILE`, and `resolve_config_path` already exist in their modules (used by every sibling setter); no new imports beyond what each snippet shows.
