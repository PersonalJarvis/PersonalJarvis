# Cartesia TTS Integration Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add Cartesia.ai as a selectable TTS provider in Personal Jarvis, surfaced in the desktop app under API Keys → TTS, alongside Gemini Flash TTS and Grok Voice.

**Architecture:** Structural copy of `jarvis/plugins/tts/grok_voice_tts.py` — Bearer-auth unary endpoint (`POST https://api.cartesia.ai/tts/bytes`), sentence-chunking pseudo-streaming, cross-provider fallback to Gemini Flash TTS with optional SAPI5 last resort. The plugin is discovered structurally via `pyproject.toml` entry-point (`jarvis.tts` group), surfaced in the UI via a `ProviderSpec` row, and configured via a three-layer pin (`jarvis.toml` + `scripts/config-soll.json` + ENV doc) to survive BUG-010 drift-guard rewrites.

**Tech Stack:** Python 3.11, `httpx` async client, `pytest` + `pytest-asyncio`, React/TypeScript frontend (Vite + shadcn UI, builds via `npm run build`).

---

## File Structure

| Path | Action | Responsibility |
|---|---|---|
| `jarvis/plugins/tts/cartesia_tts.py` | **Create** | The `CartesiaTTS` provider class. Owns: Cartesia HTTP client, sentence-chunking, Cartesia-specific payload + error mapping, fallback delegation. ~250 LOC. |
| `tests/unit/plugins/tts/test_cartesia_tts.py` | **Create** | Unit tests with mocked `httpx.AsyncClient`. Covers happy path, 401/403/429 cooldown, empty body soft-fail, voice_id validation. |
| `pyproject.toml:149` | **Modify** | Rename entry-point `cartesia-sonic3` → `cartesia`, repoint to new class. |
| `jarvis/ui/web/provider_spec.py:122` | **Modify** | Append `ProviderSpec(id="cartesia", …)` after the Grok-Voice spec so the API Keys view renders a card. |
| `jarvis.toml` | **Modify** | Add `[tts.cartesia]` section with defaults; clean up the stray sonic-3 comment in `[tts]`. |
| `scripts/config-soll.json` | **Modify** | Mirror the `[tts.cartesia]` defaults so the drift-guard daemon doesn't roll them back. |

---

## Task 1: Plugin module — `CartesiaTTS`

**Files:**
- Create: `jarvis/plugins/tts/cartesia_tts.py`

This task introduces the plugin module **before** the entry-point rename, so the import path exists when `pip install -e .` is re-run later.

- [ ] **Step 1.1: Write the plugin module**

```python
"""Cartesia.ai Sonic TTS Plugin (Sonic 3.5, 42 languages incl. German).

POST https://api.cartesia.ai/tts/bytes — Bearer auth, raw PCM s16le 24 kHz mono.
Structurally identical to GrokVoiceTTS: parallel sentence synthesis, fallback
chain Cartesia → Gemini Flash TTS → optional SAPI5, 15-minute cooldown on
401/403/429 quota/auth errors.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from typing import Any

from jarvis.core import config as cfg
from jarvis.core.protocols import AudioChunk
from jarvis.plugins.tts.gemini_flash_tts import (
    SAPI5_SAMPLE_RATE,
    _sapi5_synthesize,
)

CARTESIA_TTS_SAMPLE_RATE = 24_000
CARTESIA_TTS_ENDPOINT = "https://api.cartesia.ai/tts/bytes"
CARTESIA_VERSION = "2026-03-01"
_HTTP_TIMEOUT_S = 30.0
_QUOTA_COOLDOWN_S = 900.0
_MAX_CHARS_PER_REQUEST = 8_000

DEFAULT_MODEL_ID = "sonic-3.5"
# "Sarah" — Cartesia's documented multilingual reference voice.
# User-configurable via [tts.cartesia].voice_id.
DEFAULT_VOICE_ID = "694f9389-aac1-45b6-b726-9d9369183238"

_SENTENCE_END = re.compile(r"(?<=[.!?…])\s+(?=[A-ZÄÖÜ])")


class _CartesiaFatalError(RuntimeError):
    """401/403/429 — triggers cooldown + fallback switch."""


class CartesiaTTS:
    """TTS provider for Cartesia Sonic (api.cartesia.ai/tts/bytes).

    Structurally compatible with the ``TTSProvider`` protocol — no
    inheritance from ``jarvis.*`` (entry_point-discovery pattern).
    """

    name = "cartesia"
    supports_streaming = True  # pseudo via sentence-chunking

    def __init__(
        self,
        model_id: str = DEFAULT_MODEL_ID,
        voice_id: str = DEFAULT_VOICE_ID,
        language: str = "auto",
        chunk_by_sentence: bool = True,
        speed: float = 1.0,
        allow_sapi5_fallback: bool = False,
    ) -> None:
        if not voice_id:
            raise ValueError(
                "CartesiaTTS requires a voice_id. Set [tts.cartesia].voice_id "
                "in jarvis.toml (find UUIDs at https://play.cartesia.ai/voices)."
            )
        self._model_id = model_id
        self._voice_id = voice_id
        self._language = language
        self._chunk_by_sentence = chunk_by_sentence
        self._speed = speed
        self._allow_sapi5_fallback = allow_sapi5_fallback
        self._client: Any = None
        self._quota_blocked_until: float = 0.0

    def _resolve_api_key(self) -> str:
        val = cfg.get_secret("cartesia_api_key", env_fallback="CARTESIA_API_KEY")
        if val:
            return val
        raise RuntimeError(
            "Cartesia API key not found. Set CARTESIA_API_KEY in Windows "
            "Credential Manager or .env (slot: cartesia_api_key)."
        )

    def _ensure_client(self) -> None:
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(
                timeout=_HTTP_TIMEOUT_S,
                headers={
                    "Authorization": f"Bearer {self._resolve_api_key()}",
                    "Cartesia-Version": CARTESIA_VERSION,
                    "Content-Type": "application/json",
                    "Accept": "application/octet-stream",
                },
            )

    async def aclose(self) -> None:
        if self._client is not None:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    async def synthesize(
        self,
        text: str,
        voice: str | None = None,
        language_code: str | None = None,
    ) -> AsyncIterator[AudioChunk]:
        text = text.strip()
        if not text:
            return

        voice_id = voice or self._voice_id
        log = logging.getLogger("jarvis.tts.cartesia")

        if self._quota_blocked_until and time.monotonic() < self._quota_blocked_until:
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        try:
            self._ensure_client()
        except RuntimeError as exc:
            log.warning("Cartesia not initialisable (%s) — falling back.", exc)
            async for chunk in self._fallback(text, language_code):
                yield chunk
            return

        sentences = (
            _split_sentences(text) if self._chunk_by_sentence else [text]
        )
        if not sentences:
            return

        tasks = [
            asyncio.create_task(
                self._synthesize_one(s, voice_id, language_code)
            )
            for s in sentences
        ]
        any_success = False
        for i, task in enumerate(tasks):
            try:
                pcm = await task
            except _CartesiaFatalError as exc:
                self._quota_blocked_until = time.monotonic() + _QUOTA_COOLDOWN_S
                log.warning(
                    "Cartesia quota/auth error (%s) — fallback for %.0f min.",
                    exc, _QUOTA_COOLDOWN_S / 60,
                )
                for t in tasks[i + 1 :]:
                    t.cancel()
                await asyncio.gather(*tasks[i + 1 :], return_exceptions=True)
                remainder = " ".join(sentences[i:])
                async for chunk in self._fallback(remainder, language_code):
                    yield chunk
                return

            if pcm:
                any_success = True
                yield AudioChunk(
                    pcm=pcm,
                    sample_rate=CARTESIA_TTS_SAMPLE_RATE,
                    timestamp_ns=0,
                    channels=1,
                )
            elif self._allow_sapi5_fallback:
                log.warning(
                    "Cartesia empty for sentence %d/%d — SAPI5 emergency on.",
                    i + 1, len(tasks),
                )
                fallback_pcm = await asyncio.to_thread(
                    _sapi5_synthesize, sentences[i], language_code or "de-DE"
                )
                if fallback_pcm:
                    yield AudioChunk(
                        pcm=fallback_pcm,
                        sample_rate=SAPI5_SAMPLE_RATE,
                        timestamp_ns=0,
                        channels=1,
                    )
            else:
                log.error(
                    "Cartesia returned no audio for sentence %d/%d (%r). "
                    "SAPI5 emergency disabled — staying silent for this segment.",
                    i + 1, len(tasks), sentences[i][:80],
                )

        if not any_success:
            log.error("Cartesia produced no audio at all — cross-provider fallback.")
            async for chunk in self._fallback(text, language_code):
                yield chunk

    def list_voices(self, language: str | None = None) -> list[str]:
        return [self._voice_id]

    async def _synthesize_one(
        self,
        text: str,
        voice_id: str,
        language_code: str | None,
    ) -> bytes:
        log = logging.getLogger("jarvis.tts.cartesia")
        text = text[:_MAX_CHARS_PER_REQUEST]

        payload: dict[str, Any] = {
            "model_id": self._model_id,
            "transcript": text,
            "voice": {"mode": "id", "id": voice_id},
            "output_format": {
                "container": "raw",
                "encoding": "pcm_s16le",
                "sample_rate": CARTESIA_TTS_SAMPLE_RATE,
            },
            "language": _normalize_language(language_code or self._language),
        }
        if self._speed != 1.0:
            payload["generation_config"] = {"speed": self._speed}

        assert self._client is not None
        try:
            resp = await self._client.post(CARTESIA_TTS_ENDPOINT, json=payload)
        except Exception as exc:  # noqa: BLE001
            log.warning("Cartesia HTTP error (%s) — soft-fail.", exc.__class__.__name__)
            return b""

        if resp.status_code in (401, 403, 429):
            raise _CartesiaFatalError(f"HTTP {resp.status_code}")
        if resp.status_code >= 400:
            body = resp.text[:200] if resp.text else "<empty>"
            log.warning(
                "Cartesia HTTP %d — voice=%s text=%r body=%s",
                resp.status_code, voice_id, text[:80], body,
            )
            return b""

        data = resp.content
        if not data:
            log.warning("Cartesia 200 OK but empty body — voice=%s", voice_id)
            return b""
        return data

    async def _fallback(
        self, text: str, language_code: str | None
    ) -> AsyncIterator[AudioChunk]:
        log = logging.getLogger("jarvis.tts.cartesia")
        try:
            from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

            gemini = GeminiFlashTTS(
                language_code=language_code or "de-DE",
                allow_sapi5_fallback=self._allow_sapi5_fallback,
            )
            async for chunk in gemini.synthesize(text, language_code=language_code):
                yield chunk
            return
        except Exception as exc:  # noqa: BLE001
            log.error(
                "Gemini fallback after Cartesia error also failed (%s).",
                exc.__class__.__name__, exc_info=True,
            )

        if not self._allow_sapi5_fallback:
            log.error(
                "Both Cartesia and Gemini TTS produced no audio. "
                "SAPI5 emergency disabled — staying silent. "
                "Set tts.allow_sapi5_fallback=true if Windows TTS is an "
                "acceptable last resort."
            )
            return

        log.warning("SAPI5 emergency active (config opt-in).")
        pcm = await asyncio.to_thread(
            _sapi5_synthesize, text, language_code or "de-DE"
        )
        if pcm:
            yield AudioChunk(
                pcm=pcm,
                sample_rate=SAPI5_SAMPLE_RATE,
                timestamp_ns=0,
                channels=1,
            )


def _split_sentences(text: str) -> list[str]:
    parts = _SENTENCE_END.split(text)
    return [p.strip() for p in parts if p.strip()]


def _normalize_language(code: str | None) -> str:
    """Cartesia accepts ISO-639-1 codes ('de', 'en') or 'auto'."""
    if not code:
        return "auto"
    low = code.lower().strip()
    if low in ("auto", "automatic", ""):
        return "auto"
    return low.split("-", 1)[0]
```

- [ ] **Step 1.2: Commit the module**

```bash
git add jarvis/plugins/tts/cartesia_tts.py
git commit -m "feat(tts): add Cartesia Sonic 3.5 plugin

Structurally identical to GrokVoiceTTS — Bearer auth, unary /tts/bytes
endpoint, sentence-chunking pseudo-stream, fallback chain Cartesia → Gemini
→ optional SAPI5. Voice/model configurable via [tts.cartesia] in jarvis.toml.
"
```

---

## Task 2: Unit tests

**Files:**
- Create: `tests/unit/plugins/tts/test_cartesia_tts.py`

- [ ] **Step 2.1: Write the failing tests**

```python
"""Unit tests for CartesiaTTS — mocked httpx, no live API calls."""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from jarvis.plugins.tts.cartesia_tts import (
    CARTESIA_TTS_SAMPLE_RATE,
    CartesiaTTS,
    _CartesiaFatalError,
)


# ----- Fixtures ----- #

class _Resp:
    def __init__(self, status_code: int, content: bytes = b"", text: str = ""):
        self.status_code = status_code
        self.content = content
        self.text = text


@pytest.fixture
def patched_secret():
    with patch(
        "jarvis.plugins.tts.cartesia_tts.cfg.get_secret",
        return_value="sk_car_test_key",
    ):
        yield


@pytest.fixture
def tts(patched_secret) -> CartesiaTTS:
    return CartesiaTTS(
        voice_id="11111111-2222-3333-4444-555555555555",
        chunk_by_sentence=True,
        allow_sapi5_fallback=False,
    )


# ----- Tests ----- #

@pytest.mark.asyncio
async def test_synthesize_yields_pcm_24k_mono(tts):
    """Happy path: one sentence → one AudioChunk at 24kHz mono."""
    fake_pcm = b"\x00\x01" * 12_000  # 24k samples worth of placeholder
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(200, content=fake_pcm))
    tts._client = mock_client

    chunks = [c async for c in tts.synthesize("Hallo Welt.")]

    assert len(chunks) == 1
    assert chunks[0].pcm == fake_pcm
    assert chunks[0].sample_rate == CARTESIA_TTS_SAMPLE_RATE
    assert chunks[0].channels == 1


@pytest.mark.asyncio
async def test_multiple_sentences_yield_in_order(tts):
    """Three sentences → three chunks in source order despite parallel synth."""
    bodies = [b"AAA", b"BBB", b"CCC"]
    call_count = {"i": 0}

    async def fake_post(url: str, json: dict[str, Any]) -> _Resp:
        idx = call_count["i"]
        call_count["i"] += 1
        await asyncio.sleep(0)  # let parallel tasks interleave
        return _Resp(200, content=bodies[idx])

    mock_client = AsyncMock()
    mock_client.post = fake_post  # type: ignore[assignment]
    tts._client = mock_client

    chunks = [
        c async for c in tts.synthesize("Erste. Zweite. Dritte.")
    ]
    payloads = [c.pcm for c in chunks]
    # Order is preserved by index, even with parallel execution.
    assert payloads == [b"AAA", b"BBB", b"CCC"]


@pytest.mark.asyncio
async def test_401_triggers_cooldown_and_falls_back(tts):
    """401 → fatal → cooldown set, remainder yielded from fallback."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(401, text="unauthorized"))
    tts._client = mock_client

    async def fake_fallback(text: str, lang: str | None):
        from jarvis.core.protocols import AudioChunk

        yield AudioChunk(pcm=b"FALLBACK", sample_rate=24_000, timestamp_ns=0, channels=1)

    with patch.object(tts, "_fallback", side_effect=fake_fallback):
        chunks = [c async for c in tts.synthesize("Test.")]

    assert any(c.pcm == b"FALLBACK" for c in chunks)
    assert tts._quota_blocked_until > 0


@pytest.mark.asyncio
async def test_429_triggers_cooldown_and_falls_back(tts):
    """429 → same cooldown branch as 401."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(429, text="rate limited"))
    tts._client = mock_client

    async def fake_fallback(text: str, lang: str | None):
        from jarvis.core.protocols import AudioChunk

        yield AudioChunk(pcm=b"FALLBACK", sample_rate=24_000, timestamp_ns=0, channels=1)

    with patch.object(tts, "_fallback", side_effect=fake_fallback):
        chunks = [c async for c in tts.synthesize("Test.")]

    assert chunks and chunks[0].pcm == b"FALLBACK"
    assert tts._quota_blocked_until > 0


@pytest.mark.asyncio
async def test_empty_body_does_not_raise(tts):
    """200 OK + empty body → soft-fail, cross-provider fallback kicks in."""
    mock_client = AsyncMock()
    mock_client.post = AsyncMock(return_value=_Resp(200, content=b""))
    tts._client = mock_client

    async def fake_fallback(text: str, lang: str | None):
        from jarvis.core.protocols import AudioChunk

        yield AudioChunk(pcm=b"FB", sample_rate=24_000, timestamp_ns=0, channels=1)

    with patch.object(tts, "_fallback", side_effect=fake_fallback):
        chunks = [c async for c in tts.synthesize("Hallo.")]

    # Empty body → no Cartesia chunk; cross-provider fallback fills in.
    assert chunks and chunks[0].pcm == b"FB"


def test_missing_voice_id_raises_at_construction(patched_secret):
    with pytest.raises(ValueError, match="voice_id"):
        CartesiaTTS(voice_id="")


def test_list_voices_returns_configured_id(tts):
    assert tts.list_voices() == ["11111111-2222-3333-4444-555555555555"]
```

- [ ] **Step 2.2: Run tests, expect PASS (module already written in Task 1)**

```bash
pytest tests/unit/plugins/tts/test_cartesia_tts.py -v
```

Expected: 7 passed.

- [ ] **Step 2.3: Commit**

```bash
git add tests/unit/plugins/tts/test_cartesia_tts.py
git commit -m "test(tts): unit tests for CartesiaTTS

Mocked httpx covers: PCM yield, sentence ordering, 401/429 cooldown,
empty-body soft-fail, missing voice_id raises, list_voices.
"
```

---

## Task 3: Rename entry-point + register `ProviderSpec`

**Files:**
- Modify: `pyproject.toml:149`
- Modify: `jarvis/ui/web/provider_spec.py` (append after Grok-Voice spec at line 122)

- [ ] **Step 3.1: Rename the entry-point**

In `pyproject.toml`, replace the line

```toml
cartesia-sonic3 = "jarvis.plugins.tts.cartesia_sonic3:CartesiaSonic3TTS"
```

with

```toml
cartesia = "jarvis.plugins.tts.cartesia_tts:CartesiaTTS"
```

- [ ] **Step 3.2: Append the `ProviderSpec` row**

In `jarvis/ui/web/provider_spec.py`, after the `grok-voice` spec (between lines 121 and 122, before the STT comment block), add:

```python
    ProviderSpec(
        id="cartesia",
        label="Cartesia Sonic 3.5",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("cartesia_api_key",),
        dashboard_url="https://play.cartesia.ai/keys",
    ),
```

- [ ] **Step 3.3: Refresh editable install (entry-points hook)**

Run from the repo root:

```bash
pip install -e . --no-deps
```

Expected: `Successfully installed jarvis-…`. Without this, the new entry-point is invisible to `python -m jarvis --plugins` (BUG-006 lesson).

- [ ] **Step 3.4: Verify the plugin is discoverable**

```bash
python -m jarvis --plugins | grep -i cartesia
```

Expected: a line listing `cartesia` in the `jarvis.tts` group.

- [ ] **Step 3.5: Commit**

```bash
git add pyproject.toml jarvis/ui/web/provider_spec.py
git commit -m "feat(tts): wire Cartesia entry-point + UI ProviderSpec

Rename entry-point cartesia-sonic3 → cartesia (decouples plugin identity
from model generation). Register ProviderSpec so the desktop app's API
Keys view renders a Cartesia card under the TTS tier.
"
```

---

## Task 4: Three-layer config pin

**Files:**
- Modify: `jarvis.toml` (insert `[tts.cartesia]` section after `[tts]`)
- Modify: `scripts/config-soll.json` (mirror the keys)

- [ ] **Step 4.1: Clean up the stray Sonic-3 comment in `[tts]`**

In `jarvis.toml`, replace lines 100-102

```toml
# Sonic-3 voice_id leer-lassen wenn nicht aktiv. Bei aktivem cartesia-sonic3
# muss voice_id gesetzt sense, sonst raises der Plugin-Konstruktor.
voice_id = ""
```

with

```toml
# Generic voice_id slot — used by some legacy TTS providers; Cartesia
# reads its voice_id from [tts.cartesia].voice_id instead. Leave empty.
voice_id = ""
```

- [ ] **Step 4.2: Append the `[tts.cartesia]` section**

At the end of the `[tts]` block in `jarvis.toml` (before the `[brain]` header on the line that currently starts `# Brain: Multi-Provider …`), add:

```toml
# ── Cartesia.ai Sonic TTS ─────────────────────────────────────────────
# Selectable in the desktop app under API Keys → TTS. Sonic 3.5 supports
# 42 languages including German. Endpoint: POST api.cartesia.ai/tts/bytes,
# returns raw pcm_s16le @ 24 kHz mono — same format as Gemini/Grok.
# ENV overrides (drift-guard-safe):
#   JARVIS__TTS__CARTESIA__MODEL_ID
#   JARVIS__TTS__CARTESIA__VOICE_ID
[tts.cartesia]
model_id = "sonic-3.5"
# Default voice = Cartesia's documented multilingual reference voice ("Sarah").
# Replace with any UUID from https://play.cartesia.ai/voices.
voice_id = "694f9389-aac1-45b6-b726-9d9369183238"
language = "auto"
chunk_by_sentence = true
speed = 1.0
allow_sapi5_fallback = false
```

- [ ] **Step 4.3: Mirror to drift-guard target**

Open `scripts/config-soll.json`. Locate the top-level `tts` object and add a `cartesia` child mirroring the same keys:

```json
"tts": {
  "...existing keys...": "...",
  "cartesia": {
    "model_id": "sonic-3.5",
    "voice_id": "694f9389-aac1-45b6-b726-9d9369183238",
    "language": "auto",
    "chunk_by_sentence": true,
    "speed": 1.0,
    "allow_sapi5_fallback": false
  }
}
```

(Use the actual `tts` object as-is; only insert the new `cartesia` child. Don't touch sibling keys.)

- [ ] **Step 4.4: Verify the TOML parses and Pydantic accepts the extras**

```bash
python -c "from jarvis.core.config import load_config; c = load_config(); print(c.tts.model_dump().get('cartesia'))"
```

Expected: a dict with `model_id`, `voice_id`, `language`, etc. (TTSConfig has `model_config={'extra':'allow'}`, so this Just Works without a new Pydantic class.)

- [ ] **Step 4.5: Commit**

```bash
git add jarvis.toml scripts/config-soll.json
git commit -m "config(tts): three-layer pin for [tts.cartesia]

Adds the Cartesia provider config to jarvis.toml plus its drift-guard
mirror in scripts/config-soll.json (BUG-010 defense). Default model
sonic-3.5, default voice Sarah (UUID 694f9389-...). Replaces the stale
sonic-3 comment in [tts].
"
```

---

## Task 5: Frontend build + verification screenshot

**Files:**
- (no source edits — pure build + smoke)

- [ ] **Step 5.1: Re-build the React frontend**

```bash
npm --prefix jarvis/ui/web/frontend install
npm --prefix jarvis/ui/web/frontend run build
```

Expected: clean `dist/` under `jarvis/ui/web/dist`. If `npm install` was already up to date, the second command is the only one that matters.

- [ ] **Step 5.2: Run the API Keys smoke test against the backend**

Start the headless backend (no Tk windows, no voice):

```bash
JARVIS_VOICE=0 python -m jarvis.ui.web.launcher --headless --no-lock &
sleep 6
curl -s http://127.0.0.1:8765/api/providers | python -c "import sys,json; d=json.load(sys.stdin); print([p for p in d['providers'] if p['id']=='cartesia'])"
```

Expected: a one-element list with `tier: tts`, `auth_mode: api_key`, `secret_keys: ['cartesia_api_key']`. Then `kill %1`.

- [ ] **Step 5.3: Launch the full desktop app and screenshot**

```bash
JARVIS_VOICE=0 python -m jarvis.ui.web.launcher --no-lock
```

Once the window is up, navigate to **API Keys** in the sidebar. Take a screenshot showing the Cartesia card under the TTS section. Save the screenshot to:

```
<USER_HOME>\Downloads\cartesia-tts\cartesia-api-keys-card.png
```

- [ ] **Step 5.4: Commit (no code changes; this task is verification-only)**

No commit needed unless the screenshot is checked in. The screenshot lives in `Downloads\cartesia-tts\` per the `feedback_save_to_downloads` user mandate.

---

## Self-review summary

**Spec coverage:**
- §3 (API contract) → Task 1 implements every documented field.
- §4 AD-CT1 (plugin name) → Task 3 step 3.1.
- §4 AD-CT2 (dedicated secret) → Task 3 step 3.2 (`cartesia_api_key`).
- §4 AD-CT3 (pseudo-streaming first) → Task 1 (sentence-chunking, no SSE).
- §4 AD-CT4 (three-layer pin) → Task 4.
- §4 AD-CT5 (fallback chain) → Task 1 `_fallback`.
- §4 AD-CT6 (voice_id configurable) → Task 1 constructor + Task 4 config.
- §8 acceptance criteria → all reflected in Tasks 3.4, 4.4, 5.1-5.3.

**Placeholder scan:** none. Every code block is complete; every command is concrete.

**Type consistency:** `CartesiaTTS`, `_CartesiaFatalError`, `CARTESIA_TTS_SAMPLE_RATE`, `DEFAULT_VOICE_ID` are defined in Task 1 and reused verbatim in Task 2.

**Risk:** Task 5 launches the desktop app, which requires the dev's monitor to be active for the screenshot. If running purely headless, the alternative is to skip Step 5.3 and rely on the `/api/providers` JSON proof in Step 5.2 plus a screenshot of an inline HTML render — but the user's success criterion explicitly demands the screenshot of the UI card, so this risk is accepted.
