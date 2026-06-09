"""AtomicConfigWriter — atomic, validating mutation of `jarvis.toml`.

Plan reference: §7.2 "Atomic Writer with Pre-Validate, Backup, Rollback".

Pipeline per `mutate(request)` call (all steps mandatory, order fixed):
  1. Allowlist validation via `SelfModRegistry.require_spec`.
  2. Load `jarvis.toml` with `tomlkit` (preserving comments and structure).
  3. Read `old_value` via dotted path.
  4. Apply mutation in-memory.
  5. Pre-validate: full `JarvisConfig.model_validate(doc.unwrap())`.
  6. Backup to `<config>.parent / .backups / jarvis.toml.<iso>.bak`.
  7. Atomic write: `tempfile.mkstemp` in the same directory,
     `fsync` the tempfile contents, `os.replace` as the atomic swap.
  8. Reload test: synchronous `ConfigLoader.load()` call. On crash:
     restore from backup, then raise `ReloadError`.
  9. Dispatch `ConfigReloaded` event on the optional `EventBus`.
 10. Backup GC (FIFO at cap, age-based with floor).
 11. Audit entry via `SelfModAudit`.

Locking: `_LOCK` is a `ClassVar` `threading.Lock`. Acquired from step 2,
released in `finally`. Allowlist lookup (step 1) is read-only and
requires no lock.

BOM preservation: On Windows many editors prepend a UTF-8 BOM.
`tomlkit.parse` does not tolerate this — we strip it before parsing and
write it back after dumping so the file stays byte-identical to the user's
version (see `jarvis/core/config_writer.py` for the same pattern).
"""
from __future__ import annotations

import asyncio
import logging
import os
import tempfile
import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, ClassVar

import tomlkit
from pydantic import ValidationError as PydanticValidationError
from tomlkit import TOMLDocument
from tomlkit.items import AbstractTable

from jarvis.core.bus import EventBus
from jarvis.core.config import (
    JarvisConfig,
    load_config,
    resolve_config_path,
)
from jarvis.core.events import ConfigReloaded

from .audit import SelfModAudit
from .errors import (
    AllowlistViolationError,
    BackupError,
    PreValidateError,
    ReloadError,
    RollbackError,
    SecretAccessError,
)
from .registry import SelfModRegistry
from .schema import (
    AuditEvent,
    BackupRef,
    MutableSpec,
    MutationRequest,
    MutationResult,
)

_LOG = logging.getLogger(__name__)

# Plan-§AD-6 / §7.2: Backup directory lives directly next to `jarvis.toml`.
DEFAULT_BACKUP_SUBDIR = ".backups"
BACKUP_FILE_GLOB = "jarvis.toml.*.bak"
BACKUP_TS_FORMAT = "%Y%m%dT%H%M%S_%fZ"


class AtomicConfigWriter:
    """Atomic, validating mutation engine for `jarvis.toml`."""

    _LOCK: ClassVar[threading.Lock] = threading.Lock()
    _BOM: ClassVar[str] = "﻿"

    def __init__(
        self,
        *,
        config_path: Path | str | None = None,
        backup_dir: Path | str | None = None,
        max_backups: int = 50,
        backup_min_keep: int = 10,
        backup_max_age_days: int = 30,
        audit: SelfModAudit | None = None,
        bus: EventBus | None = None,
        config_loader: Callable[[Path], JarvisConfig] | None = None,
    ) -> None:
        self._config_path: Path = (
            Path(config_path) if config_path is not None else resolve_config_path()
        )
        self._backup_dir: Path = (
            Path(backup_dir)
            if backup_dir is not None
            else self._config_path.parent / DEFAULT_BACKUP_SUBDIR
        )
        if max_backups < backup_min_keep:
            raise ValueError(
                f"max_backups ({max_backups}) muss >= backup_min_keep "
                f"({backup_min_keep}) sein"
            )
        self._max_backups = max_backups
        self._backup_min_keep = backup_min_keep
        self._backup_max_age_days = backup_max_age_days
        self._audit: SelfModAudit = audit if audit is not None else SelfModAudit()
        self._bus = bus
        self._config_loader: Callable[[Path], JarvisConfig] = (
            config_loader if config_loader is not None else _default_loader
        )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    @property
    def config_path(self) -> Path:
        return self._config_path

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    def read_value(self, path: str) -> Any:
        """Reads a dotted path from the current `jarvis.toml`.

        Read-only — no lock required (reads of jarvis.toml always see a
        consistent snapshot since the atomic `os.replace`).
        Raises `BackupError` if the file is missing or unparseable.
        Returns `None` for non-existent paths.
        """
        try:
            raw = self._config_path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BackupError(
                f"Konnte {self._config_path} nicht lesen: {exc}"
            ) from exc
        if raw.startswith(self._BOM):
            raw = raw[len(self._BOM):]
        try:
            doc = tomlkit.parse(raw)
        except Exception as exc:  # noqa: BLE001 — alle Parser-Fehler sind BackupError
            raise BackupError(
                f"Konnte {self._config_path} nicht parsen: {exc}"
            ) from exc
        return self._read_dotted(doc, path)

    def mutate(self, request: MutationRequest) -> MutationResult:
        """Plan-§7.2 pipeline.

        Raises depending on the failure stage:
        - `AllowlistViolationError` / `SecretAccessError` (step 1)
        - `PreValidateError` (step 5; no backup, no write)
        - `BackupError` (step 6; no write)
        - `ReloadError` (step 8; backup has been restored)
        - `RollbackError` (step 8; backup restore itself failed)
        """
        # Step 1 — no lock required (ClassVar read).
        try:
            spec = SelfModRegistry.require_spec(request.path)
        except (AllowlistViolationError, SecretAccessError) as exc:
            self._audit_failure(request, old_value=None, error=str(exc))
            raise

        with type(self)._LOCK:
            return self._mutate_locked(request, spec)

    def list_backups(self, limit: int = 20) -> list[BackupRef]:
        """Returns the `limit` most recent backups (sorted: newest first).

        Race protection: between `glob` and `stat` a concurrent GC may
        unlink files. We therefore snapshot in a single pass and skip
        missing files (TOCTOU-tolerant).
        """
        if not self._backup_dir.exists():
            return []
        snapshot: list[tuple[Path, float, int]] = []
        for path in self._backup_dir.glob(BACKUP_FILE_GLOB):
            try:
                stat = path.stat()
            except OSError:
                continue
            snapshot.append((path, stat.st_mtime, stat.st_size))
        snapshot.sort(key=lambda triplet: triplet[1], reverse=True)
        now = time.time()
        result: list[BackupRef] = []
        for path, mtime, size in snapshot[:limit]:
            result.append(
                BackupRef(
                    filename=path.name,
                    path=str(path),
                    timestamp=datetime.fromtimestamp(mtime, tz=UTC),
                    size_bytes=size,
                    age_seconds=max(0.0, now - mtime),
                )
            )
        return result

    def rollback(self, backup_filename: str) -> Path:
        """Manual restore from a named backup.

        Raises `BackupError` if the file does not exist or is not in the
        backup directory (path-traversal protection). Existence check and
        restore run under `_LOCK` so that a concurrent GC cannot remove
        the file between check and use.
        """
        candidate = (self._backup_dir / backup_filename).resolve()
        try:
            candidate.relative_to(self._backup_dir.resolve())
        except ValueError as exc:
            raise BackupError(
                f"Backup-Pfad '{backup_filename}' liegt außerhalb des "
                f"Backup-Verzeichnisses {self._backup_dir}"
            ) from exc
        with type(self)._LOCK:
            if not candidate.is_file():
                raise BackupError(
                    f"Backup '{backup_filename}' existiert nicht"
                )
            self._restore(candidate)
            return candidate

    # ------------------------------------------------------------------
    # Pipeline
    # ------------------------------------------------------------------

    def _mutate_locked(
        self, request: MutationRequest, spec: MutableSpec
    ) -> MutationResult:
        # Step 2 — load + BOM detection
        try:
            raw = self._config_path.read_text(encoding="utf-8")
        except OSError as exc:
            self._audit_failure(request, old_value=None, error=f"read_failed: {exc}")
            raise BackupError(f"Konnte {self._config_path} nicht lesen: {exc}") from exc

        had_bom = raw.startswith(self._BOM)
        if had_bom:
            raw = raw[len(self._BOM):]
        try:
            doc = tomlkit.parse(raw)
        except Exception as exc:
            self._audit_failure(request, old_value=None, error=f"parse_failed: {exc}")
            raise BackupError(
                f"Konnte {self._config_path} nicht parsen: {exc}"
            ) from exc

        # Step 3 — old_value
        old_value = self._read_dotted(doc, request.path)

        # Step 4 — apply mutation in-memory
        self._apply_dotted(doc, request.path, request.new_value)

        # Step 5 — pre-validate (Plan-§AD-5, §AP-3)
        merged = doc.unwrap()
        try:
            JarvisConfig.model_validate(merged)
        except (PydanticValidationError, ValueError, TypeError) as exc:
            self._audit_failure(
                request,
                old_value=old_value,
                error=f"validate_failed: {_summarize_error(exc)}",
            )
            raise PreValidateError(
                f"Pre-Validate für '{request.path}' = {request.new_value!r} "
                f"schlug fehl: {_summarize_error(exc)}"
            ) from exc

        # Step 6 — backup (only AFTER pre-validate, so that the reject path
        # leaves no traces — Plan-§AC §7.2 "no backup, no file")
        try:
            backup_path = self._make_backup()
        except OSError as exc:
            self._audit_failure(
                request,
                old_value=old_value,
                error=f"backup_failed: {exc}",
            )
            raise BackupError(
                f"Backup für {self._config_path} schlug fehl: {exc}"
            ) from exc

        # Step 7 — atomic write
        output = tomlkit.dumps(doc)
        if had_bom:
            output = self._BOM + output
        try:
            self._atomic_write(output)
        except OSError as exc:
            # Step 7 failure: original still intact (os.replace not yet executed).
            self._audit_failure(
                request,
                old_value=old_value,
                error=f"write_failed: {exc}",
            )
            raise BackupError(
                f"Atomic-Write für {self._config_path} schlug fehl: {exc}"
            ) from exc

        # Step 8 — reload test (synchronous; Plan-§AP-14)
        try:
            self._config_loader(self._config_path)
        except Exception as exc:  # noqa: BLE001 — Plan-§AD-5: jeder Reload-Crash → Rollback
            try:
                self._restore(backup_path)
                rolled = True
            except Exception as restore_exc:  # noqa: BLE001
                rolled = False
                _LOG.error(
                    "RollbackError nach ReloadFailure: %s (original error: %s)",
                    restore_exc,
                    exc,
                )
                self._audit_failure(
                    request,
                    old_value=old_value,
                    error=f"reload_failed_AND_rollback_failed: {exc} | {restore_exc}",
                    rolled_back=False,
                )
                raise RollbackError(
                    f"jarvis.toml ist möglicherweise korrupt. Manueller "
                    f"Restore aus {backup_path} erforderlich. "
                    f"Reload-Error: {exc}; Rollback-Error: {restore_exc}"
                ) from restore_exc
            self._audit_failure(
                request,
                old_value=old_value,
                error=f"post_validate_failed_rolled_back: {_summarize_error(exc)}",
                rolled_back=rolled,
            )
            raise ReloadError(
                f"Reload-Test nach Mutation '{request.path}' schlug fehl, "
                f"Original wurde aus Backup restauriert: "
                f"{_summarize_error(exc)}"
            ) from exc

        # Step 9 — bus dispatch (fire-and-forget)
        self._dispatch_reloaded(request)

        # Step 10 — backup GC (failures here do NOT propagate — the mutation
        # itself succeeded; GC is hygiene, not critical)
        try:
            self._gc_backups()
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("Backup-GC fehlgeschlagen: %s", exc)

        # Step 11 — audit success
        self._audit.record(
            AuditEvent(
                source=request.source,
                requested_by=request.actor,
                path=request.path,
                old_value=old_value,
                new_value=request.new_value,
                ok=True,
                rolled_back=False,
                error=None,
            )
        )

        return MutationResult(
            request=request,
            ok=True,
            old_value=old_value,
            new_value=request.new_value,
            error_kind=None,
            error_message=None,
            rolled_back=False,
            backup_path=str(backup_path),
        )

    # ------------------------------------------------------------------
    # Helpers — pipeline building blocks
    # ------------------------------------------------------------------

    @staticmethod
    def _read_dotted(doc: TOMLDocument, path: str) -> Any:
        parts = path.split(".")
        cur: Any = doc
        for part in parts:
            if not isinstance(cur, (dict, AbstractTable)):
                return None
            try:
                cur = cur[part]
            except KeyError:
                return None
        if hasattr(cur, "unwrap"):
            try:
                return cur.unwrap()
            except (TypeError, AttributeError):
                pass
        return cur

    @staticmethod
    def _apply_dotted(doc: TOMLDocument, path: str, value: Any) -> None:
        parts = path.split(".")
        if not parts or any(p == "" for p in parts):
            raise ValueError(f"Ungültiger Pfad: {path!r}")
        parent: Any = doc
        for part in parts[:-1]:
            existing = parent.get(part) if isinstance(parent, (dict, AbstractTable)) else None
            if not isinstance(existing, (dict, AbstractTable)):
                new_table = tomlkit.table()
                parent[part] = new_table
                parent = new_table
            else:
                parent = existing
        parent[parts[-1]] = value

    def _atomic_write(self, content: str) -> None:
        """Plan-§7.2 step 7: tempfile + fsync + os.replace.

        - `mkstemp` in the same directory guarantees an NTFS/POSIX atomic swap.
        - `fsync` forces the OS to physically write the content before
          `os.replace` swaps the path — otherwise a crash between write and
          replace could leave a partially written file.
        - Tempfile is cleaned up in the except path (no orphaned `.tmp`).
        - Directory fsync (POSIX best practice after power loss) is deliberately
          omitted — not trivially portable on NTFS/Windows.
          Assumption P7.2-A1: NTFS journaling preserves consistency; if
          power-loss robustness is required Phase 7.7+ must add it.
        """
        parent = self._config_path.parent
        parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(
            suffix=".tmp",
            prefix=f"{self._config_path.name}.",
            dir=str(parent),
        )
        tmp_path: Path | None = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as fh:
                fh.write(content)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    # fsync can fail on some filesystems (e.g. tmpfs in CI)
                    # but is not critical enough to block the write.
                    _LOG.debug("fsync fehlgeschlagen — ignoriere", exc_info=True)
            os.replace(tmp_path, self._config_path)
            tmp_path = None  # replace succeeded — no cleanup needed
        except Exception:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError as cleanup_exc:
                    _LOG.warning(
                        "Tempfile-Cleanup fehlgeschlagen: %s", cleanup_exc
                    )
            raise

    def _make_backup(self) -> Path:
        """Backup creation: tempfile.mkstemp for the FINAL target.

        This guarantees OS-level collision freedom — neither two threads in
        the same process (sharing `os.getpid()`) nor two separate processes
        can produce the same filename.
        """
        self._backup_dir.mkdir(parents=True, exist_ok=True)
        ts = datetime.now(UTC).strftime(BACKUP_TS_FORMAT)
        # mkstemp writes the final .bak file directly; uniqueness is OS-guaranteed.
        fd, target_name = tempfile.mkstemp(
            suffix=".bak",
            prefix=f"jarvis.toml.{ts}.",
            dir=str(self._backup_dir),
        )
        target = Path(target_name)
        try:
            content = self._config_path.read_bytes()
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    _LOG.debug("Backup-fsync fehlgeschlagen — ignoriere", exc_info=True)
        except Exception:
            try:
                target.unlink(missing_ok=True)
            except OSError as cleanup_exc:
                _LOG.warning("Backup-Cleanup fehlgeschlagen: %s", cleanup_exc)
            raise
        return target

    def _restore(self, backup_path: Path) -> None:
        """Restore from backup via tempfile + os.replace.

        Raises the original OSError hierarchy; the caller converts it
        to `RollbackError`.
        """
        if not backup_path.is_file():
            raise FileNotFoundError(f"Backup nicht gefunden: {backup_path}")
        parent = self._config_path.parent
        fd, tmp_name = tempfile.mkstemp(
            suffix=".restore",
            prefix=f"{self._config_path.name}.",
            dir=str(parent),
        )
        tmp_path = Path(tmp_name)
        try:
            content = backup_path.read_bytes()
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    _LOG.debug("Restore-fsync fehlgeschlagen", exc_info=True)
            os.replace(tmp_path, self._config_path)
        except Exception:
            tmp_path.unlink(missing_ok=True)
            raise

    def _gc_backups(self) -> None:
        """Plan-§7.2 step 10 + cap per prompt.

        Two-stage logic (with floor `backup_min_keep`):
          1. Cap: if count > `max_backups` → remove oldest FIFO,
             but never drop below `backup_min_keep`.
          2. Age: from the remaining entries remove all that are older than
             `backup_max_age_days`, but never drop below `backup_min_keep`.
        """
        if not self._backup_dir.exists():
            return
        backups = sorted(
            self._backup_dir.glob(BACKUP_FILE_GLOB),
            key=lambda p: p.stat().st_mtime,
        )  # oldest first
        if len(backups) <= self._backup_min_keep:
            return

        # Stage 1 — cap to max_backups (FIFO)
        while len(backups) > self._max_backups and len(backups) > self._backup_min_keep:
            oldest = backups[0]
            try:
                oldest.unlink()
            except OSError as exc:
                _LOG.warning("Backup-GC unlink failed (cap): %s — %s", oldest, exc)
                break
            backups.pop(0)

        # Stage 2 — age-based, with floor
        age_threshold = self._backup_max_age_days * 86400
        now = time.time()
        while len(backups) > self._backup_min_keep:
            oldest = backups[0]
            try:
                age = now - oldest.stat().st_mtime
            except OSError:
                break
            if age <= age_threshold:
                break
            try:
                oldest.unlink()
            except OSError as exc:
                _LOG.warning("Backup-GC unlink failed (age): %s — %s", oldest, exc)
                break
            backups.pop(0)

    def _dispatch_reloaded(self, request: MutationRequest) -> None:
        """Fire-and-forget `ConfigReloaded` event onto the bus."""
        if self._bus is None:
            return
        event = ConfigReloaded(
            trace_id=request.correlation_id,
            timestamp_ns=time.time_ns(),
            source_layer="self_mod",
            changed_keys=(request.path,),
        )
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            try:
                asyncio.run(self._bus.publish(event))
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("Bus-Dispatch (sync) fehlgeschlagen: %s", exc)
            return
        loop.create_task(self._bus.publish(event))

    def _audit_failure(
        self,
        request: MutationRequest,
        *,
        old_value: Any,
        error: str,
        rolled_back: bool = False,
    ) -> None:
        self._audit.record(
            AuditEvent(
                source=request.source,
                requested_by=request.actor,
                path=request.path,
                old_value=old_value,
                new_value=request.new_value,
                ok=False,
                rolled_back=rolled_back,
                error=error,
            )
        )


# ----------------------------------------------------------------------
# Module-Level-Helpers
# ----------------------------------------------------------------------


def _default_loader(path: Path) -> JarvisConfig:
    """Wrapper around `load_config` so the loader is injectable (tests)."""
    return load_config(config_file=path)


def _summarize_error(exc: BaseException) -> str:
    """Truncates a Pydantic or reload exception to an audit-suitable
    one-liner format without a stacktrace."""
    msg = str(exc).strip()
    if not msg:
        msg = type(exc).__name__
    # First line only, max 200 characters
    first = msg.splitlines()[0]
    return first[:200] if len(first) > 200 else first


__all__ = ["BACKUP_FILE_GLOB", "DEFAULT_BACKUP_SUBDIR", "AtomicConfigWriter"]
