"""Unit tests for GeminiFlashTTS voice-consistency knobs.

Background (2026-05-24): the user reported the TTS voice drifting constantly —
different prosody between the pre-answer ("bin dran") and the main answer,
shifts mid-sentence, and day-to-day changes. Root cause: Gemini Flash TTS is a
generative model invoked *per sentence* with no determinism control (no seed,
no temperature) in ``_build_config``. Each sentence is an independent neural
generation, so a single answer is stitched from several differently-voiced
takes, and identical phrases re-render differently across runs.

Fix levers, all config-driven:
    - ``chunk_by_sentence=False`` -> whole utterance is ONE generation -> one
      coherent voice performance, no mid-answer jumps.
    - fixed ``seed``               -> identical text renders identically run-to-run.
    - lowered ``temperature``      -> less sampling variance in delivery.

These tests lock the wiring in place so a future refactor can't silently drop
the determinism config and reopen the drift.
"""
from __future__ import annotations

import pytest

from jarvis.plugins.tts import build_tts_from_config
from jarvis.plugins.tts.gemini_flash_tts import GeminiFlashTTS

# --- _build_config carries the determinism knobs ----------------------------

def test_build_config_sets_seed_and_temperature() -> None:
    """seed + temperature must reach the GenerateContentConfig the API sees."""
    tts = GeminiFlashTTS(seed=7, temperature=0.7)
    cfg = tts._build_config("Charon")
    assert cfg.seed == 7
    assert cfg.temperature == 0.7
    # Voice must still be wired through.
    voice_name = cfg.speech_config.voice_config.prebuilt_voice_config.voice_name
    assert voice_name == "Charon"


def test_build_config_defaults_leave_seed_temperature_unset() -> None:
    """Backwards-compat: without the knobs nothing is forced onto the API."""
    tts = GeminiFlashTTS()
    cfg = tts._build_config("Charon")
    assert cfg.seed is None
    assert cfg.temperature is None


# --- chunk_by_sentence=False -> single generation ---------------------------

@pytest.mark.asyncio
async def test_chunk_disabled_synthesizes_whole_text_once() -> None:
    """With chunking off, a multi-sentence answer is ONE generation call —
    the structural guarantee that the whole answer shares one voice take."""
    tts = GeminiFlashTTS(chunk_by_sentence=False)
    tts._ensure_client = lambda: None  # type: ignore[assignment]

    seen: list[str] = []

    async def fake_one(text: str, voice: str, language_code: str | None = None) -> bytes:
        seen.append(text)
        return b"PCM"

    tts._synthesize_one = fake_one  # type: ignore[assignment]

    chunks = [c async for c in tts.synthesize("Hallo. Wie geht es dir? Gut.")]
    assert len(seen) == 1
    assert seen[0] == "Hallo. Wie geht es dir? Gut."
    assert len(chunks) == 1


@pytest.mark.asyncio
async def test_chunk_enabled_still_splits_per_sentence() -> None:
    """Regression guard: the old per-sentence path stays intact when enabled."""
    tts = GeminiFlashTTS(chunk_by_sentence=True)
    tts._ensure_client = lambda: None  # type: ignore[assignment]

    seen: list[str] = []

    async def fake_one(text: str, voice: str, language_code: str | None = None) -> bytes:
        seen.append(text)
        return b"PCM"

    tts._synthesize_one = fake_one  # type: ignore[assignment]

    _ = [c async for c in tts.synthesize("Hallo. Wie geht es dir? Gut.")]
    assert len(seen) >= 2  # multiple sentences -> multiple generations


# --- build_tts_from_config propagates the knobs -----------------------------

class _FakeTTSCfg:
    """Minimal stand-in for TTSConfig with the consistency fields."""
    provider = "gemini-flash-tts"
    model = "gemini-3.1-flash-tts-preview"
    voice_de = "Charon"
    language_code = "de-DE"
    style_prompt = ""
    allow_sapi5_fallback = False
    chunk_by_sentence = False
    seed = 7
    temperature = 0.7


def test_factory_propagates_consistency_knobs() -> None:
    tts = build_tts_from_config(_FakeTTSCfg())
    assert isinstance(tts, GeminiFlashTTS)
    assert tts._chunk_by_sentence is False
    assert tts._seed == 7
    assert tts._temperature == 0.7


# --- Vertex AI path (2026-05-26) --------------------------------------------
# Background: AI Studio enforces a 100-RPD cap on gemini-3.1-flash-tts-preview
# that is independent of Pay-as-you-go billing. When the cap is hit, the
# plugin's Sibling-Bridge switches to gemini-2.5-flash-preview-tts and the
# perceived Charon voice shifts mid-session. Vertex AI on a billed project
# does not have the Preview RPD cap, so use_vertex=True keeps the voice on
# the same model day-to-day and removes the bridge-switch trigger entirely.


def test_vertex_constructor_stores_project_and_location() -> None:
    tts = GeminiFlashTTS(
        use_vertex=True,
        vertex_project="my-gcp-project",
        vertex_location="europe-west4",
        service_account_path="/tmp/sa.json",
    )
    assert tts._use_vertex is True
    assert tts._vertex_project == "my-gcp-project"
    assert tts._vertex_location == "europe-west4"
    assert tts._service_account_path == "/tmp/sa.json"


def test_vertex_ensure_client_raises_when_project_missing() -> None:
    """Misconfiguration must fail loudly, not silently fall onto AI Studio."""
    tts = GeminiFlashTTS(use_vertex=True, vertex_project=None)
    import pytest as _pytest
    with _pytest.raises(RuntimeError, match="vertex_project"):
        tts._ensure_client()


def test_vertex_ensure_client_builds_vertex_client(monkeypatch) -> None:
    """When use_vertex=True the client is built with vertexai=True + project/
    location, NOT with an api_key. The AI-Studio env keys are stripped so the
    SDK cannot accidentally route to AI Studio."""
    monkeypatch.setenv("GOOGLE_API_KEY", "should-be-stripped")
    monkeypatch.setenv("GEMINI_API_KEY", "should-be-stripped")
    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeGenAI:
        Client = _FakeClient

    import sys
    monkeypatch.setitem(sys.modules, "google.genai", _FakeGenAI())
    # The plugin imports as ``from google import genai`` -> we need the
    # ``google`` namespace to expose .genai. Patch via setattr.
    import google  # type: ignore
    monkeypatch.setattr(google, "genai", _FakeGenAI(), raising=False)

    tts = GeminiFlashTTS(
        use_vertex=True,
        vertex_project="proj-x",
        vertex_location="us-central1",
        service_account_path="/path/to/sa.json",
    )
    tts._ensure_client()

    assert captured.get("vertexai") is True
    assert captured.get("project") == "proj-x"
    assert captured.get("location") == "us-central1"
    assert "api_key" not in captured
    # AI-Studio keys must be cleared from env so the SDK cannot re-route.
    import os
    assert "GOOGLE_API_KEY" not in os.environ
    assert "GEMINI_API_KEY" not in os.environ
    # GOOGLE_APPLICATION_CREDENTIALS must point at the configured SA file.
    assert os.environ.get("GOOGLE_APPLICATION_CREDENTIALS") == "/path/to/sa.json"


class _FakeVertexTTSCfg(_FakeTTSCfg):
    use_vertex = True
    vertex_project = "vx-project"
    vertex_location = "europe-west4"
    service_account_path = "/etc/jarvis/sa.json"


def test_factory_propagates_vertex_knobs() -> None:
    tts = build_tts_from_config(_FakeVertexTTSCfg())
    assert isinstance(tts, GeminiFlashTTS)
    assert tts._use_vertex is True
    assert tts._vertex_project == "vx-project"
    assert tts._vertex_location == "europe-west4"
    assert tts._service_account_path == "/etc/jarvis/sa.json"


def test_factory_vertex_defaults_off_for_legacy_cfg() -> None:
    """Legacy TTSConfig shapes (without the vertex fields) must still produce
    an AI-Studio plugin — defaults preserve the historical behaviour."""
    tts = build_tts_from_config(_FakeTTSCfg())
    assert tts._use_vertex is False
    assert tts._vertex_project is None
    assert tts._vertex_location == "us-central1"  # harmless default


def test_vertex_ensure_client_derives_project_from_sa_when_empty(monkeypatch, tmp_path) -> None:
    """When vertex_project is empty (the JARVIS__TTS__VERTEX_PROJECT env
    override was never set after a clean clone), the project must be derived
    from the `project_id` field of the service-account JSON we already load.

    This is the BUG fix for the silent-TTS regression: use_vertex=True with an
    empty vertex_project used to raise RuntimeError on every sentence, leaving
    Jarvis mute ("hears + thinks but never answers")."""
    import json

    sa_path = tmp_path / "vertex-sa.json"
    sa_path.write_text(
        json.dumps({"type": "service_account", "project_id": "derived-proj-123"}),
        encoding="utf-8",
    )

    captured: dict = {}

    class _FakeClient:
        def __init__(self, **kwargs):
            captured.update(kwargs)

    class _FakeGenAI:
        Client = _FakeClient

    import google  # type: ignore
    monkeypatch.setattr(google, "genai", _FakeGenAI(), raising=False)

    tts = GeminiFlashTTS(
        use_vertex=True,
        vertex_project="",  # empty — the env override was missing
        vertex_location="us-central1",
        service_account_path=str(sa_path),
    )
    tts._ensure_client()

    assert captured.get("project") == "derived-proj-123"
    assert tts._vertex_project == "derived-proj-123"


def test_vertex_ensure_client_raises_when_project_empty_and_sa_lacks_project_id(
    monkeypatch, tmp_path
) -> None:
    """If the SA file cannot supply a project_id either, fail loudly rather
    than building a Vertex client with no project."""
    import json

    sa_path = tmp_path / "vertex-sa.json"
    sa_path.write_text(json.dumps({"type": "service_account"}), encoding="utf-8")

    tts = GeminiFlashTTS(
        use_vertex=True,
        vertex_project="",
        service_account_path=str(sa_path),
    )
    import pytest as _pytest

    with _pytest.raises(RuntimeError, match="vertex_project"):
        tts._ensure_client()


def test_vertex_ensure_client_expands_tilde_in_sa_path(monkeypatch, tmp_path) -> None:
    """Config can carry the cross-platform convention path ~/.config/jarvis/...
    The plugin must expand ~ so Google's auth chain (which does not expand
    tilde itself) receives an absolute, OS-correct path."""
    # Point HOME at a temp dir so the test is hermetic on Windows and POSIX.
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))

    class _FakeClient:
        def __init__(self, **kwargs):
            pass

    class _FakeGenAI:
        Client = _FakeClient

    import google  # type: ignore
    monkeypatch.setattr(google, "genai", _FakeGenAI(), raising=False)

    tts = GeminiFlashTTS(
        use_vertex=True,
        vertex_project="proj-x",
        service_account_path="~/.config/jarvis/vertex-sa.json",
    )
    tts._ensure_client()

    import os
    resolved = os.environ["GOOGLE_APPLICATION_CREDENTIALS"]
    # Tilde must be gone.
    assert "~" not in resolved
    # Resolution must land under the test home dir, not at the literal "~/".
    assert str(tmp_path) in resolved
    assert resolved.endswith("vertex-sa.json")
