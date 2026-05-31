"""Lifecycle-Manager: State-Transitions + optionales Git-Auto-Versioning.

Git-Support ist optional: wenn ``gitpython`` nicht installiert ist oder
``autoversion=False`` gesetzt wurde, arbeitet der Manager rein in-memory.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from .schema import Skill, SkillLifecycleState

try:
    import git as _git  # type: ignore
    _HAVE_GIT = True
except Exception:  # pragma: no cover
    _git = None  # type: ignore
    _HAVE_GIT = False

log = logging.getLogger(__name__)


_ALLOWED_TRANSITIONS: dict[SkillLifecycleState, set[SkillLifecycleState]] = {
    SkillLifecycleState.DRAFT: {SkillLifecycleState.VALIDATED, SkillLifecycleState.DISABLED},
    SkillLifecycleState.VALIDATED: {
        SkillLifecycleState.ACTIVE,
        SkillLifecycleState.DRAFT,
        SkillLifecycleState.DISABLED,
    },
    SkillLifecycleState.ACTIVE: {
        SkillLifecycleState.DISABLED,
        SkillLifecycleState.DRAFT,
    },
    SkillLifecycleState.DISABLED: {
        SkillLifecycleState.VALIDATED,
        SkillLifecycleState.ACTIVE,
    },
}


@dataclass
class AuditEntry:
    """Ein Eintrag im In-Memory-Audit-Log."""
    skill_name: str
    from_state: SkillLifecycleState
    to_state: SkillLifecycleState
    timestamp: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""


class LifecycleManager:
    """Verwaltet Skill-State-Transitions + optional Git-Commits."""

    def __init__(self, root: Path, autoversion: bool = True) -> None:
        self.root = Path(root)
        self.autoversion = autoversion and _HAVE_GIT and shutil.which("git") is not None
        self._audit: list[AuditEntry] = []
        self._repo: _git.Repo | None = None  # type: ignore[name-defined]
        if self.autoversion:
            self._ensure_repo()

    # ------------------------------------------------------------------
    # Git-Init
    # ------------------------------------------------------------------

    def _ensure_repo(self) -> None:
        if not _HAVE_GIT:
            self.autoversion = False
            return
        try:
            self.root.mkdir(parents=True, exist_ok=True)
            try:
                self._repo = _git.Repo(self.root)  # type: ignore[union-attr]
            except _git.InvalidGitRepositoryError:  # type: ignore[attr-defined]
                self._repo = _git.Repo.init(self.root)  # type: ignore[union-attr]
                log.info("skill-root initialisiert als git-repo: %s", self.root)
            except _git.NoSuchPathError:  # type: ignore[attr-defined]
                self.root.mkdir(parents=True, exist_ok=True)
                self._repo = _git.Repo.init(self.root)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log.warning("git auto-init failed: %s", exc)
            self.autoversion = False
            self._repo = None

    # ------------------------------------------------------------------
    # State-Transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        skill: Skill,
        from_state: SkillLifecycleState,
        to_state: SkillLifecycleState,
        reason: str = "",
    ) -> Skill:
        """Validiert die Transition + gibt einen neuen Skill mit neuem State zurück.

        Raises ``ValueError`` wenn die Transition nicht erlaubt ist.
        """
        if to_state not in _ALLOWED_TRANSITIONS.get(from_state, set()):
            raise ValueError(
                f"Illegal transition: {from_state.value} → {to_state.value} "
                f"(skill={skill.name})"
            )
        self._audit.append(
            AuditEntry(
                skill_name=skill.name,
                from_state=from_state,
                to_state=to_state,
                reason=reason,
            )
        )
        return Skill(
            path=skill.path,
            frontmatter=skill.frontmatter,
            body=skill.body,
            state=to_state,
            body_hash=skill.body_hash,
            error=skill.error,
        )

    @property
    def audit_log(self) -> list[AuditEntry]:
        return list(self._audit)

    # ------------------------------------------------------------------
    # Git-Commits
    # ------------------------------------------------------------------

    def commit_change(self, skill: Skill, message: str) -> str | None:
        """Committet die SKILL.md-Änderung. Gibt Commit-Hash oder None zurück."""
        if not self.autoversion or self._repo is None:
            return None
        try:
            rel = skill.path.relative_to(self.root)
        except ValueError:
            return None
        try:
            self._repo.index.add([str(rel).replace("\\", "/")])
            commit = self._repo.index.commit(message)
            return commit.hexsha
        except Exception as exc:  # noqa: BLE001
            log.warning("git commit failed: %s", exc)
            return None
