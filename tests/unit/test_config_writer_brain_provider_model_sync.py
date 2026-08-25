"""Tests for the two-layer ``[brain.providers.<p>]`` model persistence sync.

Per-provider model pins (``model`` / ``deep_model``) are pinned in
config-soll.json under the flat dotted key ``"brain.providers.<p>"``, so a  # i18n-allow
model picked in the per-provider model selector that wrote only the TOML would
be rolled back by the drift-guard within 5 minutes — the same BUG-010 class
that hit ``brain.primary``. ``set_brain_provider_model`` must therefore write:

  1. ``jarvis.toml`` ``[brain.providers.<p>] model/deep_model``      (TOML)
  2. ``scripts/config-soll.json`` ``brain.providers.<p>``            (drift-soll)  # i18n-allow

There is intentionally NO ENV layer: per-provider model keys have no effective
``JARVIS__*`` boot override (the override parser only nests on ``__``, and the
drift-guard's dotted ``JARVIS__BRAIN.PROVIDERS.*`` vars are inert), so adding
one would only create a new stale-override trap.

Layer 2 is best-effort (cloud-first): graceful no-op on a headless VPS, never
raises out of ``set_brain_provider_model``, never breaks the TOML write.

Uses TEMP files + monkeypatch only — never touches the live config.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from jarvis.core import config_writer


@pytest.fixture
def sample_toml(tmp_path: Path) -> Path:
    p = tmp_path / "jarvis.toml"
    p.write_text(
        """\
# Personal Jarvis config
[brain]
primary = "gemini"

[brain.providers.gemini]
model = "gemini-2.5-flash"
deep_model = "gemini-2.5-pro"
""",
        encoding="utf-8",
    )
    return p


@pytest.fixture
def sample_soll(tmp_path: Path) -> Path:  # i18n-allow
    p = tmp_path / "config-soll.json"  # i18n-allow
    p.write_text(
        json.dumps(
            {
                "_comment": "do not lose me",
                "_updated": "2026-06-20",
                "brain": {"primary": "gemini"},
                "brain.providers.gemini": {
                    "model": "gemini-2.5-flash",
                    "deep_model": "gemini-2.5-pro",
                },
                "tts": {"provider": "cartesia"},
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return p


def test_writes_toml_and_soll_no_env(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    env_calls: list[tuple[str, str]] = []
    monkeypatch.setattr(
        config_writer, "_set_user_env_var",
        lambda name, value: env_calls.append((name, value)),
    )

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", deep_model="gemini-3.1-pro-preview",
        path=sample_toml,
    )

    # Layer 1: TOML.
    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'model = "gemini-3.5-flash"' in toml_raw
    assert 'deep_model = "gemini-3.1-pro-preview"' in toml_raw

    # Layer 2: config-soll.json flat "brain.providers.gemini" key.  # i18n-allow
    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.providers.gemini"]["model"] == "gemini-3.5-flash"  # i18n-allow
    assert soll["brain.providers.gemini"]["deep_model"] == "gemini-3.1-pro-preview"  # i18n-allow

    # NO ENV layer for per-provider model keys.
    assert env_calls == []


def test_only_written_keys_are_synced(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """Writing only ``model`` leaves the soll ``deep_model`` untouched."""  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", path=sample_toml,
    )

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.providers.gemini"]["model"] == "gemini-3.5-flash"  # i18n-allow
    # deep_model in the soll block is NOT changed (only model was written).  # i18n-allow
    assert soll["brain.providers.gemini"]["deep_model"] == "gemini-2.5-pro"  # i18n-allow


def test_config_soll_preserves_other_keys(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", path=sample_toml,
    )

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["_comment"] == "do not lose me"  # i18n-allow
    assert soll["brain"]["primary"] == "gemini"  # i18n-allow
    assert soll["tts"]["provider"] == "cartesia"  # i18n-allow


def test_creates_soll_block_if_missing(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """A provider with no soll block yet gets one created (not an error)."""  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "claude-api", deep_model="claude-opus-4-8", path=sample_toml,
    )

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.providers.claude-api"]["deep_model"] == "claude-opus-4-8"  # i18n-allow


def test_missing_config_soll_does_not_break_toml(  # i18n-allow
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    nonexistent = tmp_path / "no-such-config-soll.json"  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: nonexistent)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", path=sample_toml,
    )

    assert 'model = "gemini-3.5-flash"' in sample_toml.read_text(encoding="utf-8")
    assert not nonexistent.exists()


def test_noop_when_both_none(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """Both ``None`` → nothing written anywhere (idempotent early return)."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)
    before_toml = sample_toml.read_text(encoding="utf-8")
    before_soll = sample_soll.read_text(encoding="utf-8")  # i18n-allow

    config_writer.set_brain_provider_model("gemini", path=sample_toml)

    assert sample_toml.read_text(encoding="utf-8") == before_toml
    assert sample_soll.read_text(encoding="utf-8") == before_soll  # i18n-allow


def test_writes_cu_model_to_toml_and_soll(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """Phase 3: the per-provider Computer-Use model persists like model/deep_model."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "gemini", cu_model="gemini-3.1-pro-preview", path=sample_toml,
    )

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'cu_model = "gemini-3.1-pro-preview"' in toml_raw
    # Untouched sibling keys remain.
    assert 'model = "gemini-2.5-flash"' in toml_raw

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.providers.gemini"]["cu_model"] == "gemini-3.1-pro-preview"  # i18n-allow
    # Only cu_model synced — model/deep_model in the soll block stay as they were.  # i18n-allow
    assert soll["brain.providers.gemini"]["model"] == "gemini-2.5-flash"  # i18n-allow
    assert soll["brain.providers.gemini"]["deep_model"] == "gemini-2.5-pro"  # i18n-allow


def test_cu_model_cleared_with_empty_string(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """An empty cu_model writes "" (UI 'use my main model') — distinct from None
    which means 'leave unchanged'."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model("gemini", cu_model="", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'cu_model = ""' in toml_raw


def test_writes_voice_to_toml_and_soll(  # i18n-allow
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """The realtime voice picker persists like model/deep_model/cu_model —
    same nested-table writer, same drift-soll sync."""  # i18n-allow: config-soll ref
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model(
        "openai-realtime", voice="echo", path=sample_toml,
    )

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'voice = "echo"' in toml_raw

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.providers.openai-realtime"]["voice"] == "echo"  # i18n-allow


def test_voice_alone_is_not_a_noop(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """Passing ONLY voice (model/deep_model/cu_model all None) must still
    write — the early-return idempotency guard has to include voice too."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model("gemini-live", voice="Puck", path=sample_toml)

    assert 'voice = "Puck"' in sample_toml.read_text(encoding="utf-8")


def test_voice_cleared_with_empty_string(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    """An empty voice writes "" (UI 'provider default') — distinct from None
    which means 'leave unchanged', mirroring cu_model's contract."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model("openai-realtime", voice="", path=sample_toml)

    assert 'voice = ""' in sample_toml.read_text(encoding="utf-8")


def test_voice_does_not_disturb_sibling_model_key(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    monkeypatch.setattr(config_writer, "_set_user_env_var", lambda name, value: None)

    config_writer.set_brain_provider_model("gemini", voice="Puck", path=sample_toml)

    toml_raw = sample_toml.read_text(encoding="utf-8")
    assert 'voice = "Puck"' in toml_raw
    assert 'model = "gemini-2.5-flash"' in toml_raw  # untouched sibling key

    soll = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert soll["brain.providers.gemini"]["voice"] == "Puck"  # i18n-allow
    assert soll["brain.providers.gemini"]["model"] == "gemini-2.5-flash"  # i18n-allow


# -- WriteReceipt: the writer says when the baseline did not follow --------


def test_receipt_reports_ok_when_the_baseline_followed(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch  # i18n-allow
) -> None:
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    receipt = config_writer.set_brain_provider_model(
        "gemini", model="gemini-3.5-flash", path=sample_toml
    )
    assert isinstance(receipt, config_writer.WriteReceipt)
    assert receipt.toml_ok is True
    assert receipt.baseline_ok is True
    assert receipt.baseline_path == str(sample_soll)  # i18n-allow
    # An empty pick ("Let Jarvis pick") is a real value and must verify too.
    receipt = config_writer.set_brain_provider_model("gemini", model="", path=sample_toml)
    assert receipt.baseline_ok is True
    data = json.loads(sample_soll.read_text(encoding="utf-8"))  # i18n-allow
    assert data["brain.providers.gemini"]["model"] == ""


def test_receipt_flags_a_baseline_that_could_not_be_written(
    sample_toml: Path, sample_soll: Path, monkeypatch: pytest.MonkeyPatch, caplog  # i18n-allow
) -> None:
    """A read-only baseline: the TOML lands, the receipt says baseline_ok=False,
    and the key is named in an ERROR log line."""
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: sample_soll)  # i18n-allow
    before = sample_soll.read_text(encoding="utf-8")  # i18n-allow

    def _refuse(path: Path, content: str) -> None:
        # Stands in for the read-only / locked file on every OS: on Linux a
        # read-only file in a writable directory can still be replaced, so the
        # refusal is simulated deterministically instead of via chmod.
        raise PermissionError(f"{path} is read-only")

    monkeypatch.setattr(config_writer, "_atomic_write_text", _refuse)
    with caplog.at_level("ERROR", logger="jarvis.core.config_writer"):
        receipt = config_writer.set_brain_provider_model(
            "gemini", model="gemini-3.5-flash", path=sample_toml
        )
    assert receipt.toml_ok is True
    assert receipt.baseline_ok is False
    assert "gemini-3.5-flash" in sample_toml.read_text(encoding="utf-8")
    assert sample_soll.read_text(encoding="utf-8") == before  # i18n-allow
    errors = [r.getMessage() for r in caplog.records if r.levelname == "ERROR"]
    assert any("brain.providers.gemini.model" in m for m in errors), errors


def test_receipt_is_ok_without_a_baseline_file(
    sample_toml: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Headless VPS: no config-soll.json means nothing can revert the pick."""  # i18n-allow
    missing = tmp_path / "nope" / "config-soll.json"  # i18n-allow
    monkeypatch.setattr(config_writer, "_config_soll_path", lambda: missing)  # i18n-allow
    receipt = config_writer.set_brain_provider_model(
        "gemini", deep_model="gemini-3.1-pro", path=sample_toml
    )
    assert receipt.baseline_ok is True and receipt.toml_ok is True
