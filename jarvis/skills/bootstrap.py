"""First-run bootstrap for the user skill folder.

Copies the bundled builtin skills from ``jarvis/skills/builtin/`` into
``user_skills_dir()`` if they aren't already there. Idempotent — safe to call
on every start; existing user edits are never overwritten.

Why copy at all (instead of just loading from ``builtin/``)?

- The ``SkillRegistry`` watcher (``jarvis/skills/registry.py``) watches *one*
  root. When all skills live in one place, hot reload works out of the box.
- The user should be able to *see* and inspect builtins. Hidden read-only
  copies in the site-packages directory are opaque to skill authors.
- Admin edit protection (see ``skills_routes.py``) can tell what's a builtin
  via a path check (name in ``BUILTIN_SKILL_NAMES``) — the rest is user space.

Bootstrap versioning: a ``.bootstrap-version`` file in the user skills dir
stores the last bootstrapped package version. On a mismatch, missing skills
are copied in again (existing ones are left alone). That's how, e.g., a new
builtin skill added in a later version automatically arrives without
destroying user customizations.
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
from pathlib import Path

from jarvis.core.paths import ensure_user_dirs, user_skills_dir

from .builtin import BUILTIN_SKILL_NAMES, BUILTIN_SKILLS_DIR
from .schema import RESOURCE_KINDS

log = logging.getLogger(__name__)

BOOTSTRAP_VERSION_FILE = ".bootstrap-version"
BOOTSTRAP_VERSION = "3"
SHIPPED_HASHES_FILE = ".shipped-hashes.json"

# One-time v2→v3 migration aid (AD-S8): SHA-256 of every builtin SKILL.md as
# shipped BEFORE the 2026-06-09 instruction-skill migration. Installs that
# bootstrapped under v2 have no `.shipped-hashes.json` manifest yet — a user
# copy whose hash appears here was never edited and is safe to refresh.
_V2_SHIPPED_HASHES: dict[str, str] = {
    "control-api": "166153d84ce003c8743b31822def21ba09c9078a577556766d2853fa5bc111b1",
    "deep-work-mode": "abb9592728ec381a9a67c5ad2116db943f82cf6c1ac3461569d87e4bdc0219ba",
    "jarvis-doc-author": "5f294861b4dbc553c213780e8e55d10e417eabc2131441ce64246c15323e18f7",
    "memory-save": "9e4113d367389362e46c1ab0ad961893bc14e486f122631d73c48481a6b6b650",
    "morning-routine": "200f90780791f9fdc43fa59cc24ed0ff2244aac048ccda57d8bca039a09140c7",
    "plugin-asana": "ef2e2f19cc9cbef6ab63a7edea07803ec1a8b2c120a6c027e821ca98363d92ce",
    "plugin-cloudflare": "9318795f36b34adf364bb054c5a70a7006e28308fbe5dc1ea76a4839456ebb96",
    "plugin-discord": "b67e26f10d7e0d17529a1bf6ac0248f2a05b5696394730e1d05280424a59d940",
    "plugin-github": "dcb55262f8f11e93fb132a83ca3a321c0acdd6c63f11a12fe607c19655c58c7d",
    "plugin-gmail": "81c3f7057956222b26674ff9a6a50b85d734939dd5b2e039c12781c962c4680e",
    "plugin-google_drive": "162c1d08718904f0e25b0a8546d76fd92eaf64df492bb95c10a2e1609b7403e4",
    "plugin-linear": "76001a3ae9db52670fe2153ee9bea3b9f54a26f384964e9da7c7046b0e2c5ff0",
    "plugin-notion": "5abe395949dcc1a6a0410e5309d5e237dcefcb52d2ec44b82010493f1898f90e",
    "plugin-slack": "bd61e860e36b1d67c7663d3042defb91266f5033205e732b824a2c58f3426809",
    "plugin-stripe": "3b868d9cb07def43a341dda0a00ba84be693ddefd3bcd68b7e28e8eac096b9e0",
    "plugin-supabase": "554bb9de1823cbe832054940b29b7a87f6f4aa2aebae76c9e812ef585e78c2d7",
    "plugin-vercel": "92b3a53072addffa35af4090733c29c07d9f220198ec28f7109035d13bf83c73",
    "skill-creator": "fce6f964195f51427a850ddf1663e44143585454e3b3b59e1b5f0d2f1a742d33",
}


def ensure_user_skills_dir() -> Path:
    """Creates ``user_skills_dir()`` and copies in any missing builtin skills.

    Two cases per builtin:

    1. **Brand new:** the user doesn't have the skill folder yet -> a full
       ``copytree`` from the builtin root to the user root.
    2. **Upgrade:** the user already has a ``SKILL.md`` (may be edited, must
       not be overwritten), but the builtin gained bundle sibling folders
       -> pull in only the missing child folders.

    This keeps the "never touch a user-edited SKILL.md" guarantee while still
    rolling out new bundle resources (``references/``, ``scripts/``, …)
    automatically.

    Returns the user skills path so callers can use it directly as the
    registry root.
    """
    ensure_user_dirs()
    dst_root = user_skills_dir()
    shipped = _load_shipped_hashes(dst_root)

    copied: list[str] = []
    refreshed: list[str] = []
    kept_edited: list[str] = []
    upgraded: list[str] = []
    new_manifest: dict[str, str] = {}
    for name in BUILTIN_SKILL_NAMES:
        src = BUILTIN_SKILLS_DIR / name
        dst = dst_root / name
        if not src.exists():
            log.warning("builtin skill '%s' missing from package — skip", name)
            continue
        src_md = src / "SKILL.md"
        try:
            new_manifest[name] = _sha256(src_md)
        except OSError:
            log.warning("builtin skill '%s' has no readable SKILL.md — skip", name)
            continue
        dst_md = dst / "SKILL.md"
        if not dst_md.exists():
            # Case 1: brand new — full copy.
            try:
                shutil.copytree(src, dst, dirs_exist_ok=True)
                copied.append(name)
            except Exception as exc:  # noqa: BLE001
                log.exception("copy builtin skill '%s' failed: %s", name, exc)
            continue

        # Case 2: content refresh (AD-S8, v3). Overwrite ONLY when the user
        # copy matches a known previously-shipped hash — i.e. it was never
        # edited. Known hashes: the persisted manifest plus the static
        # v2 migration map.
        try:
            dst_hash = _sha256(dst_md)
        except OSError:
            dst_hash = ""
        if dst_hash and dst_hash != new_manifest[name]:
            known = {shipped.get(name), _V2_SHIPPED_HASHES.get(name)}
            if dst_hash in known:
                try:
                    shutil.copy2(src_md, dst_md)
                    refreshed.append(name)
                except Exception as exc:  # noqa: BLE001
                    log.warning("refresh builtin skill '%s' failed: %s", name, exc)
            else:
                kept_edited.append(name)

        # Case 3: upgrade — pull in missing bundle sibling dirs.
        added = _sync_missing_resources(src, dst)
        if added:
            upgraded.append(f"{name} (+{','.join(added)})")

    _write_shipped_hashes(dst_root, new_manifest)
    _write_version_marker(dst_root)
    if copied:
        log.info("bootstrap: %d builtin skills copied: %s",
                 len(copied), ", ".join(copied))
    if refreshed:
        log.info("bootstrap: %d unedited builtin skills refreshed: %s",
                 len(refreshed), ", ".join(refreshed))
    if kept_edited:
        log.info("bootstrap: %d user-edited builtin skills NOT refreshed: %s",
                 len(kept_edited), ", ".join(kept_edited))
    if upgraded:
        log.info("bootstrap: %d builtin skills got new resources: %s",
                 len(upgraded), ", ".join(upgraded))
    return dst_root


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load_shipped_hashes(dst_root: Path) -> dict[str, str]:
    """Read the shipped-hashes manifest; empty dict when absent/corrupt."""
    try:
        raw = (dst_root / SHIPPED_HASHES_FILE).read_text(encoding="utf-8")
        data = json.loads(raw)
        if isinstance(data, dict):
            return {str(k): str(v) for k, v in data.items()}
    except (OSError, ValueError):
        pass
    return {}


def _write_shipped_hashes(dst_root: Path, manifest: dict[str, str]) -> None:
    """Persist the currently-shipped hashes. Failures are non-fatal."""
    try:
        (dst_root / SHIPPED_HASHES_FILE).write_text(
            json.dumps(manifest, indent=1, sort_keys=True), encoding="utf-8"
        )
    except OSError:  # pragma: no cover
        log.warning("could not write %s", SHIPPED_HASHES_FILE)


def _sync_missing_resources(src: Path, dst: Path) -> list[str]:
    """Copies sibling folders (references/scripts/assets/agents) that exist
    in the builtin but are missing from the user dir. Existing user folders
    are **not** touched — even if the builtin has new files in them. This is
    the conservative default policy; an explicit "force-sync" mode would be
    a later extension.
    """
    added: list[str] = []
    for kind in RESOURCE_KINDS:
        src_kind = src / kind
        dst_kind = dst / kind
        if not src_kind.is_dir():
            continue
        if dst_kind.exists():
            continue  # user already has the folder — hands off
        try:
            shutil.copytree(src_kind, dst_kind)
            added.append(kind)
        except Exception as exc:  # noqa: BLE001
            log.warning("sync resource '%s/%s' failed: %s", dst.name, kind, exc)
    return added


def _write_version_marker(dst_root: Path) -> None:
    """Writes the bootstrap version marker. Failures are swallowed —
    the marker is pure info, not a blocker."""
    try:
        (dst_root / BOOTSTRAP_VERSION_FILE).write_text(
            BOOTSTRAP_VERSION, encoding="utf-8"
        )
    except OSError:  # pragma: no cover
        pass


__all__ = ["ensure_user_skills_dir", "BOOTSTRAP_VERSION"]
