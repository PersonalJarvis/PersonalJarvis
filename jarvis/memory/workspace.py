"""Workspace layout for the persistent user-profile system.

Inspired by OpenClaw (`~/.openclaw/<agentId>/workspace/`), adapted for
Jarvis: we use `data/workspace/` relative to the project root so that
development and production share the same layout and the files can be
versioned (or deliberately .gitignored).

Files:

- `USER.md`       — User profile (YAML frontmatter + Markdown)
- `SOUL.md`       — Jarvis' own persona
- `BOOTSTRAP.md`  — First-run interview (self-deleting)
- `people/`       — Subdirectory with one Markdown file per person

The separation of USER.md vs. people/<name>.md is **the central firewall**
against subject confusion: the curator writes to exactly one file per fact,
and file selection depends on the subject resolver (see `curator/validator.py`).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .templates import (
    render_bootstrap_md,
    render_soul_md,
    render_user_md,
)

log = logging.getLogger(__name__)


USER_MD = "USER.md"
SOUL_MD = "SOUL.md"
BOOTSTRAP_MD = "BOOTSTRAP.md"
PEOPLE_DIR = "people"


# Filename-safe slug: e.g. "Laura Müller" → "laura_mueller"  # i18n-allow: example name demonstrating umlaut-folding logic
_UMLAUT_MAP = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "ß": "ss",  # i18n-allow: umlaut-folding lookup table used by person_slug()
                             "Ä": "Ae", "Ö": "Oe", "Ü": "Ue"})  # i18n-allow: umlaut-folding lookup table used by person_slug()
_NON_SAFE = re.compile(r"[^a-z0-9_-]+")


def person_slug(name: str) -> str:
    """Normalise a name to a safe filename slug.

    Examples:
        "Laura Müller"  → "laura_mueller"  # i18n-allow: example name demonstrating umlaut-folding logic
        "Dr. Paul O."   → "dr_paul_o"
        "Anne-Marie"    → "anne-marie"
    """
    s = name.strip().translate(_UMLAUT_MAP).lower()
    s = s.replace(" ", "_")
    s = _NON_SAFE.sub("", s)
    return s or "unknown"


@dataclass
class Workspace:
    """Central path container. Creates default files on demand."""

    root: Path

    @classmethod
    def ensure(cls, root: Path | str) -> Workspace:
        """Ensure the workspace directory and default files exist.

        Called at desktop-app boot and by BrainManager.
        Idempotent — existing files are not overwritten.
        """
        p = Path(root)
        p.mkdir(parents=True, exist_ok=True)
        (p / PEOPLE_DIR).mkdir(parents=True, exist_ok=True)

        user = p / USER_MD
        if not user.exists():
            user.write_text(render_user_md(), encoding="utf-8")
            log.info("USER.md created: %s", user)

        soul = p / SOUL_MD
        if not soul.exists():
            soul.write_text(render_soul_md(), encoding="utf-8")
            log.info("SOUL.md created: %s", soul)

        boot = p / BOOTSTRAP_MD
        # BOOTSTRAP is only created on the absolute first run (when USER.md
        # is empty or identical to the template). See `is_bootstrap_needed`.
        if not user.read_text(encoding="utf-8").strip():
            if not boot.exists():
                boot.write_text(render_bootstrap_md(), encoding="utf-8")

        return cls(root=p)

    # ------------------------------------------------------------------
    # Paths
    # ------------------------------------------------------------------

    @property
    def user_path(self) -> Path:
        return self.root / USER_MD

    @property
    def soul_path(self) -> Path:
        return self.root / SOUL_MD

    @property
    def bootstrap_path(self) -> Path:
        return self.root / BOOTSTRAP_MD

    @property
    def people_dir(self) -> Path:
        return self.root / PEOPLE_DIR

    def person_path(self, name: str) -> Path:
        return self.people_dir / f"{person_slug(name)}.md"

    def list_people(self) -> list[Path]:
        if not self.people_dir.exists():
            return []
        return sorted(p for p in self.people_dir.glob("*.md") if p.is_file())

    def is_bootstrap_needed(self) -> bool:
        """True if the first-run interview has not yet been completed.

        Heuristic: BOOTSTRAP.md exists (i.e. was created by ensure and
        not yet consumed) OR USER.md has no name set.
        """
        if self.bootstrap_path.exists():
            return True
        try:
            head = "\n".join(self.user_path.read_text(encoding="utf-8").splitlines()[:30])
            # In the frontmatter head: identity.name still null → bootstrap required
            return "name: null" in head
        except FileNotFoundError:
            return True

    def consume_bootstrap(self) -> None:
        """Delete BOOTSTRAP.md after the first-run interview is complete."""
        try:
            self.bootstrap_path.unlink()
        except FileNotFoundError:
            pass
