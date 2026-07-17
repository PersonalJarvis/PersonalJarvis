"""Unit tests for ``jarvis.setup.obsidian.register_vault`` (Phase B9.3).

Pure-Python tests that NEVER touch the real ``%APPDATA%``; every JSON
write happens under pytest's ``tmp_path``. Each test simulates a single
slice of the register pipeline (config-missing, idempotent, happy-path,
dry-run, backup collision, write failure, UUID format, unicode round-
trip, post-write verification failure, unknown-key preservation).
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from jarvis.setup import obsidian as mod
from jarvis.setup.obsidian import (
    RegisterResult,
    register_vault,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
def _write_cfg(cfg_path: Path, payload: dict) -> None:
    """Persist ``payload`` as UTF-8 JSON at ``cfg_path`` (no BOM)."""
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(json.dumps(payload), encoding="utf-8")


# ---------------------------------------------------------------------------
# (a) missing config → bootstrap
# ---------------------------------------------------------------------------
def test_register_vault_bootstraps_missing_config(tmp_path: Path) -> None:
    """No obsidian.json on disk (Obsidian never launched) → the register
    click creates a fresh index containing the vault, status=added."""
    cfg = tmp_path / "obsidian" / "obsidian.json"  # dir + file do not exist
    vault = tmp_path / "Vault"
    vault.mkdir()

    result = register_vault(vault, config_path=cfg)

    assert isinstance(result, RegisterResult)
    assert result.status == "added"
    assert result.vault_uuid is not None
    # A bootstrapped config has no original to back up.
    assert result.backup_path is None
    data = json.loads(cfg.read_text(encoding="utf-8"))
    entry = data["vaults"][result.vault_uuid]
    assert Path(entry["path"]).resolve() == vault.resolve()
    assert entry["open"] is False
    # Registering again is idempotent against the bootstrapped file.
    second = register_vault(vault, config_path=cfg)
    assert second.status == "already_registered"


def test_register_vault_missing_config_dry_run_touches_nothing(
    tmp_path: Path,
) -> None:
    """dry_run with a missing obsidian.json previews success without
    creating the file (parity with the existing-config dry-run contract)."""
    cfg = tmp_path / "obsidian" / "obsidian.json"
    vault = tmp_path / "Vault"
    vault.mkdir()

    result = register_vault(vault, config_path=cfg, dry_run=True)

    assert result.status == "added"
    assert result.vault_uuid is not None
    assert not cfg.exists()
    assert not cfg.parent.exists()


def test_register_vault_bootstrap_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed bootstrap write reports rolled_back and leaves no file."""
    cfg = tmp_path / "obsidian" / "obsidian.json"
    vault = tmp_path / "Vault"
    vault.mkdir()

    def _boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr(mod.json, "dump", _boom)

    result = register_vault(vault, config_path=cfg)

    assert result.status == "rolled_back"
    assert result.error is not None
    assert "disk full" in result.error
    assert not cfg.exists()


# ---------------------------------------------------------------------------
# (b) already_registered
# ---------------------------------------------------------------------------
def test_register_vault_already_registered(tmp_path: Path) -> None:
    """Vault path already in obsidian.json → status=already_registered."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "Jarvis Vault"
    vault.mkdir()
    _write_cfg(
        cfg,
        {
            "vaults": {
                "abc1234567890abc": {
                    "path": str(vault.resolve()),
                    "ts": 1778597706074,
                    "open": False,
                }
            }
        },
    )
    original_bytes = cfg.read_bytes()

    result = register_vault(vault, config_path=cfg)

    assert result.status == "already_registered"
    assert result.vault_uuid is None
    assert result.backup_path is None
    # No backup created, file unchanged.
    assert cfg.read_bytes() == original_bytes
    siblings = list(tmp_path.glob("*.b9-backup-*"))
    assert siblings == []


# ---------------------------------------------------------------------------
# (c) happy path
# ---------------------------------------------------------------------------
def test_register_vault_happy_path(tmp_path: Path) -> None:
    """Empty vaults → status=added, JSON contains new entry, backup exists."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "My Vault"
    vault.mkdir()
    original_payload = {"vaults": {}}
    _write_cfg(cfg, original_payload)
    original_bytes = cfg.read_bytes()

    result = register_vault(vault, config_path=cfg)

    assert result.status == "added"
    assert result.vault_uuid is not None
    assert result.backup_path is not None
    assert result.backup_path.exists()
    # Backup matches the original file byte-for-byte.
    assert result.backup_path.read_bytes() == original_bytes

    # New JSON contains our vault under the new UUID.
    new_data = json.loads(cfg.read_text(encoding="utf-8"))
    assert result.vault_uuid in new_data["vaults"]
    entry = new_data["vaults"][result.vault_uuid]
    assert Path(entry["path"]).resolve() == vault.resolve()
    assert entry["open"] is False
    assert isinstance(entry["ts"], int)
    assert entry["ts"] > 0


# ---------------------------------------------------------------------------
# (d) dry_run
# ---------------------------------------------------------------------------
def test_register_vault_dry_run(tmp_path: Path) -> None:
    """dry_run=True → status=added but no disk mutation, no backup."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "Vault"
    vault.mkdir()
    _write_cfg(cfg, {"vaults": {}})
    original_bytes = cfg.read_bytes()

    result = register_vault(vault, config_path=cfg, dry_run=True)

    assert result.status == "added"
    assert result.vault_uuid is not None
    assert result.backup_path is None
    # File contents unchanged byte-for-byte.
    assert cfg.read_bytes() == original_bytes
    # No backup files anywhere in the dir.
    siblings = list(tmp_path.glob("*.b9-backup-*"))
    assert siblings == []


# ---------------------------------------------------------------------------
# (e) backup name collision
# ---------------------------------------------------------------------------
def test_register_vault_backup_name_collision(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Pre-existing backup with the canonical name → new backup gets ``-1`` suffix."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "Vault"
    vault.mkdir()
    _write_cfg(cfg, {"vaults": {}})

    # Force the datetime stamp to a deterministic value so we can craft the collision.
    class _FixedDatetime:
        @classmethod
        def now(cls):  # type: ignore[no-untyped-def]
            class _D:
                @staticmethod
                def strftime(fmt: str) -> str:
                    return "20260514-120000"

            return _D()

    # Patch the `datetime` symbol inside the helper's local import. Since
    # ``_next_backup_path`` does ``from datetime import datetime`` at
    # call time, we patch the stdlib module's attribute.
    import datetime as _real_dt

    monkeypatch.setattr(_real_dt, "datetime", _FixedDatetime)

    expected_base = cfg.with_name(f"{cfg.name}.b9-backup-20260514-120000")
    expected_base.write_text("pre-existing", encoding="utf-8")

    result = register_vault(vault, config_path=cfg)

    assert result.status == "added"
    assert result.backup_path is not None
    assert result.backup_path != expected_base
    assert result.backup_path.name.endswith("-1")
    # The pre-existing file is untouched (we did not overwrite it).
    assert expected_base.read_text(encoding="utf-8") == "pre-existing"


# ---------------------------------------------------------------------------
# (f) write failure → rollback
# ---------------------------------------------------------------------------
def test_register_vault_write_failure_rolls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """json.dump raises mid-write → status=rolled_back, file unchanged."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "Vault"
    vault.mkdir()
    _write_cfg(cfg, {"vaults": {}})
    original_bytes = cfg.read_bytes()

    def _boom(*_a, **_kw):  # type: ignore[no-untyped-def]
        raise OSError("disk full")

    monkeypatch.setattr(mod.json, "dump", _boom)

    result = register_vault(vault, config_path=cfg)

    assert result.status == "rolled_back"
    assert result.error is not None
    assert "disk full" in result.error
    # File restored from backup (or never touched at this stage).
    assert cfg.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# (g) UUID format
# ---------------------------------------------------------------------------
def test_register_vault_uuid_format(tmp_path: Path) -> None:
    """vault_uuid matches Obsidian's 16-lowercase-hex format."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "Vault"
    vault.mkdir()
    _write_cfg(cfg, {"vaults": {}})

    result = register_vault(vault, config_path=cfg)

    assert result.status == "added"
    assert result.vault_uuid is not None
    assert re.match(r"^[0-9a-f]{16}$", result.vault_uuid), result.vault_uuid


# ---------------------------------------------------------------------------
# (h) unicode path preserved (ensure_ascii=False)
# ---------------------------------------------------------------------------
def test_register_vault_unicode_path_preserved(tmp_path: Path) -> None:
    """German umlauts in vault_path survive the JSON round-trip verbatim."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "Müller Vault"  # i18n-allow: umlaut test data — the test verifies German characters round-trip
    vault.mkdir()
    _write_cfg(cfg, {"vaults": {}})

    result = register_vault(vault, config_path=cfg)

    assert result.status == "added"
    raw_text = cfg.read_text(encoding="utf-8")
    # Verbatim umlaut byte sequence in the file (not ü escape).  # i18n-allow: references the literal umlaut character under test
    assert "Müller" in raw_text  # i18n-allow: matches the umlaut test data above


# ---------------------------------------------------------------------------
# (i) post-write verification failure → rollback
# ---------------------------------------------------------------------------
def test_register_vault_post_write_verification_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """is_vault_registered=False after write → status=rolled_back, file restored."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "Vault"
    vault.mkdir()
    _write_cfg(cfg, {"vaults": {}})
    original_bytes = cfg.read_bytes()

    real_is_registered = mod.is_vault_registered
    call_count = {"n": 0}

    def _flaky_is_registered(vaults, expected):  # type: ignore[no-untyped-def]
        # First call (pre-write idempotency check): use real logic.
        # Subsequent call(s) (post-write verify): force False.
        call_count["n"] += 1
        if call_count["n"] == 1:
            return real_is_registered(vaults, expected)
        return False

    monkeypatch.setattr(mod, "is_vault_registered", _flaky_is_registered)

    result = register_vault(vault, config_path=cfg)

    assert result.status == "rolled_back"
    assert result.error is not None
    assert "post-write" in result.error
    # File restored from backup.
    assert cfg.read_bytes() == original_bytes


# ---------------------------------------------------------------------------
# (j) unknown top-level keys preserved
# ---------------------------------------------------------------------------
def test_register_vault_preserves_unknown_top_level_keys(tmp_path: Path) -> None:
    """Extra top-level keys (e.g. 'settings') survive the round-trip."""
    cfg = tmp_path / "obsidian.json"
    vault = tmp_path / "Vault"
    vault.mkdir()
    original = {
        "vaults": {},
        "settings": {"theme": "dark", "fontSize": 14},
        "frames": [{"id": "framewin1", "x": 100, "y": 100}],
    }
    _write_cfg(cfg, original)

    result = register_vault(vault, config_path=cfg)

    assert result.status == "added"
    new_data = json.loads(cfg.read_text(encoding="utf-8"))
    assert new_data["settings"] == {"theme": "dark", "fontSize": 14}
    assert new_data["frames"] == [{"id": "framewin1", "x": 100, "y": 100}]
    assert result.vault_uuid in new_data["vaults"]
