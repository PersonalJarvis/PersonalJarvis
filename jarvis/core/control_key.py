"""Per-user Jarvis Control key — generation, cross-platform storage, rotation.

One credential, two doors: the key unlocks the browser UI (the AuthGate's
"Control Key" prompt mints an HttpOnly session from it) AND authenticates the
local Control API. It is auto-generated on first boot; the user may replace it
with a memorable value of their own via :func:`set_control_key`.

Open-source contract: every install owns a unique key (no shared secret baked
into the package). The key authenticates the local Control API (``/api/control/*``)
so other LOCAL agents (Codex CLI, Claude Code, a test harness) can drive Jarvis —
read settings, switch providers, change language — without Computer-Use.

Storage is the OS keyring when available (Windows Credential Manager / macOS
Keychain / Linux Secret Service) under the existing ``KEYRING_SERVICE``. On a
headless Linux VPS without a Secret Service daemon ``cfg.set_secret`` silently
returns ``False`` — so a ``0600`` file fallback (under the data dir) is
mandatory; the key must never be lost on restart. Read order: keyring -> file
-> ``JARVIS_CONTROL_API_KEY`` env seed.

Security: the key is NEVER exported into ``os.environ`` during normal operation
(a spawned worker would inherit it and leak it via ``/proc/<pid>/environ`` on
Linux). It is read on demand inside the auth dependency. Logs/UI lists show only
the masked form (``jctl_…last4``); the clear value crosses the wire solely on the
dedicated key-reveal endpoint.
"""
from __future__ import annotations

import logging
import os
import re
import secrets
import stat
from pathlib import Path

from jarvis.core import config as cfg

logger = logging.getLogger(__name__)

KEY_PREFIX = "jctl_"
KEYRING_SLOT = "jarvis_control_api_key"
ENV_VAR = "JARVIS_CONTROL_API_KEY"
_FILE_NAME = ".control_api_key"
_TOKEN_BYTES = 32  # 256-bit

# User-chosen key rules. The key doubles as an RFC-6750 Bearer token (CLI /
# local agents) and as the value typed into the browser unlock form, so it
# must stay header-safe: no spaces, URL-safe characters only. The minimum
# length keeps a memorable passphrase from degrading into a guessable PIN.
MIN_CUSTOM_KEY_LENGTH = 12
MAX_CUSTOM_KEY_LENGTH = 128
_CUSTOM_KEY_RE = re.compile(r"^[A-Za-z0-9._~-]+$")


class ControlKeyValidationError(ValueError):
    """A user-chosen control key failed the format/strength rules."""

# The literal value shipped as the default in docker-compose.yml. It is
# world-known (public repo, public image) the moment it is ever used
# unmodified, so it must never be accepted as a real key — wherever it is
# found (keyring, file fallback, or the env seed) it is treated exactly as if
# no key were configured at all.
SHIPPED_PLACEHOLDER_KEY = "jctl_local_sandbox_change_me_before_any_real_use"

_warned_shipped_placeholder = False


def _warn_shipped_placeholder_once() -> None:
    """Log a single, clear warning the first time the placeholder is seen.

    ``get_control_key()`` can be called on every request (via
    ``verify_control_key``); logging on every call would flood the log, so
    this fires once per process.
    """
    global _warned_shipped_placeholder
    if _warned_shipped_placeholder:
        return
    _warned_shipped_placeholder = True
    logger.warning(
        "Jarvis Control API key is set to the shipped placeholder value "
        "from docker-compose.yml — refusing it and treating it as if no key "
        "were configured. Set %s to a fresh random value before exposing the "
        "control surface.",
        ENV_VAR,
    )


def generate_control_key() -> str:
    """A fresh 256-bit URL-safe key with a greppable ``jctl_`` prefix."""
    return f"{KEY_PREFIX}{secrets.token_urlsafe(_TOKEN_BYTES)}"


def mask_control_key(key: str | None) -> str:
    """``jctl_…last4`` (or ``…last4`` for a user-chosen key) for logs / UI.

    Empty string for a missing key. The ``jctl_`` prefix is only echoed when
    the key actually carries it — a user-chosen key must not be dressed up as
    a generated one.
    """
    if not key:
        return ""
    tail = key[-4:] if len(key) >= 4 else key
    prefix = KEY_PREFIX if key.startswith(KEY_PREFIX) else ""
    return f"{prefix}…{tail}"


def control_key_file() -> Path:
    """Path of the ``0600`` fallback file.

    ``JARVIS_DATA_DIR`` wins; otherwise the data dir sits next to the resolved
    config (``resolve_config_path().parent / "data"``) so it co-locates with the
    rest of Jarvis's runtime state on both desktop and a VPS.
    """
    base = os.environ.get("JARVIS_DATA_DIR")
    if base and base.strip():
        data_dir = Path(base.strip())
    else:
        data_dir = cfg.resolve_config_path().parent / "data"
    return data_dir / _FILE_NAME


def _read_file_key() -> str | None:
    try:
        path = control_key_file()
        if path.is_file():
            value = path.read_text(encoding="utf-8").strip()
            return value or None
    except OSError:
        pass
    return None


def _write_file_key(key: str) -> bool:
    try:
        path = control_key_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(key, encoding="utf-8")
        if os.name == "posix":
            try:
                os.chmod(path, stat.S_IRUSR | stat.S_IWUSR)  # 0600
            except OSError:
                pass
        return True
    except OSError:
        return False


def _env_key() -> str | None:
    value = os.environ.get(ENV_VAR)
    return value.strip() if value and value.strip() else None


def _store(key: str) -> None:
    """Persist ``key``. Keyring is authoritative; the file is the headless
    fallback. CHECK the keyring return — it lies (returns False) on a VPS.

    If the keyring succeeds but a stale file copy exists (from an earlier
    headless boot), keep it in sync so the old key cannot resurrect on reboot.
    """
    keyring_ok = cfg.set_secret(KEYRING_SLOT, key)
    if not keyring_ok:
        _write_file_key(key)
    elif control_key_file().exists():
        _write_file_key(key)


def get_control_key() -> str | None:
    """The active key. Order: keyring -> 0600 file -> env seed.

    The shipped ``docker-compose.yml`` placeholder (``SHIPPED_PLACEHOLDER_KEY``)
    is never returned as a real key, no matter which layer it was found in —
    it is treated exactly as if that layer held nothing, and resolution keeps
    falling through the chain.
    """
    saw_placeholder = False

    value = cfg.get_secret(KEYRING_SLOT)  # keyring only (no env fallback here)
    if value:
        if value != SHIPPED_PLACEHOLDER_KEY:
            return value
        saw_placeholder = True

    value = _read_file_key()
    if value:
        if value != SHIPPED_PLACEHOLDER_KEY:
            return value
        saw_placeholder = True

    value = _env_key()
    if value:
        if value != SHIPPED_PLACEHOLDER_KEY:
            return value
        saw_placeholder = True

    if saw_placeholder:
        _warn_shipped_placeholder_once()
    return None


def ensure_control_key() -> str:
    """Return the existing key, or generate + persist one. Idempotent.

    Call ONCE before the FastAPI app is created so the key exists by the time an
    agent hits ``/api/control/*``. Never silently regenerates an existing key —
    that would lock out every agent that cached it (and an operator-supplied env
    seed is respected, not overwritten).
    """
    existing = get_control_key()
    if existing:
        return existing
    key = generate_control_key()
    _store(key)
    return key


def _replace_key_everywhere(key: str) -> str:
    """Overwrite the active key in keyring AND file; fail loudly on total miss.

    The single-key model means writing the new value invalidates the old (no
    separate revocation list). The file copy is overwritten too so a stale file
    cannot resurrect the previous key on the next boot.
    """
    keyring_ok = cfg.set_secret(KEYRING_SLOT, key)
    file_ok = _write_file_key(key)
    if not keyring_ok and not file_ok:
        # Neither store accepted the new key. Returning it would lock out the
        # caller: the NEXT get_control_key() reads the OLD key, so verify() of
        # the just-returned key fails. Fail loudly instead — the old key stays.
        raise RuntimeError(
            "Control key replacement failed: neither the OS keyring nor the "
            "file fallback accepted the new key. The previous key remains active."
        )
    return key


def rotate_control_key() -> str:
    """Generate a NEW random key, overwrite the old one everywhere, return it."""
    return _replace_key_everywhere(generate_control_key())


def validate_custom_control_key(value: str) -> str:
    """Return the trimmed user-chosen key or raise ``ControlKeyValidationError``.

    The messages are user-facing (surfaced verbatim by the UI/CLI), so they
    state the exact rule that failed.
    """
    candidate = (value or "").strip()
    if len(candidate) < MIN_CUSTOM_KEY_LENGTH:
        raise ControlKeyValidationError(
            f"The control key must be at least {MIN_CUSTOM_KEY_LENGTH} characters long."
        )
    if len(candidate) > MAX_CUSTOM_KEY_LENGTH:
        raise ControlKeyValidationError(
            f"The control key must be at most {MAX_CUSTOM_KEY_LENGTH} characters long."
        )
    if not _CUSTOM_KEY_RE.fullmatch(candidate):
        raise ControlKeyValidationError(
            "The control key may only contain letters, digits, and . _ ~ - "
            "(no spaces) so it works as an HTTP Bearer token."
        )
    if candidate == SHIPPED_PLACEHOLDER_KEY:
        raise ControlKeyValidationError(
            "This value is the publicly shipped placeholder key and cannot be used."
        )
    return candidate


def set_control_key(value: str) -> str:
    """Persist a user-chosen key as the active control key and return it.

    Validation is fail-closed (``ControlKeyValidationError``); persistence
    mirrors :func:`rotate_control_key` — the old key stays active if neither
    store accepts the new one.
    """
    return _replace_key_everywhere(validate_custom_control_key(value))


def verify_control_key(presented: str | None) -> bool:
    """Constant-time comparison of a presented key against the stored one."""
    if not presented:
        return False
    stored = get_control_key()
    if not stored:
        return False
    return secrets.compare_digest(presented, stored)
