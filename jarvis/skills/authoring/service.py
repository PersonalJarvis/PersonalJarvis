"""Deterministic user-skill authoring for ``POST /api/skills``.

This is the "New skill" form path in the desktop app. It takes a structured
request (name, description, body, triggers) and writes a SKILL.md
deterministically — NO brain, no network — so it works on a headless €5 VPS.

It is the sibling of the Jarvis-Agent-author *mission* pipeline
(``SkillAuthoringRunner`` in ``runner.py``): that one spawns a frontier worker to
generate a draft from a voice intent and forces ``state=draft``; this one
persists an explicit, user-reviewed form submission and leaves the skill
immediately usable (no ``state`` field -> the loader treats it as VALIDATED,
i.e. "on"). A user who fills the form and hits Create expects a working skill,
not a draft they then have to flip on. The AI-assisted creator
(``creator_service.py``) writes through ``commit`` with ``state=draft`` instead,
because that content is LLM-generated (AP-15).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jarvis.core.paths import user_skills_dir
from jarvis.skills.builtin import BUILTIN_SKILL_NAMES
from jarvis.skills.loader import parse_skill
from jarvis.skills.schema import Skill, SkillCreated, SkillFrontmatter

_LOG = logging.getLogger(__name__)

_SKILL_FILENAME = "SKILL.md"
# Slug = lowercase kebab-case; everything that is not [a-z0-9] collapses to a
# single hyphen, leading/trailing hyphens stripped. Mirrors the SkillDraft slug
# contract (no path separators, no spaces, no dot-dot).
_SLUG_STRIP_RE = re.compile(r"[^a-z0-9]+")


class SkillAuthoringError(Exception):
    """A create request could not be fulfilled.

    ``status`` is the HTTP status the REST route surfaces: 400 for a malformed
    request (empty/unslugable name, invalid frontmatter), 409 for a name that
    collides with an existing skill or a built-in.
    """

    def __init__(self, message: str, *, status: int = 400) -> None:
        super().__init__(message)
        self.status = status


@dataclass(frozen=True)
class SkillCreateRequest:
    """A structured new-skill request from the desktop form.

    Only ``name`` is required. ``triggers`` is a tuple of plain dicts matching
    the ``SkillTrigger`` shape (``{"type": "voice", "pattern": "..."}``);
    ``risk_policy`` matches ``SkillRiskPolicy``.
    """

    name: str
    description: str = ""
    category: str = "general"
    tags: tuple[str, ...] = ()
    triggers: tuple[dict[str, Any], ...] = ()
    requires_tools: tuple[str, ...] = ()
    risk_policy: dict[str, Any] | None = None
    body: str = ""
    homepage_url: str | None = None
    source_url: str | None = None
    docs_url: str | None = None
    author: str = ""
    # Optional lifecycle-state override (AP-15). ``None`` — the manual "New
    # skill" form's default — omits the frontmatter key entirely, so the
    # loader resolves the skill to VALIDATED ("on") as documented below. The
    # AI creator (``creator_service.py``) passes ``state="draft"`` here so
    # its LLM-generated skills land inactive until a human promotes them.
    state: str | None = None


def slugify(name: str) -> str:
    """Kebab-case slug from a display name. Empty string if nothing slugable."""
    return _SLUG_STRIP_RE.sub("-", name.strip().lower()).strip("-")


def body_has_instructions(body: str) -> bool:
    """True when the body carries real instructions, not just a heading/blank.

    The Hallo-Hallo-Hallo forensic: a skill whose body is only ``## Title``
    (the default the form fills in when the instructions field is left empty)
    is functionless — ``run-skill`` loads an empty body and the brain does
    nothing. A usable skill must have at least one non-heading, non-blank line.
    """
    for line in body.strip().splitlines():
        stripped = line.strip()
        if stripped and not stripped.startswith("#"):
            return True
    return False


def _build_frontmatter(req: SkillCreateRequest) -> dict[str, Any]:
    """Assemble the YAML frontmatter dict, omitting empty optional fields.

    Omits ``state`` when ``req.state`` is ``None`` (the manual form's
    default) so the loader resolves the skill to VALIDATED ("on") — an
    explicit user-authored skill is usable immediately. When ``req.state``
    is set (the AI creator passes ``"draft"``, AP-15) it is written verbatim.
    """
    fm: dict[str, Any] = {
        "schema_version": "1",
        "name": req.name.strip(),
        "version": "0.1.0",
        "description": req.description,
        "category": (req.category or "general").strip() or "general",
    }
    if req.state:
        fm["state"] = req.state
    if req.tags:
        fm["tags"] = list(req.tags)
    if req.author:
        fm["author"] = req.author
    if req.homepage_url:
        fm["homepage_url"] = req.homepage_url
    if req.source_url:
        fm["source_url"] = req.source_url
    if req.docs_url:
        fm["docs_url"] = req.docs_url
    if req.triggers:
        fm["triggers"] = [dict(t) for t in req.triggers]
    if req.requires_tools:
        fm["requires_tools"] = list(req.requires_tools)
    if req.risk_policy:
        fm["risk_policy"] = dict(req.risk_policy)
    return fm


def render_skill_md(req: SkillCreateRequest) -> str:
    """Render a complete SKILL.md (validated frontmatter + body).

    Raises ``SkillAuthoringError(400)`` when the assembled frontmatter does not
    pass ``SkillFrontmatter`` validation (e.g. a trigger missing its payload).
    """
    fm = _build_frontmatter(req)
    try:
        SkillFrontmatter.model_validate(fm)
    except ValidationError as exc:
        raise SkillAuthoringError(
            f"Invalid skill definition: {exc.errors()[0].get('msg', exc)}",
            status=400,
        ) from exc
    yaml_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    body = req.body if req.body.strip() else f"## {req.name.strip()}\n"
    return f"---\n{yaml_text}---\n\n{body.rstrip()}\n"


class SkillAuthoringService:
    """Creates a user skill on disk and refreshes the registry."""

    def __init__(
        self,
        *,
        registry: Any,
        bus: Any | None = None,
        user_skills_root: Path | None = None,
    ) -> None:
        self._registry = registry
        self._bus = bus
        # Default to the registry's own root so the written skill always lands
        # where the registry looks; fall back to the canonical user dir.
        if user_skills_root is not None:
            self._root = Path(user_skills_root)
        else:
            self._root = Path(getattr(registry, "root", user_skills_dir()))

    async def create(self, req: SkillCreateRequest) -> Skill:
        name = req.name.strip()
        if not name:
            raise SkillAuthoringError("Name must not be empty.", status=400)

        slug = slugify(name)
        if not slug:
            raise SkillAuthoringError(
                f"Name '{req.name}' has no usable characters for a skill id.",
                status=400,
            )

        # Collision: explicit built-in name, an existing skill, or a slug
        # directory already on disk.
        if name in BUILTIN_SKILL_NAMES:
            raise SkillAuthoringError(
                f"'{name}' is a built-in skill name.", status=409
            )
        existing = {s.name for s in self._registry.list()}
        if name in existing:
            raise SkillAuthoringError(
                f"A skill named '{name}' already exists.", status=409
            )
        target_dir = self._root / slug
        if target_dir.exists():
            raise SkillAuthoringError(
                f"A skill folder '{slug}' already exists.", status=409
            )

        # A skill without instructions in its body is functionless (the
        # Hallo-Hallo-Hallo forensic). Refuse it here so no path — UI form,
        # REST, or AI-commit — can persist a dead skill.
        if not body_has_instructions(req.body):
            raise SkillAuthoringError(
                "A skill needs instructions in its body — describe what it "
                "should do when it runs.",
                status=400,
            )

        rendered = render_skill_md(req)

        # Atomic write: temp file in the (newly created) slug dir, then replace.
        target_dir.mkdir(parents=True, exist_ok=True)
        skill_path = target_dir / _SKILL_FILENAME
        tmp = skill_path.with_suffix(skill_path.suffix + ".tmp")
        try:
            tmp.write_text(rendered, encoding="utf-8")
            tmp.replace(skill_path)
        except OSError as exc:
            raise SkillAuthoringError(
                f"Could not write skill: {exc}", status=500
            ) from exc

        # Refresh so the new skill is immediately listable + the response shows
        # the parsed state. reload_sync is the synchronous path (the watcher
        # hot-reload would also pick it up, but async).
        self._registry.reload_sync()
        try:
            created = self._registry.get(name)
        except KeyError:
            # The file parsed to a different name than requested (shouldn't
            # happen — frontmatter name == req.name) or failed to load.
            created = parse_skill(skill_path)

        await self._emit_created(created, req.author)
        return created

    async def _emit_created(self, skill: Skill, author: str) -> None:
        bus = self._bus
        if bus is None or not hasattr(bus, "publish"):
            return
        try:
            await bus.publish(SkillCreated(skill_name=skill.name, author=author))
        except Exception as exc:  # noqa: BLE001 — telemetry must never block create
            _LOG.warning("SkillCreated publish failed: %s", exc)


__all__ = [
    "SkillAuthoringError",
    "SkillAuthoringService",
    "SkillCreateRequest",
    "body_has_instructions",
    "render_skill_md",
    "slugify",
]
