"""[brain.worker] vs legacy [brain.sub_jarvis] — one worker tier, one winner.

Both TOML tables populate the SAME Pydantic field (``BrainConfig.worker`` via
``AliasChoices("worker", "sub_jarvis")``), so a file carrying both is a
split-brain: which value won depended on alias ordering, and the live install
shipped ``provider = "antigravity"`` in one table and ``provider =
"openai-codex"`` in the other while the fallback chain rotted in the dead
table. The loader now resolves the conflict explicitly ([brain.worker] wins,
one WARNING names both values) and ``config_writer.migrate_worker_tier_table``
heals the file at boot, rescuing legacy-only keys (the ``fallback_*`` chain)
instead of dropping them.
"""

from __future__ import annotations

import logging
import os
import tomllib
from pathlib import Path

import pytest

import jarvis.core.config_writer as config_writer_module
from jarvis.core.config import load_config
from jarvis.core.config_writer import migrate_worker_tier_table

_REPO_ROOT = Path(__file__).resolve().parents[3]

_BOTH_TABLES = """
[brain.worker]
provider = "openai-codex"
model = "gemini-3.1-pro-preview"

[brain.sub_jarvis]
provider = "antigravity"
model = "claude-opus-4-8"
fallback_provider = "gemini"
fallback_model = "gemini-3.1-pro-preview"
fallback_provider_2 = "gemini"
fallback_model_2 = "gemini-3-pro-preview"
"""


@pytest.fixture(autouse=True)
def _isolated_jarvis_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Strip every JARVIS* env override so the host's registry-persisted
    provider choices cannot leak into these loader tests (env > TOML)."""
    for key in list(os.environ):
        if key.startswith("JARVIS"):
            monkeypatch.delenv(key, raising=False)


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "jarvis.toml"
    path.write_text(body, encoding="utf-8")
    return path


# ---------------------------------------------------------------- loader


def test_only_worker_table_is_read(tmp_path: Path) -> None:
    cfg = load_config(_write(tmp_path, '[brain.worker]\nprovider = "openai-codex"\n'))
    assert cfg.brain.worker is not None
    assert cfg.brain.worker.provider == "openai-codex"


def test_only_legacy_table_still_reads(tmp_path: Path) -> None:
    """Read-compat mandate: pre-rename installs with ONLY [brain.sub_jarvis]
    keep booting (AliasChoices), with no warning noise."""
    cfg = load_config(_write(tmp_path, '[brain.sub_jarvis]\nprovider = "gemini"\n'))
    assert cfg.brain.worker is not None
    assert cfg.brain.worker.provider == "gemini"


def test_both_tables_worker_wins_and_warns(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    with caplog.at_level(logging.WARNING, logger="jarvis.core.config"):
        cfg = load_config(_write(tmp_path, _BOTH_TABLES))
    assert cfg.brain.worker is not None
    assert cfg.brain.worker.provider == "openai-codex"
    joined = " ".join(record.getMessage() for record in caplog.records)
    assert "openai-codex" in joined
    assert "antigravity" in joined


def test_env_override_beats_both_tables(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("JARVIS__BRAIN__WORKER__PROVIDER", "gemini")
    cfg = load_config(_write(tmp_path, _BOTH_TABLES))
    assert cfg.brain.worker is not None
    assert cfg.brain.worker.provider == "gemini"


# ------------------------------------------------------------- migration


def test_migration_merges_legacy_keys_and_drops_table(tmp_path: Path) -> None:
    path = _write(tmp_path, _BOTH_TABLES)
    assert migrate_worker_tier_table(path=path) is True
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "sub_jarvis" not in data["brain"]
    worker = data["brain"]["worker"]
    # Canonical values kept, legacy-only keys (the fallback chain) rescued.
    assert worker["provider"] == "openai-codex"
    assert worker["model"] == "gemini-3.1-pro-preview"
    assert worker["fallback_provider"] == "gemini"
    assert worker["fallback_model"] == "gemini-3.1-pro-preview"
    assert worker["fallback_provider_2"] == "gemini"
    assert worker["fallback_model_2"] == "gemini-3-pro-preview"


def test_migration_renames_when_only_legacy_exists(tmp_path: Path) -> None:
    path = _write(
        tmp_path,
        '[brain.sub_jarvis]\nprovider = "gemini"\nfallback_provider = "openai"\n',
    )
    assert migrate_worker_tier_table(path=path) is True
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    assert "sub_jarvis" not in data["brain"]
    assert data["brain"]["worker"]["provider"] == "gemini"
    assert data["brain"]["worker"]["fallback_provider"] == "openai"


def test_migration_is_noop_without_legacy_table(tmp_path: Path) -> None:
    path = _write(tmp_path, '[brain.worker]\nprovider = "openai-codex"\n')
    before = path.read_text(encoding="utf-8")
    assert migrate_worker_tier_table(path=path) is False
    assert path.read_text(encoding="utf-8") == before


def test_migration_missing_file_is_noop(tmp_path: Path) -> None:
    assert migrate_worker_tier_table(path=tmp_path / "absent.toml") is False


def test_migration_write_failure_is_logged_noop(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    """A drift-guarded / read-only TOML must never break boot — the heal
    degrades to a warning and reports "not migrated"."""
    path = _write(tmp_path, _BOTH_TABLES)

    def _denied(*_args: object, **_kwargs: object) -> None:
        raise PermissionError("locked by drift guard")

    monkeypatch.setattr(config_writer_module, "_atomic_write", _denied)
    with caplog.at_level(logging.WARNING):
        assert migrate_worker_tier_table(path=path) is False
    assert "migration skipped" in " ".join(r.getMessage() for r in caplog.records)


# ------------------------------------------------------------- repo lint


def test_checked_in_example_config_has_no_split_brain() -> None:
    """The shipped example config must never reintroduce both tables."""
    example = _REPO_ROOT / "jarvis.toml.example"
    data = tomllib.loads(example.read_text(encoding="utf-8"))
    brain = data.get("brain", {})
    assert not ("worker" in brain and "sub_jarvis" in brain), (
        "jarvis.toml.example carries BOTH [brain.worker] and [brain.sub_jarvis] — "
        "keep only the canonical [brain.worker] table"
    )
