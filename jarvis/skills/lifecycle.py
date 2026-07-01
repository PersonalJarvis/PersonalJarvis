"""Lifecycle manager: state transitions + optional git auto-versioning.

Git support is optional: if ``gitpython`` is not installed or
``autoversion=False`` was set, the manager works purely in-memory.
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
    """An entry in the in-memory audit log."""
    skill_name: str
    from_state: SkillLifecycleState
    to_state: SkillLifecycleState
    timestamp: datetime = field(default_factory=datetime.utcnow)
    reason: str = ""


class LifecycleManager:
    """Manages skill state transitions + optional git commits."""

    def __init__(self, root: Path, autoversion: bool = True) -> None:
        self.root = Path(root)
        self.autoversion = autoversion and _HAVE_GIT and shutil.which("git") is not None
        self._audit: list[AuditEntry] = []
        self._repo: _git.Repo | None = None  # type: ignore[name-defined]
        if self.autoversion:
            self._ensure_repo()

    # ------------------------------------------------------------------
    # Git init
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
                log.info("skill-root initialized as a git repo: %s", self.root)
            except _git.NoSuchPathError:  # type: ignore[attr-defined]
                self.root.mkdir(parents=True, exist_ok=True)
                self._repo = _git.Repo.init(self.root)  # type: ignore[union-attr]
        except Exception as exc:  # noqa: BLE001
            log.warning("git auto-init failed: %s", exc)
            self.autoversion = False
            self._repo = None

    # ------------------------------------------------------------------
    # State transitions
    # ------------------------------------------------------------------

    def transition(
        self,
        skill: Skill,
        from_state: SkillLifecycleState,
        to_state: SkillLifecycleState,
        reason: str = "",
    ) -> Skill:
        """Validates the transition + returns a new skill with the new state.

        Raises ``ValueError`` if the transition is not allowed.
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
    # Git commits
    # ------------------------------------------------------------------

    def commit_change(self, skill: Skill, message: str) -> str | None:
        """Commits the SKILL.md change. Returns a commit hash or None."""
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
