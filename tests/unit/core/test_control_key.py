"""Per-user Jarvis Control API key lifecycle (step 4).

The key authenticates the local Control API so other local agents (Codex CLI,
Claude Code) can drive Jarvis. It must: be unique per install, persist across
restarts even when the OS keyring is unavailable (headless Linux VPS), be
idempotent on boot, and be rotatable. These tests inject a fake keyring so the
real Credential Manager is never touched.
"""
from __future__ import annotations

import sys

import pytest

from jarvis.core import config as cfg
from jarvis.core import control_key as ck


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    """In-memory keyring + temp data dir + no env seed."""
    store: dict[str, str] = {}

    def fake_set(key: str, value: str) -> bool:
        store[key] = value
        return True

    def fake_get(key: str, env_fallback: str | None = None) -> str | None:
        return store.get(key)

    def fake_del(key: str) -> bool:
        store.pop(key, None)
        return True

    monkeypatch.setattr(cfg, "set_secret", fake_set)
    monkeypatch.setattr(cfg, "get_secret", fake_get)
    monkeypatch.setattr(cfg, "delete_secret", fake_del)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_CONTROL_API_KEY", raising=False)
    return store


def test_generate_has_prefix_and_entropy() -> None:
    key = ck.generate_control_key()
    assert key.startswith("jctl_")
    assert len(key) > 40  # 256-bit token_urlsafe ~ 43 chars + prefix


def test_generate_is_unique() -> None:
    assert ck.generate_control_key() != ck.generate_control_key()


def test_mask_shows_only_last_four() -> None:
    masked = ck.mask_control_key("jctl_abcdef1234")
    assert masked.startswith("jctl_")
    assert masked.endswith("1234")
    assert "abcdef" not in masked
    assert ck.mask_control_key(None) == ""
    assert ck.mask_control_key("") == ""


def test_get_returns_none_when_nothing_stored(isolated) -> None:
    assert ck.get_control_key() is None


def test_ensure_is_idempotent(isolated) -> None:
    first = ck.ensure_control_key()
    second = ck.ensure_control_key()
    assert first == second
    assert first.startswith("jctl_")
    # stored exactly once in the (fake) keyring
    assert isolated.get(ck.KEYRING_SLOT) == first


def test_headless_fallback_writes_file(monkeypatch, tmp_path) -> None:
    # Simulate a headless VPS: keyring write fails, keyring read empty.
    monkeypatch.setattr(cfg, "set_secret", lambda *a, **k: False)
    monkeypatch.setattr(cfg, "get_secret", lambda *a, **k: None)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_CONTROL_API_KEY", raising=False)

    key = ck.ensure_control_key()
    key_file = ck.control_key_file()
    assert key_file.is_file()
    assert key_file.read_text(encoding="utf-8").strip() == key
    # And it is read back from the file on the next access.
    assert ck.get_control_key() == key
    if sys.platform != "win32":
        mode = key_file.stat().st_mode & 0o777
        assert mode == 0o600


def test_rotate_replaces_key(isolated) -> None:
    first = ck.ensure_control_key()
    rotated = ck.rotate_control_key()
    assert rotated != first
    assert ck.get_control_key() == rotated


def test_verify_constant_time(isolated) -> None:
    key = ck.ensure_control_key()
    assert ck.verify_control_key(key) is True
    assert ck.verify_control_key("jctl_wrong") is False
    assert ck.verify_control_key(None) is False
    assert ck.verify_control_key("") is False


def test_env_seed_used_when_no_keyring_no_file(isolated, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_CONTROL_API_KEY", "jctl_envseed0000")
    # keyring + file empty -> env seed is the active key
    assert ck.get_control_key() == "jctl_envseed0000"
    # ensure must NOT overwrite an operator-provided env seed
    assert ck.ensure_control_key() == "jctl_envseed0000"
    assert ck.KEYRING_SLOT not in isolated


# --- user-chosen key (set_control_key) ---


def test_validate_custom_key_rules() -> None:
    with pytest.raises(ck.ControlKeyValidationError):
        ck.validate_custom_control_key("short")
    with pytest.raises(ck.ControlKeyValidationError):
        ck.validate_custom_control_key("has spaces in the key")
    with pytest.raises(ck.ControlKeyValidationError):
        ck.validate_custom_control_key("x" * (ck.MAX_CUSTOM_KEY_LENGTH + 1))
    # The publicly shipped placeholder passes length/charset but must be refused.
    with pytest.raises(ck.ControlKeyValidationError):
        ck.validate_custom_control_key(ck.SHIPPED_PLACEHOLDER_KEY)
    assert ck.validate_custom_control_key("  correct-horse-battery ") == "correct-horse-battery"


def test_set_control_key_replaces_and_verifies(isolated) -> None:
    ck.ensure_control_key()
    stored = ck.set_control_key("correct-horse-battery")
    assert stored == "correct-horse-battery"
    assert ck.get_control_key() == "correct-horse-battery"
    assert ck.verify_control_key("correct-horse-battery") is True


def test_set_control_key_invalid_value_keeps_old_key(isolated) -> None:
    old = ck.ensure_control_key()
    with pytest.raises(ck.ControlKeyValidationError):
        ck.set_control_key("short")
    assert ck.get_control_key() == old


def test_mask_custom_key_has_no_generated_prefix() -> None:
    # A user-chosen key must not be dressed up with the generated jctl_ prefix.
    assert ck.mask_control_key("correct-horse-battery") == "…tery"


# --- shipped docker-compose.yml placeholder must never be accepted as real ---


def test_shipped_placeholder_constant_matches_docker_compose() -> None:
    # docker-compose.yml ships this exact literal; keep both in lockstep.
    assert ck.SHIPPED_PLACEHOLDER_KEY == "jctl_local_sandbox_change_me_before_any_real_use"


def test_get_treats_placeholder_env_seed_as_unset(isolated, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_CONTROL_API_KEY", ck.SHIPPED_PLACEHOLDER_KEY)
    assert ck.get_control_key() is None


def test_get_treats_placeholder_keyring_value_as_unset(isolated) -> None:
    isolated[ck.KEYRING_SLOT] = ck.SHIPPED_PLACEHOLDER_KEY
    assert ck.get_control_key() is None


def test_get_treats_placeholder_file_value_as_unset(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(cfg, "set_secret", lambda *a, **k: False)
    monkeypatch.setattr(cfg, "get_secret", lambda *a, **k: None)
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("JARVIS_CONTROL_API_KEY", raising=False)
    ck.control_key_file().parent.mkdir(parents=True, exist_ok=True)
    ck.control_key_file().write_text(ck.SHIPPED_PLACEHOLDER_KEY, encoding="utf-8")

    assert ck.get_control_key() is None


def test_verify_rejects_the_placeholder_itself(isolated, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_CONTROL_API_KEY", ck.SHIPPED_PLACEHOLDER_KEY)
    # Presenting the exact placeholder must be denied — same as "no key configured".
    assert ck.verify_control_key(ck.SHIPPED_PLACEHOLDER_KEY) is False


def test_ensure_regenerates_a_real_key_when_placeholder_configured(isolated, monkeypatch) -> None:
    monkeypatch.setenv("JARVIS_CONTROL_API_KEY", ck.SHIPPED_PLACEHOLDER_KEY)
    fresh = ck.ensure_control_key()
    assert fresh != ck.SHIPPED_PLACEHOLDER_KEY
    assert fresh.startswith("jctl_")
    # The freshly generated key is now the active one (stored, not the env seed).
    assert ck.get_control_key() == fresh
    assert isolated.get(ck.KEYRING_SLOT) == fresh


def test_get_logs_a_warning_naming_the_remedy(isolated, monkeypatch, caplog) -> None:
    # The warning is deliberately logged only once per process (get_control_key
    # is on the hot request path); reset that latch so this test is independent
    # of whatever ran earlier in the same session.
    monkeypatch.setattr(ck, "_warned_shipped_placeholder", False)
    monkeypatch.setenv("JARVIS_CONTROL_API_KEY", ck.SHIPPED_PLACEHOLDER_KEY)
    with caplog.at_level("WARNING", logger="jarvis.core.control_key"):
        assert ck.get_control_key() is None
    assert any(
        "JARVIS_CONTROL_API_KEY" in record.getMessage()
        and "fresh random value" in record.getMessage()
        for record in caplog.records
    )
