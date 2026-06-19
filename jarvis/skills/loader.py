"""Skill-Loader: Markdown-File → Skill.

Benötigt optional `python-frontmatter`. Wenn nicht verfügbar, fällt er auf
einen eingebauten YAML-Frontmatter-Splitter zurück (`---\\n...\\n---\\n`).

Kaputte Files werden NICHT geraised — sie landen als `DRAFT` mit gesetztem
`error`-Field im Skill. So kann das Registry eine Diagnose anzeigen ohne
die ganze Pipeline zu töten.
"""
from __future__ import annotations

import hashlib
import logging
from pathlib import Path

try:  # optional dep
    import frontmatter as _frontmatter  # type: ignore
    _HAVE_FRONTMATTER = True
except Exception:  # pragma: no cover
    _frontmatter = None  # type: ignore
    _HAVE_FRONTMATTER = False

try:
    import yaml
    _HAVE_YAML = True
except Exception:  # pragma: no cover
    yaml = None  # type: ignore
    _HAVE_YAML = False

from pydantic import ValidationError

from .schema import RESOURCE_KINDS, Skill, SkillFrontmatter, SkillLifecycleState

log = logging.getLogger(__name__)

SKILL_FILENAME = "SKILL.md"


def _body_hash(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _scan_resources(skill_root: Path) -> dict[str, tuple[str, ...]]:
    """Listet die Bundle-Resource-Sibling-Ordner (references/scripts/assets/agents).

    Gibt pro Kind die Liste der File-Pfade *relativ zum Kind-Ordner* zurueck,
    sortiert. Fehlende Ordner landen als leeres Tuple — Aufrufer muss kein
    defaultdict fuehren. Symlinks werden dereferenziert (``rglob`` folgt ihnen).
    """
    out: dict[str, tuple[str, ...]] = {}
    for kind in RESOURCE_KINDS:
        kind_dir = skill_root / kind
        if not kind_dir.is_dir():
            out[kind] = ()
            continue
        files: list[str] = []
        for p in kind_dir.rglob("*"):
            if p.is_file():
                # Pfad relativ zum Kind-Ordner, forward-slashes fuer UI-Konsistenz
                rel = p.relative_to(kind_dir).as_posix()
                files.append(rel)
        out[kind] = tuple(sorted(files))
    return out


def _split_frontmatter(text: str) -> tuple[dict, str]:
    """Trennt YAML-Frontmatter vom Markdown-Body.

    Bevorzugt python-frontmatter, fällt sonst auf manuellen Split zurück.
    """
    if _HAVE_FRONTMATTER:
        post = _frontmatter.loads(text)  # type: ignore[union-attr]
        return dict(post.metadata), post.content

    if not _HAVE_YAML:
        raise RuntimeError(
            "Weder python-frontmatter noch PyYAML installiert — "
            "Skill-Parsing nicht möglich."
        )

    # Manueller Split: '---\n<yaml>\n---\n<body>'
    if text.startswith("---"):
        parts = text.split("---", 2)
        if len(parts) >= 3:
            fm_str = parts[1]
            body = parts[2].lstrip("\n")
            meta = yaml.safe_load(fm_str) or {}
            if not isinstance(meta, dict):
                raise ValueError("Frontmatter muss ein YAML-Mapping sein")
            return meta, body
    return {}, text


def parse_skill(path: Path) -> Skill:
    """Lädt eine einzelne SKILL.md und gibt einen Skill zurück.

    Niemals raise — Fehler landen im `error`-Feld + DRAFT-State.
    """
    path = Path(path)
    resources = _scan_resources(path.parent)

    try:
        # utf-8-sig transparently strips a leading BOM (ef bb bf) if present
        # and reads plain UTF-8 otherwise. Without this a BOM would shift the
        # leading ``---`` so neither python-frontmatter nor the manual splitter
        # recognise the frontmatter, dropping the skill to DRAFT with a
        # 'name required' error (seen on jarvis-doc-author/SKILL.md).
        raw = path.read_text(encoding="utf-8-sig")
    except OSError as exc:
        return Skill(
            path=path,
            frontmatter=None,
            body="",
            state=SkillLifecycleState.DRAFT,
            body_hash="",
            error=f"read failed: {exc}",
            resources=resources,
        )

    try:
        meta, body = _split_frontmatter(raw)
    except Exception as exc:  # noqa: BLE001
        return Skill(
            path=path,
            frontmatter=None,
            body=raw,
            state=SkillLifecycleState.DRAFT,
            body_hash=_body_hash(raw),
            error=f"frontmatter parse failed: {exc}",
            resources=resources,
        )

    try:
        fm = SkillFrontmatter.model_validate(meta)
    except ValidationError as exc:
        return Skill(
            path=path,
            frontmatter=None,
            body=body,
            state=SkillLifecycleState.DRAFT,
            body_hash=_body_hash(body),
            error=f"frontmatter schema invalid: {exc}",
            resources=resources,
        )

    # Payload-Check pro Trigger (pattern/combo/cron passend zum type)
    trigger_errors: list[str] = []
    for t in fm.triggers:
        trigger_errors.extend(t.validate_payload())
    if trigger_errors:
        return Skill(
            path=path,
            frontmatter=fm,
            body=body,
            state=SkillLifecycleState.DRAFT,
            body_hash=_body_hash(body),
            error="; ".join(trigger_errors),
            resources=resources,
        )

    # Phase 7.5: Wenn das Frontmatter explizit `state: draft` setzt, übernehmen.
    # OpenClaw-authored Skills landen so deterministisch im DRAFT-Pool und
    # werden vom Hot-Reload-Active-Filter ausgeschlossen (Plan-§AD-8).
    final_state = (
        fm.state if fm.state is not None else SkillLifecycleState.VALIDATED
    )

    return Skill(
        path=path,
        frontmatter=fm,
        body=body,
        state=final_state,
        body_hash=_body_hash(body),
        error=None,
        resources=resources,
    )


def discover_skills(root: Path) -> list[Skill]:
    """Walkt `root` rekursiv nach SKILL.md-Dateien."""
    root = Path(root)
    if not root.exists():
        return []
    skills: list[Skill] = []
    for p in root.rglob(SKILL_FILENAME):
        try:
            skills.append(parse_skill(p))
        except Exception as exc:  # noqa: BLE001
            log.warning("parse_skill failed hard for %s: %s", p, exc)
            skills.append(
                Skill(
                    path=p,
                    frontmatter=None,
                    body="",
                    state=SkillLifecycleState.DRAFT,
                    body_hash="",
                    error=f"hard failure: {exc}",
                )
            )
    return skills
