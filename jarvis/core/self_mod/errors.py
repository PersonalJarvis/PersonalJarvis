"""Exception hierarchy for the self-mod pipeline (Phase 7.1+)."""
from __future__ import annotations


class SelfModError(Exception):
    """Base exception for all self-mod errors."""


class AllowlistViolationError(SelfModError):
    """Path is not in SelfModRegistry.ALLOWED.

    Deny-by-default. Extending the allowlist requires a code edit plus
    code review (Plan-§AD-1, §AP-11).
    """


class SecretAccessError(SelfModError):
    """Attempt to read or mutate a secret/privileged path.

    Defense-in-depth: covers `security.*`, `mcp_server.*`, `harness.*`
    and all paths containing `*_api_key`/`*_token`/`*_secret`/`*_password`
    (Plan-§AP-9, §AP-2).
    """


class ProviderSwitchLockedError(SelfModError):
    """A non-USER actor tried to change a brain provider-selection key.

    The active brain provider is the user's hard choice — it changes ONLY
    through an explicit user action (the control CLI or the manual provider
    switch in the desktop app, `actor=USER`), never through Jarvis itself
    (voice/chat self-mod) or any automatic mechanism. See `provider_lock.py`.
    """


class TypeMismatchError(SelfModError):
    """Value type does not match the expected Pydantic field type."""


class PreValidateError(SelfModError):
    """Pre-validation against `JarvisConfig` failed (Plan-§AD-5, §AP-3).

    Consequence: no write, no backup, no rollback needed.
    The original Pydantic `ValidationError` is accessible via `__cause__`.
    """


class BackupError(SelfModError):
    """Backup could not be created or restored (Plan-§AP-4)."""


class ReloadError(SelfModError):
    """Reload test failed after a successful write (Plan-§AD-5, §AP-5).

    Raised AFTER the backup has been restored — the original file is
    already in place again. The original reload exception is accessible
    via `__cause__`.
    """


class RollbackError(SelfModError):
    """Restore from backup failed.

    This is the most severe error state: jarvis.toml may have been written
    in a corrupt state AND the restore also failed. The caller must manually
    recover from `<jarvis.toml.parent>/.backups/`.
    """
