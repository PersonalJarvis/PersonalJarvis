"""Per-user Jarvis Control API key lifecycle (step 4).

The key authenticates the local Control API so other local agents (Codex CLI,
Claude Code) can drive Jarvis. It must: be unique per install, persist across
restarts even when the OS keyring is unavailable (headless Linux VPS), be
idempotent on boot, and be rotatable. These tests inject a fake keyring so the
real Credential Manager is never touched.
"""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace

import pytest

from jarvis.core import config as cfg
from jarvis.core import control_key as ck

_REAL_MACOS_APP_IDENTITY_TOKEN = ck._macos_app_identity_token


@pytest.fixture(autouse=True)
def reset_process_cache(monkeypatch):
    """No test may inherit another test's in-process credential cache."""
    ck._clear_control_key_cache()
    # Unit tests must never inspect or mutate the developer machine's real app
    # identity, even when this suite itself runs on macOS.
    monkeypatch.setattr(ck, "_macos_app_identity_token", lambda: None)
    yield
    ck._clear_control_key_cache()


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


def test_successful_keyring_read_is_cached_for_the_process(isolated, monkeypatch) -> None:
    calls = 0
    isolated[ck.KEYRING_SLOT] = "jctl_cached-key"

    def fake_get(key: str, env_fallback: str | None = None) -> str | None:
        nonlocal calls
        calls += 1
        return isolated.get(key)

    monkeypatch.setattr(cfg, "get_secret", fake_get)
    assert ck.get_control_key() == "jctl_cached-key"
    assert ck.get_control_key() == "jctl_cached-key"
    assert calls == 1


def test_concurrent_auth_checks_share_one_keyring_read(isolated, monkeypatch) -> None:
    calls = 0
    entered = threading.Event()
    release = threading.Event()
    isolated[ck.KEYRING_SLOT] = "jctl_one-dialog"

    def slow_get(key: str, env_fallback: str | None = None) -> str | None:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=2)
        return isolated.get(key)

    monkeypatch.setattr(cfg, "get_secret", slow_get)
    with ThreadPoolExecutor(max_workers=12) as pool:
        futures = [pool.submit(ck.get_control_key) for _ in range(24)]
        assert entered.wait(timeout=2)
        release.set()
        assert [future.result(timeout=2) for future in futures] == ["jctl_one-dialog"] * 24
    assert calls == 1


def test_cache_is_invalidated_by_secret_revision(isolated, monkeypatch) -> None:
    revision = 0
    isolated[ck.KEYRING_SLOT] = "jctl_first-value"
    monkeypatch.setattr(cfg, "secret_revision", lambda _key: revision)

    assert ck.get_control_key() == "jctl_first-value"
    isolated[ck.KEYRING_SLOT] = "jctl_second-value"
    assert ck.get_control_key() == "jctl_first-value"
    revision += 1
    assert ck.get_control_key() == "jctl_second-value"


def test_forked_child_drops_the_parent_cache(isolated, monkeypatch) -> None:
    calls = 0
    isolated[ck.KEYRING_SLOT] = "jctl_parent-value"

    def fake_get(key: str, env_fallback: str | None = None) -> str | None:
        nonlocal calls
        calls += 1
        return isolated.get(key)

    monkeypatch.setattr(cfg, "get_secret", fake_get)
    assert ck.get_control_key() == "jctl_parent-value"
    ck._after_fork_in_child()
    assert ck.get_control_key() == "jctl_parent-value"
    assert calls == 2


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
    ck._clear_control_key_cache()
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


# --- one-time macOS Keychain ownership migration ---


def test_macos_identity_uses_verified_canonical_designated_requirement(
    monkeypatch, tmp_path
) -> None:
    bundle_path = tmp_path / "Applications" / "Personal Jarvis.app"
    executable = bundle_path / "Contents" / "MacOS" / "PersonalJarvis"
    executable.parent.mkdir(parents=True)
    executable.write_bytes(b"Mach-O test fixture")
    bundle = SimpleNamespace(
        bundleIdentifier=lambda: "com.personal-jarvis.desktop",
        bundlePath=lambda: str(bundle_path),
        executablePath=lambda: str(executable),
    )
    foundation = SimpleNamespace(NSBundle=SimpleNamespace(mainBundle=lambda: bundle))
    commands: list[list[str]] = []

    def fake_run(command: list[str], **kwargs):
        commands.append(command)
        if "--verify" in command:
            return SimpleNamespace(returncode=0)
        return SimpleNamespace(
            returncode=0,
            stderr='# designated => identifier "com.personal-jarvis.desktop"\n',
            stdout="",
        )

    monkeypatch.setattr(ck.sys, "platform", "darwin")
    monkeypatch.setattr(ck.Path, "home", classmethod(lambda _cls: tmp_path))
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setattr(ck.subprocess, "run", fake_run)

    identity = _REAL_MACOS_APP_IDENTITY_TOKEN()
    assert identity is not None
    assert identity.startswith("designated-requirement-v1:")
    assert commands[0][:2] == ["/usr/bin/codesign", "--verify"]
    assert commands[1][:4] == [
        "/usr/bin/codesign",
        "--display",
        "--requirements",
        "-",
    ]


def test_macos_identity_rejects_direct_python_before_codesign(monkeypatch, tmp_path) -> None:
    bundle = SimpleNamespace(
        bundleIdentifier=lambda: "org.python.python",
        bundlePath=lambda: str(tmp_path),
        executablePath=lambda: str(tmp_path / "python3.12"),
    )
    foundation = SimpleNamespace(NSBundle=SimpleNamespace(mainBundle=lambda: bundle))
    monkeypatch.setattr(ck.sys, "platform", "darwin")
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setattr(
        ck.subprocess,
        "run",
        lambda *args, **kwargs: pytest.fail("codesign must not run for direct Python"),
    )

    assert _REAL_MACOS_APP_IDENTITY_TOKEN() is None


def test_legacy_macos_item_is_adopted_once(isolated, monkeypatch) -> None:
    identity = "designated-requirement-v1:stable-app"
    writes: list[tuple[str, str]] = []
    isolated[ck.KEYRING_SLOT] = "jctl_legacy-value"
    original_set = cfg.set_secret

    def recording_set(key: str, value: str) -> bool:
        writes.append((key, value))
        return original_set(key, value)

    monkeypatch.setattr(ck.sys, "platform", "darwin")
    monkeypatch.setattr(ck, "_macos_app_identity_token", lambda: identity)
    monkeypatch.setattr(ck, "_platform_credential_store_active", lambda: True)
    monkeypatch.setattr(ck, "_macos_keychain_item_exists", lambda: True)
    monkeypatch.setattr(cfg, "set_secret", recording_set)

    assert ck.get_control_key() == "jctl_legacy-value"
    assert writes == [(ck.KEYRING_SLOT, "jctl_legacy-value")]
    assert ck._macos_owner_file().read_text(encoding="utf-8") == identity

    # A new process under the same designated requirement reads the item but
    # does not delete/re-create it again.
    ck._clear_control_key_cache()
    assert ck.get_control_key() == "jctl_legacy-value"
    assert writes == [(ck.KEYRING_SLOT, "jctl_legacy-value")]


def test_new_macos_app_requirement_readopts_legacy_item(isolated, monkeypatch) -> None:
    identity = ["designated-requirement-v1:first-build"]
    writes: list[str] = []
    isolated[ck.KEYRING_SLOT] = "jctl_legacy-value"
    original_set = cfg.set_secret

    def recording_set(key: str, value: str) -> bool:
        writes.append(identity[0])
        return original_set(key, value)

    monkeypatch.setattr(ck.sys, "platform", "darwin")
    monkeypatch.setattr(ck, "_macos_app_identity_token", lambda: identity[0])
    monkeypatch.setattr(ck, "_platform_credential_store_active", lambda: True)
    monkeypatch.setattr(ck, "_macos_keychain_item_exists", lambda: True)
    monkeypatch.setattr(cfg, "set_secret", recording_set)

    assert ck.get_control_key() == "jctl_legacy-value"
    identity[0] = "designated-requirement-v1:rebuilt-app"
    ck._clear_control_key_cache()
    assert ck.get_control_key() == "jctl_legacy-value"
    assert writes == [
        "designated-requirement-v1:first-build",
        "designated-requirement-v1:rebuilt-app",
    ]


def test_direct_python_read_never_changes_keychain_acl(isolated, monkeypatch) -> None:
    writes = 0
    isolated[ck.KEYRING_SLOT] = "jctl_legacy-value"

    def unexpected_set(key: str, value: str) -> bool:
        nonlocal writes
        writes += 1
        return True

    monkeypatch.setattr(ck, "_macos_app_identity_token", lambda: None)
    monkeypatch.setattr(cfg, "set_secret", unexpected_set)
    assert ck.get_control_key() == "jctl_legacy-value"
    assert writes == 0


def test_file_seed_is_not_promoted_into_macos_keychain(isolated, monkeypatch) -> None:
    writes = 0
    ck.control_key_file().write_text("jctl_file-value", encoding="utf-8")
    monkeypatch.setattr(ck.sys, "platform", "darwin")
    monkeypatch.setattr(
        ck,
        "_macos_app_identity_token",
        lambda: "designated-requirement-v1:stable-app",
    )
    monkeypatch.setattr(ck, "_platform_credential_store_active", lambda: True)
    monkeypatch.setattr(ck, "_macos_keychain_item_exists", lambda: False)

    def unexpected_set(key: str, value: str) -> bool:
        nonlocal writes
        writes += 1
        return True

    monkeypatch.setattr(cfg, "set_secret", unexpected_set)
    assert ck.get_control_key() == "jctl_file-value"
    assert writes == 0


def test_macos_owner_stamp_requires_a_successful_platform_write(
    isolated, monkeypatch
) -> None:
    identity = "designated-requirement-v1:stable-app"
    monkeypatch.setattr(ck.sys, "platform", "darwin")
    monkeypatch.setattr(ck, "_macos_app_identity_token", lambda: identity)
    monkeypatch.setattr(ck, "_platform_credential_store_active", lambda: True)

    ck.set_control_key("correct-horse-battery")
    assert ck._macos_owner_file().read_text(encoding="utf-8") == identity

    # A failed Keychain write can still succeed via the dedicated 0600 file,
    # but it must not retain a false claim that the app owns a Keychain item.
    monkeypatch.setattr(cfg, "set_secret", lambda *args, **kwargs: False)
    ck.set_control_key("another-correct-key")
    assert not ck._macos_owner_file().exists()


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
