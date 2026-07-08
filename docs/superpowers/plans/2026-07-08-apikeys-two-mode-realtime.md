# Two-Mode API-Keys UI + Gemini Live Provider — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Split the API-Keys screen into Pipeline vs Realtime modes (a view-only segmented switch), and add Gemini Live as a second real realtime provider next to OpenAI Realtime with a key-aware cross-family factory.

**Architecture:** Frontend gains a `VoiceEngineMode` view-state above the existing tab row; the visible tabs derive from the mode. Backend gains a `gemini-live` provider spec + a google-genai Live adapter mirroring `openai_realtime.py`; the realtime factory resolves the active realtime provider by key presence across families. No change to the classic pipeline; the segment switch never writes `[voice].mode`.

**Tech Stack:** React/TS (Vite, vitest), Python 3.11 (FastAPI, pytest), google-genai Live API, openai realtime SDK.

## Global Constraints

- **D1 (BINDING):** the Pipeline|Realtime segment switch is VIEW-ONLY — it MUST NOT write `[voice].mode` or flip the live engine. A test asserts switching fires no voice-mode mutation. The segment matching the current `[voice].mode` shows an "Active" badge (read via the existing `GET /api/settings/voice-mode`).
- **D2:** Realtime mode lists exactly `openai-realtime` + `gemini-live`. OpenRouter is NOT a realtime provider.
- **AP-21/22:** gate realtime on capability/key presence, never a hardcoded provider name; the factory crosses families by key and degrades to `None` (→ pipeline) honestly.
- **AP-26 / §3 cross-OS:** the `google-genai` Live import is LAZY (inside adapter methods only); no-key / no-SDK / headless is a clean logged no-op; base boot unaffected; `google-genai` stays a BASE dep (not behind an extra).
- **Realtime provider modules MUST NOT import `jarvis.*`** beyond `jarvis.core.config.get_provider_secret`, `jarvis.core.protocols`, and `jarvis.realtime.protocol` (mirror `openai_realtime.py`).
- **English-only artifacts;** de/es i18n VALUES are the allowed localized copy; identical key sets across locales.
- **Test interpreter:** `.venv/Scripts/python.exe -m pytest` (Windows shell `python` is the wrong interpreter). Frontend: `cd jarvis/ui/web/frontend && npm run test -- <pattern>` and `npx tsc --noEmit`. NEVER run `npm run build` (empties the live `../dist`).
- **Shared working tree:** commit ONLY your own files by explicit path; NEVER `git add -A`/`.`. Branch `main`, pathspec-scoped, no worktree.

---

### Task 1: Frontend — Pipeline|Realtime segmented view switch

**Files:**
- Modify: `jarvis/ui/web/frontend/src/views/ApiKeysView.tsx`
- Modify: `jarvis/ui/web/frontend/src/i18n/locales/en.json`, `de.json`, `es.json`
- Test: `jarvis/ui/web/frontend/src/views/ApiKeysView.two-mode.test.tsx` (new)

**Interfaces:**
- Consumes: existing `CategoryTabs`, `ProviderCategory`, `SubagentCategory`, `AdvancedCategory`, `useProviders`, `useSectionHealth`, and the existing `useVoiceMode` hook (`GET /api/settings/voice-mode` → `{ mode: "pipeline"|"realtime" }`).
- Produces: a `VoiceEngineMode = "pipeline" | "realtime"` view-state; tab sets `PIPELINE_TABS = ["brain","tts","stt","subagents","advanced"]`, `REALTIME_TABS = ["realtime","subagents","advanced"]`.

- [ ] **Step 1: Write the failing test**

```tsx
// ApiKeysView.two-mode.test.tsx
import { render, screen, fireEvent } from "@testing-library/react";
import { describe, it, expect, vi } from "vitest";
import { ApiKeysView } from "./ApiKeysView";

// Mock the data hooks so the view renders deterministically.
vi.mock("@/hooks/useProviders", () => ({
  useProviders: () => ({ providers: [], loading: false, error: null, refetch: vi.fn(), setActiveOptimistic: vi.fn() }),
  useSectionHealth: () => ({ health: {} }),
}));
const putVoiceMode = vi.fn();
vi.mock("@/hooks/useVoiceMode", () => ({
  useVoiceMode: () => ({ mode: "pipeline", setMode: putVoiceMode, loading: false }),
}));

describe("ApiKeysView two-mode", () => {
  it("defaults to Pipeline mode showing Brain/Voice/Subagents tabs, no Realtime tab", () => {
    render(<ApiKeysView />);
    expect(screen.getByRole("tab", { name: /brain/i })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /realtime/i })).not.toBeInTheDocument();
  });

  it("switching to Realtime mode shows only Realtime/Subagents/Advanced and NEVER writes voice-mode", () => {
    render(<ApiKeysView />);
    fireEvent.click(screen.getByRole("button", { name: /realtime/i })); // the segment
    expect(screen.getByRole("tab", { name: /realtime/i })).toBeInTheDocument();
    expect(screen.queryByRole("tab", { name: /voice output/i })).not.toBeInTheDocument();
    expect(putVoiceMode).not.toHaveBeenCalled(); // D1: view-only
  });
});
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd jarvis/ui/web/frontend && npm run test -- ApiKeysView.two-mode`
Expected: FAIL (no segmented control; Realtime still in the flat row).

- [ ] **Step 3: Implement the two-mode wrapper in `ApiKeysView.tsx`**

Add above the component:
```tsx
type VoiceEngineMode = "pipeline" | "realtime";
const PIPELINE_TABS: CategoryKey[] = ["brain", "tts", "stt", "subagents", "advanced"];
const REALTIME_TABS: CategoryKey[] = ["realtime", "subagents", "advanced"];
```
In `ApiKeysView()`:
- Read the live mode for the badge only: `const { mode: liveMode } = useVoiceMode();`
- `const [engineMode, setEngineMode] = useState<VoiceEngineMode>("pipeline");`
- When `engineMode` changes, reset `active` to the first tab of that mode (`useEffect` on `engineMode` → `setActive(engineMode === "realtime" ? "realtime" : "brain")`).
- Render a segmented control (two `<button>`s, `Pipeline` / `Realtime`) above `<CategoryTabs>`; the button whose value === `liveMode` shows a small `t("apikeys_view.mode_active_badge")` pill. Clicking a segment calls ONLY `setEngineMode(...)` — never `setMode`/any PUT.
- Pass the mode's tab list into `CategoryTabs` (add a `tabs: CategoryKey[]` prop; it currently hardcodes `coreTabs` — derive `coreTabs` from the passed list, keeping `advanced` rendered via its existing separated slot). The main render condition stays: `active` in `("brain","tts","stt","realtime")` → `<ProviderCategory tier={active} .../>`.

- [ ] **Step 4: Add i18n keys (en/de/es)**

`en.json` under `apikeys_view`: `"mode_pipeline": "Pipeline", "mode_realtime": "Realtime", "mode_active_badge": "Active"`.
`de.json`: `"mode_pipeline": "Pipeline", "mode_realtime": "Realtime", "mode_active_badge": "Aktiv"`.
`es.json`: `"mode_pipeline": "Pipeline", "mode_realtime": "Realtime", "mode_active_badge": "Activo"`.

- [ ] **Step 5: Run tests + typecheck**

Run: `cd jarvis/ui/web/frontend && npm run test -- ApiKeysView && npx tsc --noEmit -p tsconfig.json`
Expected: PASS; tsc exit 0. Update any existing `ApiKeysView.*.test.tsx` that assumed a flat Realtime tab (the Realtime tab now lives under Realtime mode).

- [ ] **Step 6: Commit** (`git add -- <the 5 files>` by explicit path; message `feat(frontend): Pipeline|Realtime segmented view switch in API-Keys`).

---

### Task 2: Backend — `gemini-live` provider spec + entry-point

**Files:**
- Modify: `jarvis/ui/web/provider_spec.py` (add to the `PROVIDERS` tuple, in the Realtime section)
- Modify: `pyproject.toml` (`jarvis.realtime` entry-point group)
- Test: `tests/unit/web/test_realtime_provider_category.py` (extend)

**Interfaces:**
- Produces: `ProviderSpec(id="gemini-live", tier="realtime", secret_keys=("gemini_api_key",))`.

- [ ] **Step 1: Write the failing test**

```python
def test_gemini_live_spec_present():
    from jarvis.ui.web.provider_spec import get_spec
    spec = get_spec("gemini-live")
    assert spec is not None
    assert spec.tier == "realtime"
    assert spec.secret_keys == ("gemini_api_key",)
```

- [ ] **Step 2: Run it — FAIL** (`.venv/Scripts/python.exe -m pytest tests/unit/web/test_realtime_provider_category.py::test_gemini_live_spec_present -v`).

- [ ] **Step 3: Add the spec** (after the `openai-realtime` entry in `provider_spec.py`):

```python
    ProviderSpec(
        id="gemini-live",
        label="Gemini Live",
        tier="realtime",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
        credential_help=(
            "Google AI Studio key (AIza/AQ.), shared with the Gemini brain, to "
            "power Google's full-duplex Live realtime voice. Or use the Vertex AI "
            "service-account path for higher quota. Default-OFF until the realtime "
            "client is wired in (Phase 2)."
        ),
        alt_credential=_GEMINI_VERTEX,
    ),
```

- [ ] **Step 4: Add the entry-point** in `pyproject.toml` under `[project.entry-points."jarvis.realtime"]`: `gemini-live = "jarvis.plugins.realtime.gemini_live:GeminiLiveProvider"`. (Task 3 creates that class; this line is inert until then.)

- [ ] **Step 5: Run test — PASS**; also `.venv/Scripts/python.exe -m pytest tests/unit/web/ -q` (no regression). Run `pip install -e . --no-deps` so the new entry-point registers.

- [ ] **Step 6: Commit** (`provider_spec.py`, `pyproject.toml`, the test — explicit paths; `feat(realtime): gemini-live provider spec + entry-point`).

---

### Task 3: Backend — Gemini Live adapter (`gemini_live.py`)

**Files:**
- Create: `jarvis/plugins/realtime/gemini_live.py`
- Test: `tests/contract/test_realtime_provider_contract.py` (extend to cover `gemini-live`) + `tests/unit/realtime/test_gemini_live.py` (new)

**Interfaces:**
- Consumes: `jarvis.realtime.protocol` (`RealtimeEvent`, `RealtimeSessionConfig`), `jarvis.core.protocols.AudioChunk`, `jarvis.core.config.get_provider_secret`.
- Produces: `class GeminiLiveProvider` (`name="gemini-live"`, `supports_realtime=True`, `input_sample_rate=16000`, `output_sample_rate=24000`, `async can_open_duplex_session()`, `async open_session(cfg) -> _GeminiLiveSession`); `_GeminiLiveSession` with the full `RealtimeSession` protocol surface.

**Structural template:** mirror `jarvis/plugins/realtime/openai_realtime.py` exactly (same class shape, same lazy-import discipline, same `RealtimeEvent` mapping). Differences below.

- [ ] **Step 1: Verify the installed google-genai Live surface FIRST**

The Live API method names vary by SDK version. Before writing, confirm against the INSTALLED version: `.venv/Scripts/python.exe -c "import google.genai, inspect; from google.genai import types; print(google.genai.__version__)"` and read the live session methods (`client.aio.live.connect`, the session's `send_realtime_input`/`send`, `receive`, `close`; the message shape: `.data` for audio bytes, `.server_content.output_transcription.text`, `.server_content.input_transcription.text`, `.server_content.interrupted`, `.server_content.turn_complete`). Use context7 (`/googleapis/python-genai`) if the surface is unclear. Record the exact names you found in the module docstring.

- [ ] **Step 2: Write the failing contract/unit test (mocked google-genai — no network)**

```python
# tests/unit/realtime/test_gemini_live.py
import pytest
from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider

@pytest.mark.asyncio
async def test_can_open_duplex_session_reflects_key(monkeypatch):
    monkeypatch.setattr("jarvis.plugins.realtime.gemini_live.get_provider_secret",
                        lambda name: "AIza-test" if name == "gemini" else "")
    assert await GeminiLiveProvider().can_open_duplex_session() is True
    monkeypatch.setattr("jarvis.plugins.realtime.gemini_live.get_provider_secret", lambda name: "")
    assert await GeminiLiveProvider().can_open_duplex_session() is False

def test_provider_shape():
    p = GeminiLiveProvider()
    assert p.name == "gemini-live" and p.supports_realtime is True
    assert p.input_sample_rate == 16000 and p.output_sample_rate == 24000
```
Add a `receive()`-mapping unit test that feeds fake Live messages (a small fake object exposing `.data` / `.server_content`) through `_GeminiLiveSession.receive()` and asserts the yielded `RealtimeEvent` types (`audio_delta` @ 24 kHz, `output_transcript_delta`, `input_transcript` final, `speech_started` on interrupted, `turn_complete`).

- [ ] **Step 3: Run — FAIL** (`.venv/Scripts/python.exe -m pytest tests/unit/realtime/test_gemini_live.py -v`).

- [ ] **Step 4: Implement `gemini_live.py`**

Mirror `openai_realtime.py` with these Gemini specifics (adjust method names to Step 1's findings):
```python
"""Gemini Live realtime provider (google-genai Live API) for jarvis.realtime.

Lazy `from google import genai` inside open_session (AP-26). Must not import
jarvis.* beyond the config secret + protocol types. Live: 16 kHz PCM in / 24 kHz
PCM out — no upsample (mic is already 16 kHz), unlike the OpenAI adapter.
"""
from __future__ import annotations
from collections.abc import AsyncIterator
from typing import Any
from jarvis.core.config import get_provider_secret
from jarvis.core.protocols import AudioChunk
from jarvis.realtime.protocol import RealtimeEvent, RealtimeSessionConfig

_MODEL = "gemini-2.0-flash-live-001"   # verify the current Live model id in Step 1
_INPUT_RATE = 16000
_OUTPUT_RATE = 24000

class _GeminiLiveSession:
    def __init__(self, session: Any, cm: Any, cfg: RealtimeSessionConfig, session_id: str) -> None:
        self._session = session      # the live session
        self._cm = cm                # the async context manager, for close()
        self._cfg = cfg
        self.session_id = session_id

    async def send_audio(self, chunk: AudioChunk) -> None:
        from google.genai import types
        pcm = chunk.pcm  # mic is 16 kHz == _INPUT_RATE; no resample
        await self._session.send_realtime_input(
            audio=types.Blob(data=pcm, mime_type=f"audio/pcm;rate={_INPUT_RATE}")
        )

    async def receive(self) -> AsyncIterator[RealtimeEvent]:
        async for msg in self._session.receive():
            data = getattr(msg, "data", None)
            if data:
                yield RealtimeEvent(type="audio_delta",
                    audio=AudioChunk(pcm=data, sample_rate=_OUTPUT_RATE, timestamp_ns=0))
            sc = getattr(msg, "server_content", None)
            if sc is not None:
                ot = getattr(sc, "output_transcription", None)
                if ot and getattr(ot, "text", None):
                    yield RealtimeEvent(type="output_transcript_delta", text=ot.text)
                it = getattr(sc, "input_transcription", None)
                if it and getattr(it, "text", None):
                    yield RealtimeEvent(type="input_transcript", text=it.text, is_final=True)
                if getattr(sc, "interrupted", False):
                    yield RealtimeEvent(type="speech_started")
                if getattr(sc, "turn_complete", False):
                    yield RealtimeEvent(type="turn_complete")

    async def update_session(self, *, instructions: str | None = None, language: str | None = None) -> None:
        # Gemini Live sets system_instruction at connect; no mid-session update. No-op is honest.
        return None

    async def truncate(self, audio_end_ms: int) -> None:
        return None  # server-side context trim not exposed; barge-in handled by send flow

    async def interrupt(self) -> None:
        return None  # interruption is driven by new input audio (server VAD)

    async def close(self) -> None:
        try:
            await self._cm.__aexit__(None, None, None)
        except Exception:  # noqa: BLE001
            pass

class GeminiLiveProvider:
    name = "gemini-live"
    supports_realtime = True
    input_sample_rate = _INPUT_RATE
    output_sample_rate = _OUTPUT_RATE

    async def can_open_duplex_session(self) -> bool:
        return bool(get_provider_secret("gemini"))

    async def open_session(self, cfg: RealtimeSessionConfig) -> _GeminiLiveSession:
        from google import genai            # lazy (AP-26)
        from google.genai import types
        import uuid
        client = genai.Client(api_key=get_provider_secret("gemini"))
        live_config = types.LiveConnectConfig(
            response_modalities=["AUDIO"],
            system_instruction=cfg.instructions or None,
        )
        cm = client.aio.live.connect(model=_MODEL, config=live_config)
        session = await cm.__aenter__()
        return _GeminiLiveSession(session, cm, cfg, session_id=str(uuid.uuid4()))
```
Adjust the exact `types.*` / method names to Step 1's verified surface. Keep the import lazy.

- [ ] **Step 5: Run — PASS** (`.venv/Scripts/python.exe -m pytest tests/unit/realtime/test_gemini_live.py tests/contract/test_realtime_provider_contract.py -v`) + `ruff check jarvis/plugins/realtime/gemini_live.py`. Add a lazy-import guard test (importing `jarvis.plugins.realtime.gemini_live` must NOT import `google.genai` at module load).

- [ ] **Step 6: Commit** (`gemini_live.py` + tests — explicit paths; `feat(realtime): Gemini Live adapter over google-genai Live API`).

---

### Task 4: Backend — key-aware cross-family realtime factory

**Files:**
- Modify: `jarvis/realtime/factory.py`
- Test: `tests/unit/realtime/test_factory.py` (extend)

**Interfaces:**
- Consumes: `GeminiLiveProvider` (Task 3), `OpenAIRealtimeProvider`, `[brain.realtime].provider`, `get_provider_secret`.
- Produces: `build_realtime_session(...)` that resolves the realtime provider by key across families.

- [ ] **Step 1: Write the failing tests**

```python
# resolution: configured provider wins if keyed; else cross to the other keyed family; else None
def _cfg(mode="realtime", provider="openai-realtime"):
    # build a minimal fake cfg with cfg.voice.mode and cfg.brain.realtime.provider
    ...
@pytest.mark.parametrize("configured,keys,expect", [
    ("openai-realtime", {"openai"}, "openai-realtime"),
    ("gemini-live",     {"gemini"}, "gemini-live"),
    ("openai-realtime", {"gemini"}, "gemini-live"),   # cross-family
    ("gemini-live",     {"openai"}, "openai-realtime"),
    ("openai-realtime", set(),      None),            # neither key → None (pipeline)
])
def test_factory_key_aware_selection(monkeypatch, configured, keys, expect):
    monkeypatch.setattr("jarvis.realtime.factory.get_provider_secret",
                        lambda name: "k" if name in keys else "")
    session = build_realtime_session(cfg=_cfg(provider=configured), bus=None,
                                     session_id="s", send_binary=None, send_json=None)
    if expect is None:
        assert session is None
    else:
        assert session is not None and session_provider_name(session) == expect
```

- [ ] **Step 2: Run — FAIL.**

- [ ] **Step 3: Implement the key-aware resolution** in `factory.py`:

```python
def _resolve_realtime_provider(cfg):
    """Return an instantiated realtime provider by key presence (cross-family,
    AP-22), preferring [brain.realtime].provider; None when no realtime key."""
    from jarvis.plugins.realtime.openai_realtime import OpenAIRealtimeProvider
    from jarvis.plugins.realtime.gemini_live import GeminiLiveProvider
    # (id, secret-name, class) — capability/key-gated, never name-pinned behavior
    FAMILIES = [
        ("openai-realtime", "openai", OpenAIRealtimeProvider),
        ("gemini-live", "gemini", GeminiLiveProvider),
    ]
    configured = getattr(getattr(getattr(cfg, "brain", None), "realtime", None), "provider", "") or "openai-realtime"
    ordered = sorted(FAMILIES, key=lambda f: f[0] != configured)  # configured first
    for _id, secret, cls in ordered:
        if get_provider_secret(secret):
            return cls()
    return None
```
Then in `build_realtime_session`, replace the OpenAI-only block with:
```python
    provider = _resolve_realtime_provider(cfg)
    if provider is None:
        log.info("realtime: no realtime key in any family — classic path")
        return None
    from jarvis.realtime.session import RealtimeVoiceSession
    return RealtimeVoiceSession(session_id=session_id, send_binary=send_binary,
        send_json=send_json, provider=provider, config=cfg, bus=bus)
```
Update the module docstring (drop "OpenAI-only / Phase 4" — cross-family is now here).

- [ ] **Step 4: Run — PASS** (`.venv/Scripts/python.exe -m pytest tests/unit/realtime/ -v`).

- [ ] **Step 5: Commit** (`factory.py` + test — explicit paths; `feat(realtime): key-aware cross-family realtime factory (OpenAI + Gemini)`).

---

## Self-Review

**Spec coverage:** D1 (view-only switch) → Task 1 + its no-mutation test. D2 (only openai+gemini) → Tasks 2/1. D3 (Gemini adapter + key-aware factory) → Tasks 3/4. D4 (cross-OS lazy import) → Task 3 lazy-import guard + `google-genai` base-dep note. All spec sections mapped.

**Placeholder scan:** the one deliberate open item is the exact google-genai Live method/type names (Task 3 Step 1 verifies against the installed SDK — an external surface that varies by version; not a placeholder for OUR logic). All OUR code (frontend, spec, factory) is complete.

**Type consistency:** `RealtimeEvent`/`RealtimeSessionConfig`/`AudioChunk` names match `protocol.py`; `GeminiLiveProvider`/`_GeminiLiveSession` surface matches the `RealtimeProvider`/`RealtimeSession` protocol; `CategoryKey`/tab-list names match `ApiKeysView.tsx`; `build_realtime_session` signature unchanged.
