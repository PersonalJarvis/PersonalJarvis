"""Language-aware wake routing: the fix for the cross-language deaf-wake class.

A Vosk model is acoustically language-SPECIFIC — an English model cannot hear a
German-spoken name even when the word is in its lexicon (live 2026-07-09:
'Hey Ruben' spoken de on the en model free-decoded to 'hey of whom' and every
verify suppressed). So ``resolve_wake_plan`` must trust vosk_kws ONLY when its
language provably matches the speaker, and prefer the multilingual
open-vocabulary stt_match path under an ambiguous "auto" language when local
Whisper is available. These tests pin that contract so the regression cannot
silently return.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from jarvis.speech.wake_phrase import resolve_wake_plan


def _cfg(phrase: str = "Hey Ruben", engine: str = "auto", custom: str = "") -> SimpleNamespace:
    return SimpleNamespace(
        phrase=phrase,
        engine=engine,
        custom_model_path=custom,
        sensitivity=0.5,
        fuzzy_match_ratio=0.8,
    )


@pytest.fixture()
def de_model(tmp_path, monkeypatch) -> Path:
    """Only a GERMAN vosk model on disk (mirrors the user's real box)."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    model = tmp_path / "wake_models" / "vosk" / "de" / "vosk-model-small-de-0.15"
    (model / "am").mkdir(parents=True)
    return model


@pytest.fixture()
def en_model(tmp_path, monkeypatch) -> Path:
    """Only an ENGLISH vosk model on disk."""
    monkeypatch.setenv("JARVIS__MEMORY__DATA_DIR", str(tmp_path))
    model = tmp_path / "wake_models" / "vosk" / "en" / "vosk-model-small-en-us-0.15"
    (model / "am").mkdir(parents=True)
    return model


# --------------------------------------------------------------------------
# The core regression: ambiguous language must NOT gamble on a mismatched model
# --------------------------------------------------------------------------


@pytest.mark.parametrize("language", ["auto", None])
def test_ambiguous_language_with_whisper_prefers_multilingual_stt(en_model, language):
    # engine=auto + language ambiguous + local Whisper present: the first-installed
    # (en) model might not match the speaker, so DO NOT gamble on it — route to the
    # multilingual open-vocabulary stt_match path instead.
    plan = resolve_wake_plan(
        _cfg(),
        local_whisper_available=True,
        language=language,
        vosk_available=True,
    )
    assert plan.engine == "stt_match"
    assert plan.wake_available is True


@pytest.mark.parametrize("language", ["auto", None])
def test_ambiguous_language_without_whisper_falls_back_to_vosk_best_effort(en_model, language):
    # No multilingual Whisper to fall back on → vosk (first-installed) is the only
    # local option, so use it as an honest best effort rather than going deaf.
    plan = resolve_wake_plan(
        _cfg(),
        local_whisper_available=False,
        language=language,
        vosk_available=True,
    )
    assert plan.engine == "vosk_kws"


def test_concrete_matching_language_uses_fast_vosk(de_model):
    # A German speaker with the German model installed gets the fast CPU vosk path.
    plan = resolve_wake_plan(
        _cfg(),
        local_whisper_available=True,
        language="de",
        vosk_available=True,
    )
    assert plan.engine == "vosk_kws"
    assert plan.vosk_model_path == str(de_model)


def test_concrete_mismatched_language_avoids_wrong_vosk_model(de_model):
    # The 'Hey Ruben' bug in miniature: the user's resolved language is English but
    # ONLY a German model is on disk. resolve_vosk_model_path("en") → None, so we
    # must NOT use the German model — route to multilingual stt_match instead.
    plan = resolve_wake_plan(
        _cfg(),
        local_whisper_available=True,
        language="en",
        vosk_available=True,
    )
    assert plan.engine == "stt_match"


def test_explicit_vosk_engine_still_honours_first_installed(en_model):
    # An explicit engine="vosk_kws" is a user override — honour it against whatever
    # model is installed even under an ambiguous language (no gamble guard applies).
    plan = resolve_wake_plan(
        _cfg(engine="vosk_kws"),
        local_whisper_available=True,
        language="auto",
        vosk_available=True,
    )
    assert plan.engine == "vosk_kws"


def test_mismatched_language_no_whisper_is_honest_wake_off(de_model):
    # English speaker, only a German model, and no local Whisper: there is no
    # honest way to hear the English word → wake OFF, never a silent dead listener.
    plan = resolve_wake_plan(
        _cfg(),
        local_whisper_available=False,
        language="en",
        vosk_available=True,
    )
    assert plan.engine == "none"
    assert plan.wake_available is False


# --------------------------------------------------------------------------
# resolve_wake_language cascade (wake pin → stt → ui → default) — one SoT
# everywhere
# --------------------------------------------------------------------------


def test_resolve_wake_language_cascade():
    from jarvis.speech.wake_model_fetch import resolve_wake_language

    # the explicit wake-word pin wins over everything (the 2026-07-21
    # decoupling: the wake language must never follow the app language)
    assert resolve_wake_language(
        SimpleNamespace(
            trigger=SimpleNamespace(wake_word=SimpleNamespace(language="de")),
            stt=SimpleNamespace(language="en"),
            ui=SimpleNamespace(language="en"),
        )
    ) == "de"
    # stt.language concrete wins while the pin is "auto"
    assert resolve_wake_language(
        SimpleNamespace(stt=SimpleNamespace(language="de"), ui=SimpleNamespace(language="en"))
    ) == "de"
    # stt "auto" → fall to ui.language
    assert resolve_wake_language(
        SimpleNamespace(stt=SimpleNamespace(language="auto"), ui=SimpleNamespace(language="es"))
    ) == "es"
    # nothing concrete → default 'en'
    assert resolve_wake_language(
        SimpleNamespace(stt=SimpleNamespace(language="auto"), ui=SimpleNamespace(language="auto"))
    ) == "en"


# --------------------------------------------------------------------------
# OOV guard: deterministic branches (a real model check needs a downloaded model)
# --------------------------------------------------------------------------


def test_oov_guard_empty_phrase_is_unsupported():
    from jarvis.plugins.wake.vosk_kws_provider import vosk_model_supports_phrase

    assert vosk_model_supports_phrase("/nonexistent/model", "") is False


def test_oov_guard_fails_open_on_bad_model_path():
    # A probe that cannot even load the model must NEVER reject a real word.
    from jarvis.plugins.wake.vosk_kws_provider import vosk_model_supports_phrase

    assert vosk_model_supports_phrase("/definitely/not/a/model", "Hey Ruben") is True


# --------------------------------------------------------------------------
# CPU-first default: the GPU wake hot-swap is OFF unless explicitly opted in
# --------------------------------------------------------------------------


def test_wake_high_accuracy_defaults_to_cpu():
    # Guards the 2026-07-09 CPU-first flip: the sticky GPU probe caches made a
    # once-fast wake go permanently deaf after a restart, so GPU wake is opt-in.
    from jarvis.core.config import STTConfig

    assert STTConfig().wake_high_accuracy is False


# --------------------------------------------------------------------------
# Anti-drift: the STT/wake language list must agree Python ↔ TS (BUG-008 class)
# --------------------------------------------------------------------------


async def test_the_stt_language_list_has_exactly_one_source():
    # This used to compare the Python accepted set against a hand-mirrored TS
    # union. That mirror is gone on purpose: once recognition widened from
    # auto/de/en/es to ~100 languages, a copied union was no longer a guard but
    # the drift trap itself (AP-4) — the day a language is added to one side the
    # other silently rejects it. The list is now shipped over the wire and the
    # UI hydrates from it, so the thing worth pinning is that ONE source exists.
    #
    # Half one: the backend actually hands out the whole accepted set, because a
    # client that cannot fetch the options has no way to build a correct picker.
    from jarvis.ui.web.settings_routes import _STT_LANGUAGES, get_stt_language

    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=None)))
    body = await get_stt_language(request)  # type: ignore[arg-type]
    assert body["options"] == list(_STT_LANGUAGES), (
        "GET /api/settings/stt-language must ship the full accepted set — it is "
        "the only place the UI can learn which languages are selectable."
    )
    assert len(_STT_LANGUAGES) >= 50, "the recogniser list collapsed back to a handful"


def test_the_frontend_never_re_mirrors_the_stt_language_list():
    # Half two: the copy must not come back. `SttLanguage` stays a plain string
    # and the only hardcoded list is the one-entry placeholder rendered before
    # the fetch resolves — anything longer is a second source of truth.
    import re

    ts_path = (
        Path(__file__).resolve().parents[3]
        / "jarvis" / "ui" / "web" / "frontend" / "src" / "i18n" / "index.ts"
    )
    text = ts_path.read_text(encoding="utf-8")

    m = re.search(r"export type SttLanguage\s*=\s*([^;]+);", text)
    assert m, "SttLanguage type not found in i18n/index.ts"
    declared = m.group(1).strip()
    assert declared == "string", (
        f"SttLanguage is {declared!r} — a union of codes here is a hand-mirrored "
        "copy of settings_routes._STT_LANGUAGES and will drift (AP-4). The "
        "accepted set is fetched from the backend instead."
    )

    fallback = re.search(r"STT_FALLBACK_OPTIONS[^=]*=\s*\[([^\]]*)\]", text)
    assert fallback, "STT_FALLBACK_OPTIONS placeholder not found in i18n/index.ts"
    codes = re.findall(r"[\"']([a-z-]+)[\"']", fallback.group(1))
    assert codes == ["auto"], (
        f"the pre-fetch placeholder grew into a real list ({codes}) — it is one "
        "render's worth of fallback, not a second source of truth."
    )

    assert "body.options" in text, (
        "hydrateSttLanguage must consume the options the backend ships, or the "
        "picker silently falls back to the placeholder forever."
    )


def test_wake_language_list_parity_python_ts():
    # The wake-word language is its OWN setting (decoupled from the app display
    # language and the STT recognition language, 2026-07-21). The Settings
    # dropdown (SettingsView WAKE_LANGUAGES) deliberately omits "auto" — every
    # concrete code it offers must be accepted by the backend route
    # (settings_routes._WAKE_LANGUAGES), and both must stay within the models
    # the fetcher can actually provision (VOSK_MODELS).
    import re

    from jarvis.speech.wake_model_fetch import VOSK_MODELS
    from jarvis.ui.web.settings_routes import _WAKE_LANGUAGES

    ts_path = (
        Path(__file__).resolve().parents[3]
        / "jarvis" / "ui" / "web" / "frontend" / "src" / "views" / "SettingsView.tsx"
    )
    text = ts_path.read_text(encoding="utf-8")
    m = re.search(r"const WAKE_LANGUAGES: WakeLanguage\[\]\s*=\s*\[([^\]]+)\]", text)
    assert m, "WAKE_LANGUAGES const not found in SettingsView.tsx"
    ts_langs = set(re.findall(r"[\"']([a-z]+)[\"']", m.group(1)))
    assert ts_langs == set(_WAKE_LANGUAGES) - {"auto"}, (
        f"TS {ts_langs} != Python {set(_WAKE_LANGUAGES) - {'auto'}} — keep the wake "
        "language list in lockstep (settings_routes._WAKE_LANGUAGES ↔ "
        "SettingsView WAKE_LANGUAGES)."
    )
    assert set(_WAKE_LANGUAGES) - {"auto"} == set(VOSK_MODELS), (
        "every pinnable wake language needs a provisionable Vosk model"
    )
