"""Shareable agent templates: one agent's design, safe to hand to a stranger.

A template is what the Share button on an agent exports and what the
marketplace installs as a new teammate (``kind: "agent"`` in the community
registry, docs/marketplace/agent-templates.md). It differs from the
machine-to-machine ecosystem bundle (``jarvis/mcp/agents/portable.py``) in
whom it trusts: a bundle moves YOUR team to YOUR other computer, a template
goes to people you do not know and comes from people you do not know. So
the field list here is stricter, in both directions:

* **Nothing that names the author's machine** — accounts, providers and
  models, workspace folders, connected computers, an imported figure file.
* **Nothing that widens what runs without asking on the installer's
  machine** — the permission ceiling, the approval mode, always-allow rules,
  the spending budget, browser attach mode. ``require_approval`` travels,
  because it can only ADD confirmations.
* **No secrets and no personal details in the text** — the standing
  instructions and the summary are scrubbed: credentials, e-mail addresses,
  phone numbers, home folders, private network addresses and secret URL
  parameters are replaced by a placeholder, and every replacement is listed
  so the person sees what was taken out before anything leaves the machine.

The registry re-checks all of it (``scripts/validate.py``, ``validate_agent``)
— this module is the first line, never the only one. Keep the two field
lists identical.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from .companion import CompanionAppearance
from .roster import AgentRecord, slugify

log = logging.getLogger(__name__)

__all__ = [
    "MAX_INSTRUCTIONS_BYTES",
    "MAX_SUMMARY_CHARS",
    "TEMPLATE_SCHEMA",
    "Finding",
    "ShareDraft",
    "TemplateError",
    "build_draft",
    "create_fields",
    "load_overlay",
    "public_avatar",
    "save_overlay",
    "scrub_text",
    "submission",
    "validate_template",
]

TEMPLATE_SCHEMA: Final[int] = 1
#: Same caps as the registry (rules.json ``limits``).
MAX_INSTRUCTIONS_BYTES: Final[int] = 20_000
MAX_SUMMARY_CHARS: Final[int] = 500
MAX_TITLE_CHARS: Final[int] = 120
MAX_CAPABILITIES: Final[int] = 60
MAX_CATEGORIES: Final[int] = 10

#: The fields a template carries — an allowlist, mirrored by the registry.
TEMPLATE_KEYS: Final[frozenset[str]] = frozenset(
    {
        "schema",
        "name",
        "title",
        "instructions",
        "tier",
        "effort",
        "focus",
        "grant_mode",
        "grants",
        "denies",
        "skills",
        "require_approval",
        "knowledge_scope",
        "avatar",
    }
)

_AGENT_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[^/\\:@#<>\"'`\x00-\x1f]{1,40}$")
_LISTING_NAME_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9](?:[a-z0-9.-]{0,62}[a-z0-9])?$")
_CAPABILITY_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z]+:[A-Za-z0-9_.:-]{1,80}$")
_SKILL_RE: Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_.-]{1,80}$")
_EFFORT_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z]{0,16}$")
_TIERS: Final[tuple[str, ...]] = ("specialist", "orchestrator")
_GRANT_MODES: Final[tuple[str, ...]] = ("all", "allowlist")
_SCOPES: Final[tuple[str, ...]] = ("shared", "own")

# The figure recipe: catalog ids and colours. ``model`` (an imported GLB) is a
# URL served by the author's own app and never travels.
_AVATAR_ID_RE: Final[re.Pattern[str]] = re.compile(r"^[a-z0-9_-]{1,40}$")
_HEX_RE: Final[re.Pattern[str]] = re.compile(r"^#[0-9a-fA-F]{6}$")
_AVATAR_STRINGS: Final[tuple[str, ...]] = (
    "archetype",
    "base",
    "style",
    "hairStyle",
    "outfit",
    "eyewear",
)
_COMPANION_KEYS: Final[frozenset[str]] = frozenset(
    {"shape", "color", "eyes", "enabled", "sizeM", "followDistanceM"}
)


class TemplateError(ValueError):
    """A template cannot be built or installed; the message is user-facing."""


# ---------------------------------------------------------------------------
# Scrubbing
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class Finding:
    """One thing taken out of the text, described without repeating it."""

    kind: str
    field: str
    hint: str

    def to_dict(self) -> dict[str, str]:
        return {"kind": self.kind, "field": self.field, "hint": self.hint}


# Credential shapes: the registry's SECRET_PATTERNS plus the publish
# pre-check's (jarvis/marketplace/publish.py), and a generic "key = value".
_SECRET_RES: Final[tuple[re.Pattern[str], ...]] = (
    re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    re.compile(r"github_pat_[A-Za-z0-9_]{20,}"),
    re.compile(r"sk-[A-Za-z0-9_-]{20,}"),
    re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    re.compile(r"AKIA[0-9A-Z]{16}"),
    re.compile(r"AIza[0-9A-Za-z_-]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?(?:-----END [A-Z ]*PRIVATE KEY-----|$)"),
    re.compile(r"eyJ[A-Za-z0-9_-]{20,}\.eyJ[A-Za-z0-9_-]{20,}(?:\.[A-Za-z0-9_-]+)?"),
    re.compile(
        r"(?i)\b(?:api[_-]?key|secret|password|passwd|token|bearer)\b\s*[:=]\s*[\"']?"
        r"[^\s\"']{8,}"
    ),
)
_URL_SECRET_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)([?&](?:token|key|api_key|apikey|secret|sig|signature|access_token|auth|password)=)"
    r"[^&\s#)]+"
)
_EMAIL_RE: Final[re.Pattern[str]] = re.compile(
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b"
)
# Home folders: they name the machine and, nearly always, the person.
_HOME_RE: Final[re.Pattern[str]] = re.compile(
    r"(?i)(?:\b[A-Za-z]:[\\/]+Users[\\/]+[^\s\"'`)]+|/Users/[^\s\"'`)]+|/home/[^\s\"'`)]+)"
)
# International numbers only (a leading +): bare digit runs are dates, ids and
# version numbers far more often than phone numbers.
_PHONE_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w+])\+\d{1,3}[\s.-]?\(?\d{1,4}\)?(?:[\s.-]?\d{2,4}){2,4}(?!\w)"
)
_PRIVATE_IP_RE: Final[re.Pattern[str]] = re.compile(
    r"\b(?:10\.\d{1,3}|192\.168|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}\b"
)

#: Example addresses a public template may legitimately show.
_EXAMPLE_DOMAINS: Final[tuple[str, ...]] = ("example.com", "example.org", "example.net")


def _mask(text: str) -> str:
    """A hint that identifies the match to its author without repeating it."""
    text = text.strip()
    if len(text) <= 4:
        return "…"
    return f"{text[:2]}…{text[-2:]}"


def scrub_text(text: str, field_name: str) -> tuple[str, list[Finding]]:
    """``text`` with every credential and personal detail replaced, plus the list.

    Order matters: a secret inside a URL or a path is taken out as a secret
    first, so the finding names the worse of the two.
    """
    findings: list[Finding] = []

    def _sub(pattern: re.Pattern[str], kind: str, placeholder: str, value: str) -> str:
        def _replace(match: re.Match[str]) -> str:
            found = match.group(0)
            if kind == "email" and found.lower().split("@", 1)[-1] in _EXAMPLE_DOMAINS:
                return found
            findings.append(Finding(kind=kind, field=field_name, hint=_mask(found)))
            return placeholder

        return pattern.sub(_replace, value)

    out = text
    for pattern in _SECRET_RES:
        out = _sub(pattern, "secret", "[secret removed]", out)

    def _url(match: re.Match[str]) -> str:
        findings.append(Finding(kind="secret", field=field_name, hint=match.group(1)))
        return f"{match.group(1)}[removed]"

    out = _URL_SECRET_RE.sub(_url, out)
    out = _sub(_HOME_RE, "path", "[folder]", out)
    out = _sub(_EMAIL_RE, "email", "[email]", out)
    out = _sub(_PHONE_RE, "phone", "[phone]", out)
    out = _sub(_PRIVATE_IP_RE, "address", "[address]", out)
    return out, findings


# ---------------------------------------------------------------------------
# Building
# ---------------------------------------------------------------------------


def _clean_ids(values: Any, pattern: re.Pattern[str]) -> list[str]:
    if not isinstance(values, list):
        return []
    out: list[str] = []
    for value in values:
        if isinstance(value, str) and pattern.fullmatch(value) and value not in out:
            out.append(value)
        if len(out) >= MAX_CAPABILITIES:
            break
    return out


def public_avatar(avatar: Any) -> dict[str, Any]:
    """The catalog part of a figure recipe; anything else is dropped."""
    if not isinstance(avatar, dict):
        return {}
    out: dict[str, Any] = {}
    if avatar.get("contract") == 1:
        out["contract"] = 1
    for key in _AVATAR_STRINGS:
        value = avatar.get(key)
        if isinstance(value, str) and _AVATAR_ID_RE.fullmatch(value):
            out[key] = value
    parts = avatar.get("parts")
    if isinstance(parts, dict):
        out["parts"] = {
            k: v
            for k, v in list(parts.items())[:32]
            if isinstance(k, str)
            and _AVATAR_ID_RE.fullmatch(k)
            and isinstance(v, str)
            and _AVATAR_ID_RE.fullmatch(v)
        }
    palette = avatar.get("palette")
    if isinstance(palette, dict):
        out["palette"] = {
            k: v
            for k, v in list(palette.items())[:32]
            if isinstance(k, str)
            and _AVATAR_ID_RE.fullmatch(k)
            and isinstance(v, str)
            and _HEX_RE.fullmatch(v)
        }
    inner = avatar.get("inner")
    if isinstance(inner, str) and _HEX_RE.fullmatch(inner):
        out["inner"] = inner
    height = avatar.get("heightM")
    if isinstance(height, (int, float)) and not isinstance(height, bool) and 0.2 <= height <= 4:
        out["heightM"] = float(height)
    companion = avatar.get("companion")
    if isinstance(companion, dict):
        kept = {k: v for k, v in companion.items() if k in _COMPANION_KEYS}
        # The roster validates this namespace strictly; a companion it would
        # refuse is dropped here, so a template never fails to install over
        # the colour of a floating shape.
        try:
            CompanionAppearance.model_validate(kept)
        except ValidationError:
            kept = {}
        if kept:
            out["companion"] = kept
    return out


def _first_paragraph(text: str) -> str:
    for block in re.split(r"\n\s*\n", text or ""):
        line = " ".join(block.replace("#", " ").split())
        if line:
            return line
    return ""


def _clip(text: str, limit: int) -> str:
    text = " ".join(text.split())
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


@dataclass(slots=True)
class ShareDraft:
    """Everything the Share sheet shows: the template, its listing, what was cut."""

    template: dict[str, Any]
    listing: dict[str, Any]
    findings: list[Finding] = field(default_factory=list)
    polished_by: str = ""
    published: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "template": self.template,
            "listing": self.listing,
            "findings": [f.to_dict() for f in self.findings],
            "polished_by": self.polished_by,
            "published": self.published,
            "submission": submission(self.template, self.listing),
        }


def build_draft(agent: AgentRecord, overlay: dict[str, Any] | None = None) -> ShareDraft:
    """The shareable template for ``agent``, with ``overlay`` edits applied.

    ``overlay`` is what the person or the agent itself wrote for the public
    version (``summary``, ``instructions``, ``title``, ``categories``,
    ``listing_name``, ``version``); every text in it is scrubbed exactly like
    the agent's own fields.
    """
    if str(agent.tier) == "lead":
        raise TemplateError("Jarvis is the lead every install already has — share a teammate")
    overlay = overlay or {}
    findings: list[Finding] = []

    raw_instructions = str(overlay.get("instructions") or agent.description or "")
    instructions, found = scrub_text(raw_instructions, "instructions")
    findings += found
    raw_title = str(overlay.get("title") or agent.title or "")
    title, found = scrub_text(raw_title, "title")
    findings += found
    raw_summary = str(
        overlay.get("summary") or agent.title or _first_paragraph(agent.description) or agent.name
    )
    summary, found = scrub_text(raw_summary, "summary")
    findings += found

    effort = str(agent.effort or "").strip().lower()
    template: dict[str, Any] = {
        "schema": TEMPLATE_SCHEMA,
        "name": agent.name,
        "title": _clip(title, MAX_TITLE_CHARS),
        "instructions": _fit_bytes(instructions.strip(), MAX_INSTRUCTIONS_BYTES),
        "tier": str(agent.tier) if str(agent.tier) in _TIERS else "specialist",
        "effort": effort if _EFFORT_RE.fullmatch(effort) else "",
        "focus": _clean_ids(agent.focus, _CAPABILITY_RE),
        "grant_mode": str(agent.grant_mode) if str(agent.grant_mode) in _GRANT_MODES else "all",
        "grants": _clean_ids(agent.grants, _CAPABILITY_RE),
        "denies": _clean_ids(agent.denies, _CAPABILITY_RE),
        "skills": (_clean_ids(agent.skills, _SKILL_RE) if agent.skills is not None else None),
        "require_approval": _clean_ids(
            (agent.approval_rules or {}).get("require_approval", []), _CAPABILITY_RE
        ),
        "knowledge_scope": (
            str(agent.knowledge_scope) if str(agent.knowledge_scope) in _SCOPES else "shared"
        ),
        "avatar": public_avatar(agent.avatar),
    }
    categories = [
        _clip(str(c).lower(), 32)
        for c in (overlay.get("categories") or [])
        if isinstance(c, str) and c.strip()
    ][:MAX_CATEGORIES]
    listing_name = str(overlay.get("listing_name") or slugify(agent.name)).strip().lower()
    listing = {
        "name": listing_name,
        "title": agent.name,
        "description": _clip(summary, MAX_SUMMARY_CHARS),
        "categories": categories,
        "version": str(overlay.get("version") or _next_version(overlay.get("published"))),
    }
    published = overlay.get("published") if isinstance(overlay.get("published"), dict) else None
    # The summary often IS the title, so one address would be listed twice.
    unique: list[Finding] = []
    for finding in findings:
        if all((f.kind, f.hint) != (finding.kind, finding.hint) for f in unique):
            unique.append(finding)
    return ShareDraft(
        template=template,
        listing=listing,
        findings=unique,
        polished_by=str(overlay.get("polished_by") or ""),
        published=published,
    )


def _fit_bytes(text: str, limit: int) -> str:
    data = text.encode("utf-8")
    if len(data) <= limit:
        return text
    return data[: limit - 3].decode("utf-8", errors="ignore").rstrip() + "…"


def _next_version(published: Any) -> str:
    """1.0.0 for a first share; the next patch after the last published one."""
    if isinstance(published, dict):
        version = str(published.get("version") or "")
        parts = version.split(".")
        if len(parts) == 3 and all(p.isdigit() for p in parts):
            return f"{parts[0]}.{parts[1]}.{int(parts[2]) + 1}"
    return "1.0.0"


def submission(template: dict[str, Any], listing: dict[str, Any]) -> dict[str, Any]:
    """The registry submission (``kind: "agent"``) for a template + listing.

    ``publisher`` is left out on purpose: the registry takes it from the
    GitHub account that files the submission, never from the file.
    """
    return {
        "kind": "agent",
        "name": listing.get("name", ""),
        "version": listing.get("version", "1.0.0"),
        "title": listing.get("title", ""),
        "description": listing.get("description", ""),
        "categories": list(listing.get("categories") or []),
        "agent": template,
    }


# ---------------------------------------------------------------------------
# Validation (the registry's rules, mirrored for instant feedback)
# ---------------------------------------------------------------------------


def validate_template(template: Any, listing: dict[str, Any] | None = None) -> list[str]:
    """Every problem with a template (and its listing), as sentences."""
    errors: list[str] = []
    if not isinstance(template, dict):
        return ["a template is a JSON object"]
    for key in template:
        if key not in TEMPLATE_KEYS:
            errors.append(f"{key} is not a template field")
    if template.get("schema") != TEMPLATE_SCHEMA:
        errors.append(f"schema must be {TEMPLATE_SCHEMA} (this Jarvis reads version 1)")
    name = template.get("name")
    if not isinstance(name, str) or not _AGENT_NAME_RE.fullmatch(name.strip() or "/"):
        errors.append("name must be 1-40 characters without / \\ : @ # < > or quotes")
    elif name.strip().lower() == "jarvis":
        errors.append("'Jarvis' is the lead every install already has")
    title = template.get("title", "")
    if not isinstance(title, str) or len(title) > MAX_TITLE_CHARS:
        errors.append(f"title must be at most {MAX_TITLE_CHARS} characters")
    instructions = template.get("instructions")
    if not isinstance(instructions, str) or not instructions.strip():
        errors.append("the instructions are empty — write what this agent does")
    elif len(instructions.encode("utf-8")) > MAX_INSTRUCTIONS_BYTES:
        errors.append(f"the instructions are longer than {MAX_INSTRUCTIONS_BYTES} bytes")
    elif _HOME_RE.search(instructions):
        errors.append("the instructions name a folder on your machine")
    if template.get("tier", "specialist") not in _TIERS:
        errors.append("tier must be specialist or orchestrator")
    if template.get("grant_mode", "all") not in _GRANT_MODES:
        errors.append("grant_mode must be all or allowlist")
    if template.get("knowledge_scope", "shared") not in _SCOPES:
        errors.append("knowledge_scope must be shared or own")
    effort = template.get("effort", "")
    if not isinstance(effort, str) or not _EFFORT_RE.fullmatch(effort):
        errors.append("effort must be a short lowercase word")
    for key in ("focus", "grants", "denies", "require_approval"):
        value = template.get(key, [])
        if not isinstance(value, list) or not all(
            isinstance(v, str) and _CAPABILITY_RE.fullmatch(v) for v in value
        ):
            errors.append(f"{key} must be a list of capability ids")
        elif len(value) > MAX_CAPABILITIES:
            errors.append(f"{key}: at most {MAX_CAPABILITIES} entries")
    skills = template.get("skills")
    if skills is not None and (
        not isinstance(skills, list)
        or not all(isinstance(v, str) and _SKILL_RE.fullmatch(v) for v in skills)
    ):
        errors.append("skills must be a list of skill names or null")
    avatar = template.get("avatar", {})
    # The cleaner drops whatever is not a catalog id or a colour, so a recipe
    # it leaves unchanged is one that carries nothing else.
    if not isinstance(avatar, dict) or public_avatar(avatar) != avatar:
        errors.append("avatar may only carry catalog ids and #rrggbb colours")
    text = json.dumps(template, ensure_ascii=False)
    if any(p.search(text) for p in _SECRET_RES):
        errors.append("the template contains something that looks like a credential")
    if listing is not None:
        listing_name = str(listing.get("name") or "")
        if (
            not _LISTING_NAME_RE.fullmatch(listing_name)
            or "--" in listing_name
            or ".." in listing_name
        ):
            errors.append(
                "the marketplace name must be 1-64 characters of a-z 0-9 - . "
                "(no leading/trailing separators)"
            )
        if not re.fullmatch(r"\d+\.\d+\.\d+", str(listing.get("version") or "")):
            errors.append("the version must look like 1.0.0")
        description = str(listing.get("description") or "")
        if not description.strip():
            errors.append("write a one-line summary for the store card")
        elif len(description) > MAX_SUMMARY_CHARS:
            errors.append(f"the summary is longer than {MAX_SUMMARY_CHARS} characters")
    return errors


# ---------------------------------------------------------------------------
# Installing
# ---------------------------------------------------------------------------


def create_fields(template: dict[str, Any]) -> dict[str, Any]:
    """Roster fields for a new agent from a VALIDATED template.

    Only design fields: the model is the installer's own choice (the create
    route inherits their last chat seat), and permissions start at the app's
    defaults — ``require_approval`` can only add confirmations.
    """
    errors = validate_template(template)
    if errors:
        raise TemplateError(errors[0])
    fields: dict[str, Any] = {
        "name": template["name"].strip(),
        "title": template.get("title", ""),
        "description": template["instructions"],
        "tier": template.get("tier", "specialist"),
        "grant_mode": template.get("grant_mode", "all"),
        "focus": list(template.get("focus", [])),
        "grants": list(template.get("grants", [])),
        "denies": list(template.get("denies", [])),
        "approval_rules": {
            "require_approval": list(template.get("require_approval", [])),
            "always_allow": [],
        },
    }
    if template.get("effort"):
        fields["effort"] = template["effort"]
    if template.get("skills") is not None:
        fields["skills"] = list(template["skills"])
    if template.get("avatar"):
        fields["avatar"] = public_avatar(template["avatar"])
    return fields


# ---------------------------------------------------------------------------
# The share overlay on disk
# ---------------------------------------------------------------------------

_OVERLAY_KEYS: Final[frozenset[str]] = frozenset(
    {
        "summary",
        "instructions",
        "title",
        "categories",
        "listing_name",
        "version",
        "polished_by",
        "published",
    }
)


def _overlay_path(data_dir: Path, agent_id: str) -> Path:
    return Path(data_dir) / "society" / agent_id / "share-template.json"


def load_overlay(data_dir: Path, agent_id: str) -> dict[str, Any]:
    """The saved public-version edits for an agent; ``{}`` when none."""
    path = _overlay_path(data_dir, agent_id)
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return {}
    except (OSError, ValueError):
        log.warning("society: unreadable share draft at %s — starting fresh", path)
        return {}
    return {k: v for k, v in raw.items() if k in _OVERLAY_KEYS} if isinstance(raw, dict) else {}


def save_overlay(data_dir: Path, agent_id: str, changes: dict[str, Any]) -> dict[str, Any]:
    """Merge ``changes`` into the saved overlay (atomic write); returns the result.

    A ``None`` value clears that key, so "back to the agent's own text" is one
    call.
    """
    current = load_overlay(data_dir, agent_id)
    for key, value in changes.items():
        if key not in _OVERLAY_KEYS:
            continue
        if value is None:
            current.pop(key, None)
        else:
            current[key] = value
    path = _overlay_path(data_dir, agent_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(path)
    return current
