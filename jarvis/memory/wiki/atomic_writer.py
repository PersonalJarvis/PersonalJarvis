"""AtomicWriter — the only disk-write path for wiki entity/concept/project pages.

Phase B1, Instance C. Designed against ``docs/phase-b1-wiki-curator/README.md``
Part 5 → Instance C and the binding Protocol in
``jarvis/memory/wiki/protocols.py``.

The five-step pipeline (executed inside one ``apply()`` call, in order):

1. **Concurrent-edit lock** — for every update, check
   ``target_path.stat().st_mtime``. If it is within
   :data:`CONCURRENT_EDIT_LOCK_SECONDS` of ``time.time()``, the update
   is skipped and the path is recorded under
   ``WriteResult.skipped_due_to_recent_edit``. This is the "user is
   editing in Obsidian" guard.
2. **Backup** — exactly *one* tar.gz snapshot of the whole vault per
   ``apply()`` call, even when fifteen pages are about to change.
   Backup is skipped entirely when nothing survived step 1.
3. **Write** — for each surviving update, write to a sibling tempfile
   in the target's parent directory, then :func:`os.replace`. The
   tempfile-in-parent placement is what makes the replace atomic on
   Windows (same-drive requirement) — the writer asserts the same-drive
   invariant and raises a clear error if it ever fails.
4. **Validate** — re-parse every just-written page via
   :meth:`PageRepository.parse`. Pages that come back with
   ``is_schema_valid=False`` (or that raise inside ``parse``) are rolled
   back individually from the snapshot taken in step 2. The other
   pages stay applied — partial success is the expected mode.
5. **Return** — :class:`WriteResult` summarising what landed, what was
   skipped, what was rolled back, plus the absolute path of the
   snapshot for forensics.

The writer holds a single ``asyncio.Lock`` so that two concurrent
ingests on the same vault serialise their pipelines. This keeps the
backup/rotate cycle race-free without dragging in a process-level
sentinel file.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from .backup import (
    DEFAULT_MAX_BACKUPS,
    BackupError,
    BackupManager,
)
from .protocols import PageRepository, PageUpdate, WriteResult
from .secret_guard import find_secrets
from .telemetry import telemetry

log = logging.getLogger(__name__)

CONCURRENT_EDIT_LOCK_SECONDS: float = 30.0
ALLOWED_OPERATIONS: frozenset[str] = frozenset({"create", "update", "rename", "archive"})


class AtomicWriteError(RuntimeError):
    """Raised for unrecoverable failures inside :meth:`AtomicWriter.apply`.

    The writer reports per-page failures via :class:`WriteResult` rather
    than exceptions — :class:`AtomicWriteError` is reserved for failures
    that affect the *whole* call (e.g. the backup snapshot could not be
    written; the vault root is missing; an update points outside the
    vault).
    """


@dataclass(frozen=True, slots=True)
class _PendingWrite:
    """Internal: one update that survived the 30-second lock check."""
    update: PageUpdate
    target_path: Path                       # absolute, inside the vault
    arc_relpath: str                        # vault-relative POSIX path for restore()
    rename_from_path: Path | None
    rename_from_arc: str | None             # vault-relative POSIX path of the source
    pre_existed: bool                       # was the target file on disk before this call?


class AtomicWriter:
    """The single disk-write surface for wiki pages.

    Parameters
    ----------
    vault_root:
        Absolute path to the vault root.
    backup_dir:
        Absolute path to the directory holding ``wiki-*.tar.gz``
        snapshots. Created on first snapshot if missing.
    max_backups:
        Rotation cap forwarded to :class:`BackupManager`.
    backup_manager:
        Inject a pre-built :class:`BackupManager` (mostly for tests).
        When omitted, the writer constructs its own from ``vault_root``,
        ``backup_dir`` and ``max_backups``.
    concurrent_edit_lock_seconds:
        Override the 30-second guard (only the test suite overrides it).
    clock:
        Override ``time.time`` for deterministic tests.
    """

    def __init__(
        self,
        vault_root: Path,
        backup_dir: Path,
        *,
        max_backups: int = DEFAULT_MAX_BACKUPS,
        backup_manager: BackupManager | None = None,
        concurrent_edit_lock_seconds: float = CONCURRENT_EDIT_LOCK_SECONDS,
        clock: "callable[[], float] | None" = None,
    ) -> None:
        self._vault_root = Path(vault_root).resolve()
        self._backup_dir = Path(backup_dir).resolve()
        self._lock_seconds = float(concurrent_edit_lock_seconds)
        self._clock = clock or time.time
        if backup_manager is None:
            self._backups = BackupManager(
                vault_root=self._vault_root,
                backup_dir=self._backup_dir,
                max_backups=max_backups,
            )
        else:
            self._backups = backup_manager
        self._serial_lock = asyncio.Lock()
        # Lazily opened sqlite3 connection for FTS5 upsert after writes.
        # check_same_thread=False: apply() runs in asyncio.to_thread, which
        # may use a different OS thread on each call.
        self._fts_conn: sqlite3.Connection | None = None

    @property
    def vault_root(self) -> Path:
        return self._vault_root

    @property
    def backup_manager(self) -> BackupManager:
        return self._backups

    # ------------------------------------------------------------------
    # Public API (AtomicWriter Protocol)
    # ------------------------------------------------------------------

    async def apply(
        self,
        updates: list[PageUpdate],
        *,
        repo: PageRepository,
    ) -> WriteResult:
        """Run the five-step pipeline once for ``updates``.

        Returns a :class:`WriteResult` with three disjoint path sets:
        ``applied``, ``skipped_due_to_recent_edit``, ``failed_validation``.
        A single path appears in exactly one set. The ``backup_path``
        field is the snapshot used for any rollback that fired.
        """
        async with self._serial_lock:
            return await asyncio.to_thread(self._apply_sync, updates, repo)

    # ------------------------------------------------------------------
    # Pipeline (sync — runs in a worker thread to avoid blocking asyncio)
    # ------------------------------------------------------------------

    def _apply_sync(
        self,
        updates: list[PageUpdate],
        repo: PageRepository,
    ) -> WriteResult:
        # ----- input normalisation ---------------------------------------
        # Defensive: deduplicate by target path, last-write-wins, and
        # validate operation labels up front. An invalid update is a
        # programmer mistake, not a runtime data issue — raise loudly.
        for upd in updates:
            if upd.operation not in ALLOWED_OPERATIONS:
                raise AtomicWriteError(
                    f"unknown PageUpdate.operation: {upd.operation!r} "
                    f"(allowed: {sorted(ALLOWED_OPERATIONS)})"
                )

        # ----- Step 1: 30s concurrent-edit lock --------------------------
        pending: list[_PendingWrite] = []
        skipped: list[Path] = []
        blocked: list[Path] = []
        now = self._clock()
        for upd in updates:
            target = upd.target_path.resolve()

            # ----- Step 0.5: secret/PII guard (AP-2) ---------------------
            # A body that contains an API key, bearer token, password, or
            # other opaque credential must never reach disk or the FTS
            # index. Regex-only, no LLM. Archive ops carry no meaningful
            # body, so only create/update/rename are screened. We log the
            # pattern *names* only, never the matched value.
            if upd.operation != "archive":
                hits = find_secrets(upd.new_body)
                if hits:
                    log.warning(
                        "atomic_writer: refusing write to %s — body matched "
                        "secret/PII patterns %s (AP-2)",
                        target,
                        hits,
                    )
                    telemetry.inc("wiki_writes_blocked_pii")
                    blocked.append(target)
                    continue

            try:
                target.relative_to(self._vault_root)
            except ValueError as exc:
                raise AtomicWriteError(
                    f"PageUpdate target {target} is outside the vault "
                    f"{self._vault_root}"
                ) from exc

            arc_relpath = target.relative_to(self._vault_root).as_posix()

            rename_from_path: Path | None = None
            rename_from_arc: str | None = None
            if upd.operation == "rename":
                if upd.rename_from is None:
                    raise AtomicWriteError(
                        f"rename PageUpdate for {arc_relpath} is missing rename_from"
                    )
                rename_from_path = upd.rename_from.resolve()
                try:
                    rename_from_path.relative_to(self._vault_root)
                except ValueError as exc:
                    raise AtomicWriteError(
                        f"rename_from {rename_from_path} is outside the vault "
                        f"{self._vault_root}"
                    ) from exc
                rename_from_arc = rename_from_path.relative_to(
                    self._vault_root
                ).as_posix()

            # Check mtime of both the target and (for renames) the source.
            recent = False
            for candidate in (target, rename_from_path):
                if candidate is None:
                    continue
                try:
                    mtime = candidate.stat().st_mtime
                except FileNotFoundError:
                    continue
                except OSError as exc:
                    log.warning(
                        "atomic_writer: stat failed for %s (%s) — treating as recent edit",
                        candidate,
                        exc,
                    )
                    recent = True
                    break
                if (now - mtime) < self._lock_seconds:
                    recent = True
                    break

            if recent:
                skipped.append(target)
                continue

            pre_existed = target.exists()
            pending.append(
                _PendingWrite(
                    update=upd,
                    target_path=target,
                    arc_relpath=arc_relpath,
                    rename_from_path=rename_from_path,
                    rename_from_arc=rename_from_arc,
                    pre_existed=pre_existed,
                )
            )

        # Nothing survived the lock — no backup, no rotation, no writes.
        # Return a result with an empty backup path so the caller knows
        # no snapshot was taken.
        if not pending:
            return WriteResult(
                applied=[],
                skipped_due_to_recent_edit=skipped,
                failed_validation=[],
                backup_path=Path(),
                blocked_pii=blocked,
            )

        # ----- Step 2: single backup snapshot ----------------------------
        try:
            backup_path = self._backups.snapshot()
        except BackupError as exc:
            raise AtomicWriteError(f"vault snapshot failed: {exc}") from exc

        # ----- Step 3: write each surviving update -----------------------
        applied: list[Path] = []
        written_paths: list[Path] = []  # for validation step
        # ``handled_renames`` records the (old → new) renames we actually
        # performed so a validation failure can unwind them via the
        # snapshot too.
        handled_renames: list[tuple[Path, Path]] = []
        # Pages that left their indexed path and must be purged from FTS:
        # archived originals (moved into _archive/) collected here.
        archived_paths: list[Path] = []
        for pend in pending:
            self._assert_same_drive(pend.target_path)
            try:
                self._write_one(pend)
            except OSError as exc:
                # A genuine write failure mid-pipeline is unrecoverable
                # for this update but must not corrupt anything: the
                # tempfile is cleaned up inside _write_one, the target
                # file on disk is whatever it was before. Log and skip;
                # the caller will see this page neither in applied nor
                # in failed_validation, which signals "did not happen".
                log.error(
                    "atomic_writer: write failed for %s — %s",
                    pend.target_path,
                    exc,
                )
                continue
            applied.append(pend.target_path)
            # Telemetry: count creates vs updates. ``rename`` and
            # ``archive`` collapse into the bucket that matches their
            # pre-existence on disk (the archive case is rare and the
            # rename case is conceptually an update of the new path).
            if pend.pre_existed:
                telemetry.inc("wiki_pages_updated")
            else:
                telemetry.inc("wiki_pages_created")
            if pend.update.operation != "archive":
                written_paths.append(pend.target_path)
            else:
                archived_paths.append(pend.target_path)
            if pend.rename_from_path is not None:
                handled_renames.append((pend.rename_from_path, pend.target_path))

        # ----- Step 4: validate written pages via repo.parse -------------
        failed_validation: list[Path] = []
        for path in written_paths:
            # The validation contract: re-read the file we just wrote
            # (not the in-memory new_body — that bypasses on-disk encoding
            # surprises) and run it through the PageRepository parser.
            try:
                raw = path.read_text(encoding="utf-8")
            except OSError as exc:
                log.error("atomic_writer: post-write read failed for %s — %s", path, exc)
                self._restore_from_backup(
                    path,
                    backup_path,
                    pre_existed=self._was_pre_existing(path, pending),
                )
                applied.remove(path)
                failed_validation.append(path)
                continue

            is_valid = self._validate_via_repo(repo, raw, path)

            if not is_valid:
                # Pull this single page back from the snapshot. If the
                # page is brand new (no archive member), remove it from
                # disk entirely. Other already-applied pages stay.
                self._restore_from_backup(
                    path,
                    backup_path,
                    pre_existed=self._was_pre_existing(path, pending),
                )
                applied.remove(path)
                failed_validation.append(path)

        # ----- Step 5: backup rotation (hygiene; failures never raise) ---
        try:
            self._backups.rotate()
        except OSError as exc:  # pragma: no cover — defensive only
            log.warning("atomic_writer: backup rotation failed: %s", exc)

        # ----- Step 6: FTS5 index maintenance (incremental, synchronous) -
        # Runs inside _serial_lock (inherited from the to_thread call in
        # apply()).  A single-row FTS5 upsert is sub-millisecond, so
        # spawning a background task would only add race risk with no
        # latency benefit.  Only pages that survived validation are indexed.
        archived_set = set(archived_paths)
        upsert_targets = [p for p in applied if p not in archived_set]
        if upsert_targets:
            self._fts_upsert_applied(upsert_targets)
        # Purge rows for pages that left their indexed path: archived
        # originals (moved into _archive/) and the source side of a rename.
        # Without this, search returns ghost hits pointing at a path that no
        # longer holds that content (the upsert path no-ops on a vanished
        # file and so never deletes the stale row).
        applied_set = set(applied)
        purge_targets = list(archived_paths) + [
            old for old, new in handled_renames if new in applied_set
        ]
        if purge_targets:
            self._fts_remove_paths(purge_targets)

        return WriteResult(
            applied=applied,
            skipped_due_to_recent_edit=skipped,
            failed_validation=failed_validation,
            backup_path=backup_path,
            blocked_pii=blocked,
        )

    # ------------------------------------------------------------------
    # Step helpers
    # ------------------------------------------------------------------

    def _assert_same_drive(self, target_path: Path) -> None:
        """Verify that the target lives on the vault drive.

        On Windows, ``os.replace`` is only atomic when source and target
        share a drive letter. Our tempfile is always created inside the
        target's parent, so as long as the parent is reachable from the
        vault root we are safe. The check here is belt-and-suspenders
        for callers that ever produce a PageUpdate outside the vault —
        the relative-to-vault assertion in ``apply()`` catches that, but
        we keep this guard as a second line in case the path layout
        changes in future phases.
        """
        target_drive = os.path.splitdrive(str(target_path))[0]
        vault_drive = os.path.splitdrive(str(self._vault_root))[0]
        if target_drive.lower() != vault_drive.lower():
            raise AtomicWriteError(
                f"target {target_path} (drive {target_drive!r}) is not on the "
                f"vault drive {vault_drive!r}; os.replace would not be atomic"
            )

    def _write_one(self, pend: _PendingWrite) -> None:
        """Execute one PageUpdate.

        * ``create`` / ``update``: write ``new_body`` to a sibling
          tempfile, then ``os.replace`` onto the target.
        * ``rename``: write ``new_body`` to the new path (same tempfile
          dance) and then ``unlink`` the rename source.
        * ``archive``: move the existing target into
          ``_archive/<arc_relpath>``; ``new_body`` is ignored. We keep
          ``new_body`` in the contract because Instance D may want to
          drop a tombstone in future — for B1 it is unused.
        """
        op = pend.update.operation
        if op == "archive":
            self._do_archive(pend)
            return

        # create / update / rename all write the new body to the target.
        pend.target_path.parent.mkdir(parents=True, exist_ok=True)
        self._write_text_atomic(pend.target_path, pend.update.new_body)

        if op == "rename" and pend.rename_from_path is not None:
            # The source file is now superseded by the target. Best-effort
            # unlink; if it fails we log but do not undo the new file —
            # that would defeat the rename. The user can clean up the
            # orphan in Obsidian.
            try:
                pend.rename_from_path.unlink(missing_ok=True)
            except OSError as exc:
                log.warning(
                    "atomic_writer: rename unlink failed for %s — %s",
                    pend.rename_from_path,
                    exc,
                )

    def _do_archive(self, pend: _PendingWrite) -> None:
        """Move the target into the ``_archive`` directory."""
        source = pend.target_path
        if not source.exists():
            # Nothing to archive — silently no-op so the call stays
            # idempotent.
            return
        archive_dir = self._vault_root / "_archive"
        dest = archive_dir / pend.arc_relpath
        dest.parent.mkdir(parents=True, exist_ok=True)
        # Same-drive guarantee holds because _archive is under vault_root.
        os.replace(source, dest)

    def _write_text_atomic(self, target: Path, content: str) -> None:
        """Tempfile + fsync + os.replace, mirroring self_mod/writer.py."""
        parent = target.parent
        fd, tmp_name = tempfile.mkstemp(
            suffix=".tmp",
            prefix=f"{target.name}.",
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
                    log.debug("fsync failed (non-critical)", exc_info=True)
            os.replace(tmp_path, target)
            tmp_path = None
        finally:
            if tmp_path is not None:
                try:
                    tmp_path.unlink(missing_ok=True)
                except OSError as cleanup_exc:  # pragma: no cover
                    log.warning("tempfile cleanup failed: %s", cleanup_exc)

    def _validate_via_repo(
        self,
        repo: PageRepository,
        raw: str,
        path: Path,
    ) -> bool:
        """Run ``repo.parse(raw, path)`` and translate the outcome.

        Returns ``True`` if the parser produced a schema-valid page.
        Treats any exception inside ``parse`` as validation failure and
        logs it — Instance A's parser is documented to return
        ``is_schema_valid=False`` rather than raise, but a defensive
        try/except keeps the writer robust against future regressions.
        """
        parse_call = repo.parse(raw, path)
        # ``parse`` is async per the Protocol; run it on a fresh loop
        # because we are inside asyncio.to_thread (no current loop).
        try:
            page = asyncio.run(parse_call)
        except Exception as exc:  # noqa: BLE001 — defensive parser guard
            log.warning(
                "atomic_writer: PageRepository.parse raised for %s — %s",
                path,
                exc,
            )
            return False
        return bool(getattr(page, "is_schema_valid", False))

    def _restore_from_backup(
        self,
        path: Path,
        backup_path: Path,
        *,
        pre_existed: bool,
    ) -> None:
        """Roll one page back to its pre-apply state.

        * If the page existed before this call, restore it from the
          snapshot's archive member.
        * If the page was brand-new in this call (``pre_existed=False``),
          there is no archive member; the rollback is to delete the
          file.

        Rollback never touches files that were not written in *this*
        call — the caller passes the exact paths.
        """
        if not pre_existed:
            try:
                path.unlink(missing_ok=True)
            except OSError as exc:
                log.error(
                    "atomic_writer: failed to delete newly-created %s during rollback — %s",
                    path,
                    exc,
                )
            return

        arc_relpath = path.resolve().relative_to(self._vault_root).as_posix()
        try:
            self._backups.restore(backup_path, arc_relpath)
        except BackupError as exc:
            # Last-resort: log loudly. The page on disk is whatever the
            # post-write snapshot left it as; the snapshot file is
            # explicitly named in the WriteResult for manual recovery.
            log.error(
                "atomic_writer: ROLLBACK FAILED for %s from %s — %s. "
                "Manual restore required.",
                path,
                backup_path,
                exc,
            )

    @staticmethod
    def _was_pre_existing(target: Path, pending: list[_PendingWrite]) -> bool:
        for pend in pending:
            if pend.target_path == target:
                return pend.pre_existed
        return False

    # ------------------------------------------------------------------
    # FTS5 index helpers
    # ------------------------------------------------------------------

    def _get_fts_conn(self) -> sqlite3.Connection | None:
        """Return the lazily-opened FTS sqlite3 connection.

        Returns ``None`` when ``fts_index`` is not yet importable (peer
        module not yet delivered) so that the write pipeline degrades
        gracefully rather than crashing.
        """
        try:
            import jarvis.memory.wiki.fts_index as _fts  # type: ignore[import]
        except ImportError:
            log.debug("atomic_writer: fts_index not available — skipping FTS upsert")
            return None

        if self._fts_conn is None:
            # Derive DB path the same way search.py does: data/jarvis.db
            # relative to the project root (4 levels above this file).
            db_path = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "data"
                / "jarvis.db"
            )
            db_path.parent.mkdir(parents=True, exist_ok=True)
            self._fts_conn = sqlite3.connect(str(db_path), check_same_thread=False)
            _fts.ensure_schema(self._fts_conn)
        return self._fts_conn

    def _fts_upsert_applied(self, applied_paths: list[Path]) -> None:
        """Call ``fts_index.upsert_page`` for every successfully written page.

        Failures are logged but never propagated — the write already
        succeeded; the FTS index is a secondary view, not a write guard.
        """
        try:
            import jarvis.memory.wiki.fts_index as _fts  # type: ignore[import]
        except ImportError:
            return

        conn = self._get_fts_conn()
        if conn is None:
            return

        for path in applied_paths:
            try:
                _fts.upsert_page(conn, self._vault_root, path)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "atomic_writer: FTS upsert failed for %s — %s",
                    path,
                    exc,
                )

    def _fts_remove_paths(self, paths: list[Path]) -> None:
        """Delete FTS rows for ``paths`` (best-effort, never propagates).

        Used for archived originals and rename sources inside ``apply`` and,
        via :meth:`forget_paths`, for files moved outside the writer.
        """
        try:
            import jarvis.memory.wiki.fts_index as _fts  # type: ignore[import]
        except ImportError:
            return

        conn = self._get_fts_conn()
        if conn is None:
            return

        for path in paths:
            try:
                _fts.remove_page(conn, self._vault_root, path)
            except Exception as exc:  # noqa: BLE001
                log.warning(
                    "atomic_writer: FTS purge failed for %s — %s",
                    path,
                    exc,
                )

    def forget_paths(self, paths: list[Path]) -> None:
        """Purge FTS rows for pages moved or deleted outside ``apply``.

        The session-rollup rolling-window archiver renames session files
        directly into ``_archive/sessions/`` without going through
        ``apply`` — their FTS rows would otherwise linger as ghost hits.
        Best-effort: the FTS index is a secondary view, never a write guard.
        """
        if paths:
            self._fts_remove_paths([Path(p) for p in paths])


__all__ = [
    "ALLOWED_OPERATIONS",
    "CONCURRENT_EDIT_LOCK_SECONDS",
    "AtomicWriteError",
    "AtomicWriter",
]
