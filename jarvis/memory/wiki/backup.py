"""BackupManager — tar.gz snapshot + single-file restore for the wiki vault.

Phase B1, Instance C. Companion to :class:`AtomicWriter`.

The vault sits at ``<repo_root>/wiki/obsidian-vault/`` (gitignored — personal
data). Each :func:`AtomicWriter.apply` call takes exactly one snapshot
before touching disk. On a per-page validation failure, that snapshot
is the only source the writer trusts to restore the offending file.

Design notes
------------
* The tar archive is stored at ``<repo_root>/wiki-backups/wiki-<ts>.tar.gz``.
  The naming matches the existing migration backup pattern in
  ``scripts/wiki_migrate_v0_to_v1.py:make_backup`` so operators only have
  one mental model for "backup files of the wiki".
* Archive member names are **vault-relative** (``entities/alex.md`` —
  *not* the absolute path on the operator's disk). This makes restore
  straightforward and prevents leaking the host filesystem layout.
* ``_archive/`` and ``attachments/`` are skipped — they can be large and
  are not part of the curated wiki surface.
* Rotation keeps the ``max_backups`` most recent archives and deletes
  the rest. Failures inside rotation are logged but never raised — a
  failed GC must not turn a successful ingest into a failure.
* The class holds no mutable state besides configuration. Reentrancy
  during a single ``apply()`` call is the caller's job (AtomicWriter
  serialises via its own lock).
"""
from __future__ import annotations

import datetime as _dt
import logging
import os
import tarfile
import tempfile
from pathlib import Path

log = logging.getLogger(__name__)

BACKUP_FILENAME_GLOB = "wiki-*.tar.gz"
BACKUP_TS_FORMAT = "%Y%m%d%H%M%S"
DEFAULT_MAX_BACKUPS = 10

# Subdirectories inside the vault that are intentionally NOT backed up.
# They can be large (attachments) or already represent retired state
# (_archive). The wiki curator never writes into either directory, so
# they are not part of the rollback surface either.
EXCLUDED_VAULT_DIRS: frozenset[str] = frozenset({"_archive", "attachments"})


class BackupError(RuntimeError):
    """Raised when a backup snapshot or restore could not complete.

    A :class:`BackupError` from ``snapshot()`` means *no* archive was
    written and the caller must abort the write. From ``restore()`` it
    means the archive exists but the requested member could not be put
    back on disk — the vault may now be in a half-applied state and the
    caller should surface the error loudly.
    """


class BackupManager:
    """Tar-based snapshot/restore helper for the wiki vault.

    Parameters
    ----------
    vault_root:
        Absolute path to the vault root (``wiki/obsidian-vault``). Must exist.
    backup_dir:
        Absolute path to the directory that holds the ``.tar.gz``
        archives (``wiki-backups`` by default). Created on first
        snapshot if missing.
    max_backups:
        Rotation cap. The N most recent archives are kept; older ones
        are deleted at the end of each :meth:`rotate` call.
    """

    def __init__(
        self,
        vault_root: Path,
        backup_dir: Path,
        *,
        max_backups: int = DEFAULT_MAX_BACKUPS,
    ) -> None:
        if max_backups < 1:
            raise ValueError(f"max_backups must be >= 1 (got {max_backups})")
        self._vault_root = Path(vault_root).resolve()
        self._backup_dir = Path(backup_dir).resolve()
        self._max_backups = max_backups

    @property
    def vault_root(self) -> Path:
        return self._vault_root

    @property
    def backup_dir(self) -> Path:
        return self._backup_dir

    @property
    def max_backups(self) -> int:
        return self._max_backups

    # ------------------------------------------------------------------
    # Snapshot
    # ------------------------------------------------------------------

    def snapshot(self, *, now: _dt.datetime | None = None) -> Path:
        """Take a tar.gz snapshot of the vault.

        Excludes :data:`EXCLUDED_VAULT_DIRS` (``_archive``, ``attachments``).
        Archive members are stored with vault-relative paths so that
        :meth:`restore` can address any file by its in-vault path.

        Returns the absolute path to the freshly written archive.
        Raises :class:`BackupError` if the vault is missing or the
        archive cannot be written.
        """
        if not self._vault_root.is_dir():
            raise BackupError(
                f"vault root does not exist or is not a directory: {self._vault_root}"
            )

        self._backup_dir.mkdir(parents=True, exist_ok=True)
        ts = (now or _dt.datetime.now()).strftime(BACKUP_TS_FORMAT)
        target = self._backup_dir / f"wiki-{ts}.tar.gz"

        # If a snapshot in the same second already exists, disambiguate
        # via tempfile.mkstemp so two rapid apply() calls do not collide.
        if target.exists():
            fd, alt_name = tempfile.mkstemp(
                suffix=".tar.gz",
                prefix=f"wiki-{ts}.",
                dir=str(self._backup_dir),
            )
            os.close(fd)
            target = Path(alt_name)

        try:
            with tarfile.open(target, "w:gz") as tar:
                for item in self._iter_vault_members():
                    arcname = item.relative_to(self._vault_root).as_posix()
                    tar.add(item, arcname=arcname, recursive=False)
        except OSError as exc:
            # Best-effort cleanup of a partial archive on failure.
            try:
                target.unlink(missing_ok=True)
            except OSError as cleanup_exc:  # pragma: no cover — diagnostic only
                log.warning("backup cleanup failed: %s", cleanup_exc)
            raise BackupError(
                f"failed to write backup {target}: {exc}"
            ) from exc

        return target

    # ------------------------------------------------------------------
    # Restore
    # ------------------------------------------------------------------

    def restore(self, backup_path: Path, target_relpath: str) -> Path:
        """Restore one vault-relative file from a snapshot.

        ``target_relpath`` is the vault-relative POSIX path as it was
        stored in the archive (e.g. ``entities/alex.md``). The file is
        written to ``<vault_root>/<target_relpath>`` via tempfile +
        :func:`os.replace`, so the restore is atomic on the same drive.

        Returns the absolute path of the restored file.
        Raises :class:`BackupError` if the archive is missing, the
        member is not present, or the write fails.

        The method **never** restores files outside the vault root —
        the archive member path is checked against the resolved target
        to prevent path-traversal (a tar with ``../etc/passwd`` would
        otherwise escape the vault).
        """
        backup_path = Path(backup_path)
        if not backup_path.is_file():
            raise BackupError(f"backup archive not found: {backup_path}")

        normalised = target_relpath.replace("\\", "/").lstrip("/")
        if not normalised:
            raise BackupError("target_relpath must not be empty")

        # Path-traversal guard: resolve the prospective target and
        # ensure it is still inside the vault root.
        prospective = (self._vault_root / normalised).resolve()
        try:
            prospective.relative_to(self._vault_root)
        except ValueError as exc:
            raise BackupError(
                f"target_relpath '{target_relpath}' escapes the vault root"
            ) from exc

        try:
            with tarfile.open(backup_path, "r:gz") as tar:
                try:
                    member = tar.getmember(normalised)
                except KeyError as exc:
                    raise BackupError(
                        f"member '{normalised}' not found in {backup_path.name}"
                    ) from exc
                if not member.isfile():
                    raise BackupError(
                        f"member '{normalised}' is not a regular file"
                    )
                extracted = tar.extractfile(member)
                if extracted is None:
                    raise BackupError(
                        f"member '{normalised}' could not be extracted"
                    )
                data = extracted.read()
        except tarfile.TarError as exc:
            raise BackupError(
                f"failed to read backup {backup_path.name}: {exc}"
            ) from exc

        prospective.parent.mkdir(parents=True, exist_ok=True)
        # Tempfile in the same directory guarantees same-drive atomicity
        # of os.replace on Windows.
        fd, tmp_name = tempfile.mkstemp(
            suffix=".restore",
            prefix=f"{prospective.name}.",
            dir=str(prospective.parent),
        )
        tmp_path = Path(tmp_name)
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(data)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except OSError:
                    log.debug("restore fsync failed (non-critical)", exc_info=True)
            os.replace(tmp_path, prospective)
        except OSError as exc:
            try:
                tmp_path.unlink(missing_ok=True)
            except OSError as cleanup_exc:  # pragma: no cover
                log.warning("restore tempfile cleanup failed: %s", cleanup_exc)
            raise BackupError(
                f"failed to restore '{normalised}' to {prospective}: {exc}"
            ) from exc

        return prospective

    # ------------------------------------------------------------------
    # Rotation
    # ------------------------------------------------------------------

    def list_backups(self) -> list[Path]:
        """Return all backup archives, newest first.

        Files are sorted by mtime descending. Files that disappear
        between glob and stat (TOCTOU under concurrent rotation) are
        silently skipped.
        """
        if not self._backup_dir.is_dir():
            return []
        snapshot: list[tuple[Path, float]] = []
        for path in self._backup_dir.glob(BACKUP_FILENAME_GLOB):
            try:
                mtime = path.stat().st_mtime
            except OSError:
                continue
            snapshot.append((path, mtime))
        snapshot.sort(key=lambda pair: pair[1], reverse=True)
        return [path for path, _ in snapshot]

    def rotate(self) -> list[Path]:
        """Delete archives beyond ``max_backups``. Returns the deleted paths.

        Best-effort: an :class:`OSError` on a single unlink is logged
        and the rotation continues with the next candidate. The mutation
        that triggered the rotation must not be invalidated by hygiene.
        """
        all_backups = self.list_backups()
        if len(all_backups) <= self._max_backups:
            return []
        deletable = all_backups[self._max_backups:]
        deleted: list[Path] = []
        for path in deletable:
            try:
                path.unlink()
            except OSError as exc:
                log.warning("backup rotation unlink failed: %s — %s", path, exc)
                continue
            deleted.append(path)
        return deleted

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    def _iter_vault_members(self) -> list[Path]:
        """List every file inside the vault that should be in the archive.

        Walks ``vault_root`` excluding :data:`EXCLUDED_VAULT_DIRS` at the
        top level. Returns absolute paths. Order is deterministic
        (sorted) so the resulting archive is reproducible for the same
        vault state.
        """
        members: list[Path] = []
        for root, dirs, files in os.walk(self._vault_root):
            # Mutate dirs in-place so os.walk skips the excluded ones.
            root_path = Path(root)
            if root_path == self._vault_root:
                dirs[:] = [d for d in dirs if d not in EXCLUDED_VAULT_DIRS]
            for filename in files:
                members.append(root_path / filename)
        members.sort()
        return members


__all__ = [
    "BACKUP_FILENAME_GLOB",
    "BACKUP_TS_FORMAT",
    "DEFAULT_MAX_BACKUPS",
    "EXCLUDED_VAULT_DIRS",
    "BackupError",
    "BackupManager",
]
