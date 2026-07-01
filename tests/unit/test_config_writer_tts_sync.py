"""Tests for the three-layer ``[tts] provider`` persistence sync.

When the user switches the TTS provider in the desktop app, the choice must
persist across a restart. ``config-soll.json`` pins the whole ``tts`` block  # i18n-allow
(``provider`` + ``voice_de`` / ``voice_en`` / ``language_code`` ...), so a UI
switch that wrote only the TOML would be rolled back by the drift-guard within
5 minutes — exactly the BUG that hit ``brain.primary`` before it became
3-layer. The TTS switch must therefore write ALL THREE layers:

  1. ``jarvis.toml`` ``[tts] provider`` (+ provider-dependent voice/lang/model)
  2. ``scripts/config-soll.json`` ``tts.*`` (drift-guard soll values)  # i18n-allow
  3. ``JARVIS__TTS__PROVIDER`` User-scope ENV var (boot override, winreg)

Crucially, layer 2 must mirror EVERY key the TOML write touched — not just
``provider`` — otherwise the guard would keep the new provider but revert the
voice to the old provider's (incompatible) value.

Layers 2 + 3 are best-effort (cloud-first): graceful no-op on a headless VPS,
never raise out of ``set_tts_provider``, never break the TOML write.

Uses TEMP files + monkeypatch only — never touches the live config.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

from jarvis.core import config_writer


@pytest.fixture
def sample_toml(tmp_path: Path) -> Path:
    p = tmp_path / "jarvis.toml"
    p.write_text(
        """\
# Personal Jarvis config
[tts]
provider = "gemini-flash-tts"
voice_de = "Charon"
voice_en = "Charon"
language_code = "de-DE"
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_soll(tmp_path: Path) -> Path:  # i18n-allow
    """A config-soll.json skeleton that pins the whole tts block."""  # i18n-allow
    p = tmp_path / "config-soll.json"  # i18n-allow
    p.write_text(
        json.dumps(
            {
                "_comment": "do not lose me",
                "_updated": "2026-05-29",
                "brain": {"primary": "gemini"},
                "tts": {
                    "provider": "gemini-flash-tts",
                    "fallback": "grok-voice",
                    "voice_de": "Charon",
                    "voice_en": "Charon",
                    "language_code": "auto",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


def test_set_tts_provider_writes_all_three_layers(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """The UI switch must write TOML + config-soll.json + ENV (winreg)."""  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer,
        "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_tts_provider("grok-voice", path=sample_toml)

    # Layer 1: TOML.
    assert 'provider = "grok-voice"' in sample_toml.read_text(encoding="utf-8")

    # Layer 2: config-soll.json tts.provider.  # i18n-allow
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["tts"]["provider"] == "grok-voice"  # i18n-allow

    # Layer 3: ENV setter called with the canonical var name + value.
    assert env_calls == [("JARVIS__TTS__PROVIDER", "grok-voice")]


def test_config_soll_mirrors_provider_dependent_voice(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """The drift-soll must mirror the SAME voice the TOML write applied.  # i18n-allow

    Switching gemini -> grok-voice rewrites voice_de/voice_en to a grok voice
    in the TOML. If config-soll kept the old "Charon" pin, the drift-guard  # i18n-allow
    would revert the voice to a value invalid for grok. So config-soll must end  # i18n-allow
    up with the grok voice too — zero drift across the whole tts block.
    """
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_tts_provider("grok-voice", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow

    # Whatever voice the TOML write picked, config-soll agrees with it.  # i18n-allow
    assert 'voice_de = "leo"' in toml_raw
    assert soll["tts"]["voice_de"] == "leo"  # i18n-allow
    assert soll["tts"]["voice_en"] == "leo"  # i18n-allow


def test_config_soll_mirrors_preserved_valid_voice(  # i18n-allow
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A valid custom voice that the TOML write PRESERVES must still be mirrored
    into config-soll — otherwise the drift-guard reverts the user's choice.  # i18n-allow

    Scenario: the user hand-set ``voice_de = "Orus"`` (a valid Gemini voice) and
    switches to / stays on gemini-flash-tts. ``_patch_tts_block`` keeps "Orus"
    in the TOML (user override wins). But config-soll still pins "Charon" — so  # i18n-allow
    the guard would revert "Orus" -> "Charon" within 5 minutes. config-soll must  # i18n-allow
    end up agreeing with the preserved TOML value.
    """
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        '[tts]\nprovider = "gemini-flash-tts"\nvoice_de = "Orus"\nvoice_en = "Orus"\n',
        encoding="utf-8",
    )
    soll = tmp_path / "config-soll.json"  # i18n-allow
    soll.write_text(  # i18n-allow
        json.dumps(
            {"tts": {"provider": "gemini-flash-tts", "voice_de": "Charon", "voice_en": "Charon"}},
            indent=2,
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_tts_provider("gemini-flash-tts", path=toml)

    # TOML keeps the user's valid voice...
    assert 'voice_de = "Orus"' in toml.read_text(encoding="utf-8")
    # ...and config-soll now agrees with it (no Charon revert).  # i18n-allow
    data = json.loads(soll.read_text(encoding="utf-8"))  # i18n-allow
    assert data["tts"]["voice_de"] == "Orus"
    assert data["tts"]["voice_en"] == "Orus"


def test_pluginless_provider_does_not_blank_voice(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Switching to a tier=tts provider that has no plugin (empty voice defaults,
    no whitelist entry) must NOT write voice_de="" / voice_en="" into the TOML.

    google-neural2 / openai-tts carry empty-string voice defaults in
    _TTS_DEFAULTS and have no _VOICES_FOR_PROVIDER whitelist entry. Without a
    falsy-guard on the generic write path, _patch_tts_block would blank the
    carried-over voice — corrupting the config and making the factory silently
    fall back. Empty values must be skipped, leaving the prior voice intact.
    """
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        '[tts]\nprovider = "gemini-flash-tts"\nvoice_de = "Charon"\nvoice_en = "Charon"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: tmp_path / "nope.json")  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_tts_provider("openai-tts", path=toml)

    raw = toml.read_text(encoding="utf-8")
    assert 'provider = "openai-tts"' in raw
    # The empty-string voice defaults must NOT be written.
    assert 'voice_de = ""' not in raw
    assert 'voice_en = ""' not in raw
    # The carried-over voice is preserved untouched.
    assert 'voice_de = "Charon"' in raw


def test_config_soll_sync_preserves_other_keys(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """Only the tts.* keys the write touched change; everything else stays."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_tts_provider("grok-voice", path=sample_toml)

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["_comment"] == "do not lose me"  # i18n-allow
    assert soll["brain"]["primary"] == "gemini"  # i18n-allow
    # The fallback key inside tts is not touched by the provider switch.
    assert soll["tts"]["fallback"] == "grok-voice"  # i18n-allow


def test_updates_live_os_environ(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", lambda name, value: None)
    monkeypatch.delenv("JARVIS__TTS__PROVIDER", raising=False)

    config_writer.set_tts_provider("grok-voice", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__TTS__PROVIDER") == "grok-voice"


def test_missing_config_soll_does_not_break_toml(  # i18n-allow
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "no-such-config-soll.json"  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_tts_provider("grok-voice", path=sample_toml)

    assert 'provider = "grok-voice"' in sample_toml.read_text(encoding="utf-8")
    assert not nonexistent.exists()


def test_soll_sync_swallows_write_errors(  # i18n-allow
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A broken config-soll.json (invalid JSON) must not break the TOML write."""  # i18n-allow
    broken = tmp_path / "config-soll.json"  # i18n-allow
    broken.write_text("{ this is not valid json ", encoding="utf-8")
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: broken)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_tts_provider("grok-voice", path=sample_toml)

    assert 'provider = "grok-voice"' in sample_toml.read_text(encoding="utf-8")


def test_winreg_skipped_on_non_win32(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(sys, "platform", "linux")

    def _boom(name: str, value: str) -> None:  # pragma: no cover - guard
        raise AssertionError("registry write attempted on non-win32 platform")

    monkeypatch.setattr(config_writer, "_set_user_env_var_winreg", _boom)
    monkeypatch.delenv("JARVIS__TTS__PROVIDER", raising=False)

    config_writer.set_tts_provider("grok-voice", path=sample_toml)

    import os

    assert os.environ.get("JARVIS__TTS__PROVIDER") == "grok-voice"


def test_raises_on_missing_toml(
    sample_soll: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    # Production now auto-creates a missing TOML instead of raising
    # FileNotFoundError (_ensure_writable_config_path, headless-VPS fix).
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    p = tmp_path / "nope.toml"
    assert not p.exists()
    config_writer.set_tts_provider("grok-voice", path=p)
    assert p.exists(), "set_tts_provider must auto-create a missing config file"
    assert 'provider = "grok-voice"' in p.read_text(encoding="utf-8")


def test_set_tts_provider_cartesia_writes_all_layers_and_preserves_subtable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """set_tts_provider("cartesia") writes all three persistence layers and
    leaves the pre-existing [tts.cartesia] subtable and config-soll _comment  # i18n-allow
    untouched.

    Regression guard for the cartesia entry added to _TTS_DEFAULTS: before that
    entry existed, switching to cartesia produced an empty defaults dict, leaving
    the gemini model name in [tts].model and syncing only "provider" to
    config-soll (so voice_de/voice_en stayed pinned to "Charon" from the old  # i18n-allow
    provider — in this case that is correct for cartesia, but the sync must still
    happen so the guard agrees with the TOML).
    """
    # TOML that starts on gemini-flash-tts and has a [tts.cartesia] subtable.
    toml = tmp_path / "jarvis.toml"
    toml.write_text(
        """\
# Personal Jarvis config
[tts]
provider = "gemini-flash-tts"
voice_de = "Charon"
voice_en = "Charon"
language_code = "de-DE"
model = "gemini-3.1-flash-tts-preview"

[tts.cartesia]
model_id = "sonic-3.5"
voice_id = "47c38ca4-5f35-497b-b1a3-415245fb35e1"
voice_id_de = "b7187e84-fe22-4344-ba4a-bc013fcb533e"
""",
        encoding="utf-8",
    )

    # config-soll with a _comment and a tts.cartesia sub-table that must be  # i18n-allow
    # preserved unchanged.
    soll = tmp_path / "config-soll.json"  # i18n-allow
    soll.write_text(  # i18n-allow
        json.dumps(
            {
                "_comment": "do not lose me",
                "tts": {
                    "provider": "gemini-flash-tts",
                    "voice_de": "Charon",
                    "voice_en": "Charon",
                    "language_code": "de-DE",
                },
                "tts.cartesia": {
                    "model_id": "sonic-3.5",
                    "voice_id": "47c38ca4-5f35-497b-b1a3-415245fb35e1",
                },
            },
            indent=2,
        ),
        encoding="utf-8",
    )

    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: soll)  # i18n-allow
    monkeypatch.setattr(
        config_writer,
        "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_tts_provider("cartesia", path=toml)

    # Layer 1: TOML provider is "cartesia".
    toml_text = toml.read_text(encoding="utf-8")
    assert 'provider = "cartesia"' in toml_text

    # The [tts.cartesia] subtable must survive the write untouched.
    assert "sonic-3.5" in toml_text
    assert "b7187e84-fe22-4344-ba4a-bc013fcb533e" in toml_text

    # Layer 2: config-soll tts.provider == "cartesia".  # i18n-allow
    soll_data = json.loads(soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll_data["tts"]["provider"] == "cartesia"  # i18n-allow

    # The _comment and tts.cartesia sub-table in config-soll are untouched.  # i18n-allow
    assert soll_data["_comment"] == "do not lose me"  # i18n-allow
    assert soll_data["tts.cartesia"]["model_id"] == "sonic-3.5"  # i18n-allow

    # Layer 3: ENV setter called with the canonical var + "cartesia".
    assert env_calls == [("JARVIS__TTS__PROVIDER", "cartesia")]
