"""First-start seeding of a non-default instance's data directory.

A dev instance gets its own ``data-dev/`` (see ``jarvis.core.instance``), which
would otherwise start as a blank install: the first-run guide again, no chats to
look at, an unnamed assistant. That is not what a second window of the *same*
product should feel like, so the very first start copies the few files that
make it recognisably this user's Jarvis — and nothing else. Everything that is
live state of the running default app (sessions, missions, the wiki, the
recall store, locks, logs, the WebView profile) starts fresh: it either must not
be shared between two processes or is simply not worth a snapshot.

The copy happens exactly once — when the directory does not exist yet. A later
start never re-seeds, so whatever the dev app accumulates stays its own.
SQLite files are copied through the online backup API so an open WAL on the
source never yields a torn file.
"""

from __future__ import annotations

import logging
import shutil
import sqlite3
from pathlib import Path

from jarvis.core.instance import InstanceIdentity, current_instance

log = logging.getLogger(__name__)

#: Files copied from the default instance on the first start of another one.
SEED_FILES: tuple[str, ...] = (
    "setup_state.json",  # onboarding done — no first-run guide in the dev app
    "identity_card.json",  # the assistant's name / persona as the user set it
    "core_memory.json",  # the core memory the persona is built from
    "chats.db",  # a snapshot of the chat history, so the chat views look real
)


def _copy_sqlite(src: Path, dst: Path) -> None:
    """Consistent copy of a possibly-open SQLite database (WAL-safe)."""
    with sqlite3.connect(f"file:{src.as_posix()}?mode=ro", uri=True) as source:
        with sqlite3.connect(dst) as target:
            source.backup(target)


def ensure_instance_data_dir(
    project_root: Path,
    identity: InstanceIdentity | None = None,
    *,
    default_data_dir: Path | None = None,
) -> list[str]:
    """Create the instance's data dir on its first start, seeded from the default.

    Returns the names of the files that were seeded (empty when the directory
    already existed, for the default instance, or when there was nothing to copy).
    Never raises: a failed seed leaves the file out and the app starts without it,
    which is the blank-install behaviour it would have had anyway.
    """
    identity = identity or current_instance()
    if identity.is_default:
        return []
    target_dir = project_root / identity.data_dir_name
    if target_dir.exists():
        return []
    source_dir = default_data_dir or (project_root / "data")
    target_dir.mkdir(parents=True, exist_ok=True)
    seeded: list[str] = []
    for name in SEED_FILES:
        src = source_dir / name
        if not src.is_file():
            continue
        dst = target_dir / name
        try:
            if src.suffix == ".db":
                _copy_sqlite(src, dst)
            else:
                shutil.copyfile(src, dst)
        except (OSError, sqlite3.Error) as exc:
            # A missing seed degrades to the blank-install state for that one
            # file — worth a line, never worth failing the boot over.
            log.warning("instance %s: could not seed %s: %s", identity.name, name, exc)
            dst.unlink(missing_ok=True)
            continue
        seeded.append(name)
    log.info(
        "instance %s: created %s (seeded from %s: %s)",
        identity.name,
        target_dir,
        source_dir,
        ", ".join(seeded) or "nothing",
    )
    return seeded


__all__ = ["SEED_FILES", "ensure_instance_data_dir"]
