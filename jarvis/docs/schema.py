"""Pydantic schemas and dataclasses for docs.

The frontmatter schema follows ``jarvis/skills/builtin/jarvis-doc-author/
references/frontmatter-schema.md`` — intentionally tolerant (``extra="ignore"``),
so existing Markdown files without frontmatter or with extra fields do not crash.
Missing required fields are synthesised by the loader (``title`` from the
filename, ``slug`` from the normalised path).

``Doc`` is a frozen dataclass — immutability is consistent with the ``Skill``
architecture (flight-recorder replay capable).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
from enum import Enum
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from jarvis.core.events import Event

# ----------------------------------------------------------------------
# Enums
# ----------------------------------------------------------------------

class DocDiataxis(str, Enum):
    """Diataxis quadrant + ADR + Troubleshooting + special case Unclassified.

    ``unclassified`` is the default for legacy files that lack frontmatter.
    The UI groups them in a separate section and marks them as migration
    candidates.
    """
    TUTORIAL = "tutorial"
    HOWTO = "howto"
    REFERENCE = "reference"
    EXPLANATION = "explanation"
    TROUBLESHOOTING = "troubleshooting"
    ADR = "adr"
    UNCLASSIFIED = "unclassified"


class DocStatus(str, Enum):
    """Lifecycle state of a doc."""
    DRAFT = "draft"
    ACTIVE = "active"
    DEPRECATED = "deprecated"


DocAudience = Literal["developer", "operator", "end-user"]


# ----------------------------------------------------------------------
# Frontmatter-Model
# ----------------------------------------------------------------------

class DocFrontmatter(BaseModel):
    """YAML frontmatter of a Jarvis doc.

    Schema source: ``jarvis/skills/builtin/jarvis-doc-author/references/
    frontmatter-schema.md``. Intentionally tolerant — ``extra="ignore"``
    absorbs fields not modelled here (e.g. ADR-specific ``adr_status``).
    """
    model_config = ConfigDict(frozen=True, extra="ignore")

    title: str
    slug: str
    diataxis: DocDiataxis = DocDiataxis.UNCLASSIFIED
    status: DocStatus = DocStatus.DRAFT
    owner: str = "harald"
    last_reviewed: date | None = None
    phase: str = "-"
    audience: DocAudience = "developer"
    tags: list[str] = Field(default_factory=list)
    related: list[str] = Field(default_factory=list)
    deprecates: str | None = None
    deprecated_by: str | None = None
    next_review_due: date | None = None
    version_min: str | None = None

    @field_validator("title", "slug")
    @classmethod
    def _strip_nonempty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("darf nicht leer sein")
        return v

    @field_validator("phase", mode="before")
    @classmethod
    def _phase_to_str(cls, v: object) -> str:
        """Handles ``phase: 5`` (int), ``phase: "5"`` (str), and ``phase: 1a`` as str."""
        if v is None:
            return "-"
        return str(v).strip() or "-"

    @field_validator("tags", "related", mode="before")
    @classmethod
    def _coerce_list(cls, v: object) -> list[str]:
        """Tolerates comma-separated strings and None values from sloppy YAML."""
        if v is None:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        if isinstance(v, list):
            return [str(s).strip() for s in v if str(s).strip()]
        raise ValueError("muss Liste oder kommaseparierter String sein")


# ----------------------------------------------------------------------
# Doc-Container
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class Doc:
    """A loaded Markdown file.

    ``frontmatter`` is always set (even for legacy files: synthesised defaults
    from the filename, ``diataxis=UNCLASSIFIED``).
    ``error`` marks parse problems — the file is still shown in the UI, with
    a diagnostic banner.
    """
    path: Path
    frontmatter: DocFrontmatter
    body: str
    headings: tuple[tuple[int, str, str], ...] = field(default_factory=tuple)
    """List of ``(level, text, slug)`` tuples for TOC and FTS5 index."""
    body_hash: str = ""
    error: str | None = None

    @property
    def slug(self) -> str:
        return self.frontmatter.slug

    @property
    def title(self) -> str:
        return self.frontmatter.title

    @property
    def diataxis(self) -> DocDiataxis:
        return self.frontmatter.diataxis


# ----------------------------------------------------------------------
# Bus-Events
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class DocIndexReloaded(Event):
    """Emitted when the ``DocRegistry`` has finished re-indexing.

    Frontend subscribers invalidate their react-query cache. Follows the
    same pattern as ``SkillRegistryReloaded`` in ``jarvis/skills/schema.py``.
    """
    total: int = 0
    by_diataxis: dict[str, int] = field(default_factory=dict)
    errors: int = 0
