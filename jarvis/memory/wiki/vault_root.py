"""Single canonical vault-root resolution (spec A7).

Every consumer of ``[wiki_integration].vault_root`` resolves through
:func:`resolve_vault_root`. A relative root anchors to the repo root
(``jarvis/core/paths.repo_root()``), never to ``Path.cwd()`` — a desktop
launch from another directory used to read/write a different vault than
the UI displayed.

Legacy migration: installs that ran with the old CWD-based resolution may
hold a populated vault under ``<old cwd>/wiki/obsidian-vault``. When that
legacy vault is populated and the anchored one is empty/missing, the
populated one wins; the ambiguity is flagged for the health surface
instead of silently forking the vault.
"""
from __future__ import annotations

import threading
from dataclasses import dataclass
from pathlib import Path

from jarvis.core.paths import repo_root

_DEFAULT_RELATIVE = Path("wiki/obsidian-vault")

_lock = threading.Lock()
_last: VaultRootResolution | None = None


@dataclass(frozen=True, slots=True)
class VaultRootResolution:
    path: Path
    source: str  # "absolute" | "repo_root" | "legacy_cwd"
    legacy_conflict: bool


def _non_empty_dir(p: Path) -> bool:
    try:
        return p.is_dir() and any(p.iterdir())
    except OSError:
        return False


def resolve_vault_root(
    raw: str | Path | None, *, cwd: Path | None = None,
) -> VaultRootResolution:
    """Resolve the configured vault root to an absolute path.

    ``cwd`` is injectable for tests; production callers omit it.
    """
    raw_path = Path(raw) if raw else _DEFAULT_RELATIVE
    if raw_path.is_absolute():
        res = VaultRootResolution(raw_path.resolve(), "absolute", False)
        return _remember(res)

    anchored = (repo_root() / raw_path).resolve()
    legacy = ((cwd or Path.cwd()) / raw_path).resolve()
    if legacy == anchored:
        return _remember(VaultRootResolution(anchored, "repo_root", False))
    legacy_populated = _non_empty_dir(legacy)
    if legacy_populated and not _non_empty_dir(anchored):
        return _remember(VaultRootResolution(legacy, "legacy_cwd", True))
    return _remember(
        VaultRootResolution(anchored, "repo_root", legacy_populated)
    )


def _remember(res: VaultRootResolution) -> VaultRootResolution:
    global _last
    with _lock:
        _last = res
    return res


def last_resolution() -> VaultRootResolution | None:
    """Most recent resolution — read by the wiki health snapshot."""
    with _lock:
        return _last
