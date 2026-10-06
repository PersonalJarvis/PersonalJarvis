"""The Agentic IDE's skill library — saved Markdown prompts for terminal panes.

A skill here is a titled piece of Markdown the user keeps at hand: a review
checklist, a house style, a SKILL.md they collected somewhere. The IDE's
Skills tab lists them, and dragging one onto a terminal pane pastes its text
into that pane's prompt (never submitted — the user still says what to do).

This is deliberately NOT the assistant's skill system (``jarvis.skills``):
those are capabilities Jarvis itself runs, with lifecycle states and a
registry. These are inert text the user hands to whichever coding agent they
choose, so they live in their own small store and never enter a tool set.

Storage is one JSON sidecar under ``user_data_dir()/data/`` written atomically
(tempfile + ``os.replace``), the same discipline as the STT dictionary. The
order of the list is the user's order; new skills join at the top.
"""
from __future__ import annotations

import dataclasses
import json
import logging
import os
import re
import tempfile
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from jarvis.core.paths import user_data_dir

log = logging.getLogger(__name__)

# Abuse guards, not product limits — generous for any real collection.
MAX_SKILLS = 500
MAX_TITLE_LEN = 120
MAX_DESCRIPTION_LEN = 280
MAX_CONTENT_CHARS = 200_000

#: The palette slots the UI paints a skill with. Names, not colours: the
#: frontend maps each onto its own theme tokens so both modes stay legible.
HUES: tuple[str, ...] = (
    "blue",
    "violet",
    "teal",
    "amber",
    "rose",
    "green",
    "magenta",
    "slate",
)

#: Glyph names the UI knows how to draw. Anything else falls back to "auto".
ICONS: tuple[str, ...] = (
    "auto",
    "doc",
    "plan",
    "code",
    "bug",
    "flask",
    "review",
    "shield",
    "refactor",
    "book",
    "list",
    "git",
    "terminal",
    "palette",
    "data",
    "perf",
    "rocket",
)

#: Glyphs an older library stored, mapped onto today's set when it is read.
_LEGACY_ICONS: dict[str, str] = {"sparkles": "auto", "wand": "refactor", "brain": "plan"}

_FRONTMATTER_RE = re.compile(r"\A\ufeff?---[ \t]*\r?\n(.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
def _heading_text(line: str) -> str | None:
    """The text of a Markdown ATX heading line (``## Title ##``), else ``None``.

    Parsed by hand rather than with a regex: a lazy group between optional
    whitespace runs backtracks quadratically on a long line of spaces.
    """
    rest = line.lstrip(" 	")
    if len(line) - len(rest) > 3:
        return None
    marks = len(rest) - len(rest.lstrip("#"))
    if not 1 <= marks <= 6 or len(rest) == marks or not rest[marks].isspace():
        return None
    text = rest[marks:].strip().rstrip("#").rstrip()
    return text or None


def skill_library_path() -> Path:
    """JSON sidecar holding the IDE's skill library."""
    return user_data_dir() / "data" / "ide_skills.json"


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _clean_line(value: str, limit: int) -> str:
    return " ".join(value.split())[:limit].strip()


def _frontmatter_field(frontmatter: str, key: str) -> str:
    """One scalar field of a YAML frontmatter block, read without a YAML parser.

    Only ``key: value`` on one line (quotes stripped) is understood — enough for
    the ``name``/``description`` every SKILL.md carries, and nothing here ever
    executes or trusts what it reads.
    """
    match = re.search(rf"^{re.escape(key)}\s*:\s*(.+?)\s*$", frontmatter, re.MULTILINE)
    if not match:
        return ""
    value = match.group(1).strip()
    if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
        value = value[1:-1]
    # A folded block scalar (`description: >-`) carries its text on the next lines.
    if value in {">", ">-", "|", "|-"}:
        rest = frontmatter[match.end():].splitlines()
        folded: list[str] = []
        for line in rest:
            if line.strip() and not line.startswith((" ", "\t")):
                break
            folded.append(line.strip())
        value = " ".join(part for part in folded if part)
    return value


def derive_title(content: str, fallback: str = "") -> str:
    """The title a pasted or imported Markdown text suggests for itself.

    Frontmatter ``name`` first (a SKILL.md), then the first heading, then the
    caller's fallback (an imported file's name), then the first line.
    """
    match = _FRONTMATTER_RE.match(content)
    if match:
        name = _frontmatter_field(match.group(1), "name")
        if name:
            return _clean_line(name, MAX_TITLE_LEN)
        body = content[match.end():]
    else:
        body = content
    for line in body.splitlines():
        heading = _heading_text(line)
        if heading:
            return _clean_line(heading, MAX_TITLE_LEN)
    if fallback.strip():
        return _clean_line(fallback, MAX_TITLE_LEN)
    for line in body.splitlines():
        if line.strip():
            return _clean_line(line.lstrip("#>-*` "), MAX_TITLE_LEN)
    return ""


def derive_description(content: str) -> str:
    """A one-line summary: frontmatter ``description``, else the first prose line."""
    match = _FRONTMATTER_RE.match(content)
    if match:
        described = _frontmatter_field(match.group(1), "description")
        if described:
            return _clean_line(described, MAX_DESCRIPTION_LEN)
        body = content[match.end():]
    else:
        body = content
    in_fence = False
    for line in body.splitlines():
        stripped = line.strip()
        if stripped.startswith(("```", "~~~")):
            in_fence = not in_fence
            continue
        if in_fence or not stripped or _heading_text(line) is not None:
            continue
        if stripped.startswith(("|", "---", "<!--")):
            continue
        plain = stripped.lstrip(">-*+ ").replace("**", "").replace("`", "")
        return _clean_line(plain, MAX_DESCRIPTION_LEN)
    return ""


@dataclass(frozen=True)
class LibrarySkill:
    """One saved prompt. ``description`` is derived unless the user set one."""

    id: str
    title: str
    content: str
    description: str = ""
    hue: str = ""
    icon: str = "auto"
    created_at: str = ""
    updated_at: str = ""
    use_count: int = 0
    last_used_at: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> LibrarySkill | None:
        try:
            skill_id = str(raw["id"]).strip()
            content = str(raw.get("content", ""))
            title = _clean_line(str(raw.get("title", "")), MAX_TITLE_LEN)
        except (KeyError, TypeError) as exc:
            # A hand-edited or half-migrated entry: skip it rather than lose the list.
            log.warning("IDE skill library: dropping unreadable entry (%s).", exc)
            return None
        if not skill_id or not title:
            return None
        hue = str(raw.get("hue") or "")
        icon = str(raw.get("icon") or "auto")
        icon = _LEGACY_ICONS.get(icon, icon)
        try:
            uses = max(0, int(raw.get("use_count") or 0))
        except (TypeError, ValueError):  # a hand-edited non-number counts as never used
            uses = 0
        last_used = raw.get("last_used_at")
        return cls(
            id=skill_id,
            title=title,
            content=content[:MAX_CONTENT_CHARS],
            description=_clean_line(str(raw.get("description") or ""), MAX_DESCRIPTION_LEN),
            hue=hue if hue in HUES else _hue_for(skill_id),
            icon=icon if icon in ICONS else "auto",
            created_at=str(raw.get("created_at") or ""),
            updated_at=str(raw.get("updated_at") or ""),
            use_count=uses,
            last_used_at=str(last_used) if last_used else None,
        )


def _hue_for(seed: str) -> str:
    """A stable palette slot for a skill that never picked one."""
    total = sum(ord(ch) for ch in seed)
    return HUES[total % len(HUES)]


def _validated_content(content: str) -> str:
    if not content.strip():
        raise ValueError("A skill needs some Markdown text.")
    if len(content) > MAX_CONTENT_CHARS:
        raise ValueError(f"A skill can hold at most {MAX_CONTENT_CHARS:,} characters.")
    # Windows line endings would paste as doubled newlines into some CLIs.
    return content.replace("\r\n", "\n").replace("\r", "\n")


def _validated_title(title: str) -> str:
    cleaned = _clean_line(title, MAX_TITLE_LEN)
    if not cleaned:
        raise ValueError("A skill needs a title.")
    return cleaned


def _validated_choice(value: str | None, allowed: tuple[str, ...], field: str) -> str | None:
    if value is None:
        return None
    if value not in allowed:
        raise ValueError(f"Unknown {field} {value!r}.")
    return value


class SkillLibrary:
    """Read-modify-write over the sidecar. Callers serialise with their own lock."""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or skill_library_path()

    def list_all(self) -> list[LibrarySkill]:
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except FileNotFoundError:  # no library file yet means an empty library
            return []
        except (OSError, ValueError) as exc:
            log.warning(
                "IDE skill library at %s is unreadable (%s); starting empty.", self._path, exc
            )
            return []
        entries = raw.get("skills", []) if isinstance(raw, dict) else []
        out: list[LibrarySkill] = []
        seen: set[str] = set()
        for entry in entries if isinstance(entries, list) else []:
            if not isinstance(entry, dict):
                continue
            skill = LibrarySkill.from_dict(entry)
            if skill is None or skill.id in seen:
                continue
            seen.add(skill.id)
            out.append(skill)
        return out

    def get(self, skill_id: str) -> LibrarySkill | None:
        return next((skill for skill in self.list_all() if skill.id == skill_id), None)

    def create(
        self,
        *,
        title: str,
        content: str,
        description: str | None = None,
        hue: str | None = None,
        icon: str | None = None,
    ) -> LibrarySkill:
        skills = self.list_all()
        if len(skills) >= MAX_SKILLS:
            raise ValueError(f"The library is full ({MAX_SKILLS} skills).")
        body = _validated_content(content)
        skill_id = uuid.uuid4().hex[:12]
        now = _now_iso()
        skill = LibrarySkill(
            id=skill_id,
            title=_validated_title(title),
            content=body,
            description=(
                _clean_line(description, MAX_DESCRIPTION_LEN)
                if description
                else derive_description(body)
            ),
            hue=_validated_choice(hue, HUES, "colour") or _hue_for(skill_id),
            icon=_validated_choice(icon, ICONS, "icon") or "auto",
            created_at=now,
            updated_at=now,
        )
        self._write([skill, *skills])
        return skill

    def update(
        self,
        skill_id: str,
        *,
        title: str | None = None,
        content: str | None = None,
        description: str | None = None,
        hue: str | None = None,
        icon: str | None = None,
    ) -> LibrarySkill | None:
        skills = self.list_all()
        for index, skill in enumerate(skills):
            if skill.id != skill_id:
                continue
            body = _validated_content(content) if content is not None else skill.content
            if description is not None:
                summary = _clean_line(description, MAX_DESCRIPTION_LEN)
                summary = summary or derive_description(body)
            elif content is not None:
                summary = derive_description(body)
            else:
                summary = skill.description
            updated = dataclasses.replace(
                skill,
                title=_validated_title(title) if title is not None else skill.title,
                content=body,
                description=summary,
                hue=_validated_choice(hue, HUES, "colour") or skill.hue,
                icon=_validated_choice(icon, ICONS, "icon") or skill.icon,
                updated_at=_now_iso(),
            )
            skills[index] = updated
            self._write(skills)
            return updated
        return None

    def mark_used(self, skill_id: str) -> LibrarySkill | None:
        """Count one paste into a terminal. Does not touch ``updated_at``."""
        skills = self.list_all()
        for index, skill in enumerate(skills):
            if skill.id != skill_id:
                continue
            used = dataclasses.replace(
                skill, use_count=skill.use_count + 1, last_used_at=_now_iso()
            )
            skills[index] = used
            self._write(skills)
            return used
        return None

    def reorder(self, ids: list[str]) -> list[LibrarySkill]:
        """Put the named skills first in the given order; the rest keep theirs."""
        skills = self.list_all()
        by_id = {skill.id: skill for skill in skills}
        ordered: list[LibrarySkill] = []
        for skill_id in ids:
            skill = by_id.pop(skill_id, None)
            if skill is not None:
                ordered.append(skill)
        ordered.extend(skill for skill in skills if skill.id in by_id)
        self._write(ordered)
        return ordered

    def delete(self, skill_id: str) -> bool:
        skills = self.list_all()
        kept = [skill for skill in skills if skill.id != skill_id]
        if len(kept) == len(skills):
            return False
        self._write(kept)
        return True

    def _write(self, skills: list[LibrarySkill]) -> None:
        payload = {"version": 1, "skills": [skill.to_dict() for skill in skills]}
        self._path.parent.mkdir(parents=True, exist_ok=True)
        # Atomic tempfile + os.replace: a crash mid-write never tears the list.
        fd, tmp_name = tempfile.mkstemp(
            dir=str(self._path.parent), prefix=".ide_skills_", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, ensure_ascii=False, indent=2)
            os.replace(tmp_name, self._path)
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                log.debug("IDE skill library: temp file %s already gone.", tmp_name)
            raise
