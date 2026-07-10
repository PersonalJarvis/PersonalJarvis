"""AI Skill Creator — backs ``/api/skills/creator/{draft,refine,validate,commit}``.

Turns a free-text intent into a structured SKILL.md draft, optionally with brain
assistance, and commits the user-reviewed draft as a real skill.

Cloud-first contract: the creator is brain-*assisted*, never brain-*dependent*.
``draft`` always returns a valid deterministic skeleton; if a brain is reachable
and returns parseable JSON, the skeleton is replaced by the brain's richer draft
(``brain_used=True``). A missing brain, a timeout, a refusal, or malformed JSON
all degrade silently to the skeleton — so the feature works on a headless €5 VPS
with no provider configured.

``commit`` persists the reviewed draft through the same deterministic writer as
the manual "New skill" form (``SkillAuthoringService``), so there is one
code path that touches the registry.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

from jarvis.skills.authoring.service import (
    SkillAuthoringError,
    SkillAuthoringService,
    SkillCreateRequest,
)
from jarvis.skills.schema import Skill, SkillFrontmatter

_LOG = logging.getLogger(__name__)

# Bounded brain call — the creator runs from a UI click, not the voice hot path,
# but we still cap it so a hung provider cannot wedge the request.
_BRAIN_TIMEOUT_S = 45.0
_BRAIN_MAX_TOKENS = 1500

_WORD_RE = re.compile(r"[A-Za-z0-9]+")


# ----------------------------------------------------------------------
# Inputs / outputs
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SkillCreatorInput:
    """A creator request. ``intent`` is the user's free-text description; the
    rest are optional UI hints. ``existing_draft`` + ``feedback`` drive
    ``refine`` (a revision of a prior draft)."""

    intent: str
    name_hint: str = ""
    category: str = "general"
    trigger_hint: str = ""
    extra_context: str = ""
    existing_draft: dict[str, Any] | None = None
    feedback: str = ""


@dataclass(frozen=True)
class SkillCreatorResult:
    """What ``draft``/``refine`` return — mirrors the frontend response type."""

    draft: dict[str, Any]
    skill_md: str
    validation: dict[str, Any]
    brain_used: bool


# ----------------------------------------------------------------------
# Draft shape helpers
# ----------------------------------------------------------------------


def _empty_draft() -> dict[str, Any]:
    return {
        "name": "",
        "description": "",
        "category": "general",
        "tags": [],
        "triggers": [],
        "requires_tools": [],
        "risk_policy": {"default_tier": "ask"},
        "body": "",
        "questions": [],
        "assumptions": [],
        "test_prompts": [],
    }


def _title_from_intent(intent: str) -> str:
    """A short Title-Case name from the first few words of the intent."""
    words = _WORD_RE.findall(intent)[:5]
    if not words:
        return "New Skill"
    return " ".join(w.capitalize() for w in words)


def _skeleton_draft(inp: SkillCreatorInput) -> dict[str, Any]:
    """A valid deterministic draft from the intent alone — no brain.

    This is the always-available fallback. It is intentionally minimal but
    complete enough to validate and commit unchanged.
    """
    draft = _empty_draft()
    name = inp.name_hint.strip() or _title_from_intent(inp.intent)
    draft["name"] = name
    draft["description"] = inp.intent.strip()[:400] or name
    draft["category"] = (inp.category or "general").strip() or "general"
    if inp.trigger_hint.strip():
        draft["triggers"] = [
            {"type": "voice", "pattern": inp.trigger_hint.strip()}
        ]
    body_lines = [
        f"## {name}",
        "",
        inp.intent.strip() or "Describe what this skill does.",
        "",
        "## Steps",
        "",
        "1. ...",
    ]
    if inp.extra_context.strip():
        body_lines += ["", "## Notes", "", inp.extra_context.strip()]
    draft["body"] = "\n".join(body_lines) + "\n"
    draft["assumptions"] = [
        "Generated deterministically without a brain — edit before committing."
    ]
    return draft


def _coerce_brain_draft(
    data: dict[str, Any], inp: SkillCreatorInput
) -> dict[str, Any]:
    """Merge a brain-produced dict onto the empty-draft shape, dropping unknown
    keys and backfilling required ones from the skeleton."""
    skeleton = _skeleton_draft(inp)
    draft = _empty_draft()
    for key in draft:
        if key in data and data[key] not in (None, ""):
            draft[key] = data[key]
        else:
            draft[key] = skeleton[key]
    # Normalise list/dict typed fields defensively.
    for list_key in (
        "tags",
        "triggers",
        "requires_tools",
        "questions",
        "assumptions",
        "test_prompts",
    ):
        if not isinstance(draft[list_key], list):
            draft[list_key] = []
    if not isinstance(draft["risk_policy"], dict):
        draft["risk_policy"] = {"default_tier": "ask"}
    draft["name"] = str(draft["name"]).strip() or skeleton["name"]
    draft["body"] = str(draft["body"]).strip() and draft["body"] or skeleton["body"]
    return draft


def _extract_json(text: str) -> dict[str, Any] | None:
    """Pull the first top-level JSON object out of a brain response."""
    cleaned = text.strip()
    fence = re.match(r"```(?:json)?\s*\n?(.*?)```", cleaned, re.DOTALL | re.IGNORECASE)
    if fence:
        cleaned = fence.group(1).strip()
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        data = json.loads(cleaned[start : end + 1])
    except (ValueError, TypeError):
        return None
    return data if isinstance(data, dict) else None


# ----------------------------------------------------------------------
# Render + validate
# ----------------------------------------------------------------------


def _extract_frontmatter(content: str) -> dict[str, Any] | None:
    """Parse the YAML frontmatter block out of a SKILL.md string.

    Returns ``None`` when there is no ``---``-delimited frontmatter at all.
    Raises ``ValueError`` when the YAML is malformed.
    """
    stripped = content.lstrip()
    if not stripped.startswith("---"):
        return None
    parts = stripped.split("---", 2)
    if len(parts) < 3:
        return None
    try:
        data = yaml.safe_load(parts[1])
    except yaml.YAMLError as exc:
        raise ValueError(f"Malformed YAML frontmatter: {exc}") from exc
    return data if isinstance(data, dict) else None


def render_skill_md(draft: dict[str, Any]) -> str:
    """Render a SKILL.md string from a creator draft dict (state forced draft).

    Unlike the manual form (which writes a usable VALIDATED skill), the AI
    creator stamps ``state: draft`` because the content is LLM-generated
    (AP-15) — a human must explicitly promote it before it goes live. This
    render is what the user previews AND what ``commit`` persists (via
    ``SkillCreateRequest(state="draft")``), so the two never disagree.
    """
    fm: dict[str, Any] = {
        "schema_version": "1",
        "name": str(draft.get("name", "")).strip(),
        "version": "0.1.0",
        "description": str(draft.get("description", "")),
        "category": str(draft.get("category", "general")) or "general",
        "state": "draft",
    }
    if draft.get("tags"):
        fm["tags"] = list(draft["tags"])
    if draft.get("triggers"):
        fm["triggers"] = [dict(t) for t in draft["triggers"]]
    if draft.get("requires_tools"):
        fm["requires_tools"] = list(draft["requires_tools"])
    if draft.get("risk_policy"):
        fm["risk_policy"] = dict(draft["risk_policy"])
    yaml_text = yaml.safe_dump(fm, sort_keys=False, allow_unicode=True)
    body = str(draft.get("body", "")).strip() or f"## {fm['name']}\n"
    return f"---\n{yaml_text}---\n\n{body.rstrip()}\n"


def validate_skill_md(content: str) -> tuple[dict[str, Any], dict[str, Any] | None]:
    """Validate a SKILL.md string. Returns ``(validation, frontmatter)``.

    ``validation`` is ``{ok, state, errors, warnings, parse_error}``. ``ok`` is
    True only when the frontmatter both parses and passes ``SkillFrontmatter``.
    """
    errors: list[str] = []
    warnings: list[str] = []
    parse_error: str | None = None

    try:
        fm_dict = _extract_frontmatter(content)
    except ValueError as exc:
        return (
            {
                "ok": False,
                "state": "draft",
                "errors": [str(exc)],
                "warnings": [],
                "parse_error": str(exc),
            },
            None,
        )

    if not fm_dict:
        parse_error = "No YAML frontmatter found."
        return (
            {
                "ok": False,
                "state": "draft",
                "errors": [parse_error],
                "warnings": [],
                "parse_error": parse_error,
            },
            None,
        )

    try:
        model = SkillFrontmatter.model_validate(fm_dict)
    except ValidationError as exc:
        for err in exc.errors():
            loc = ".".join(str(p) for p in err.get("loc", ()))
            errors.append(f"{loc}: {err.get('msg', 'invalid')}".lstrip(": "))
        return (
            {
                "ok": False,
                "state": "draft",
                "errors": errors,
                "warnings": warnings,
                "parse_error": None,
            },
            None,
        )

    # Semantic trigger checks (voice needs a pattern, etc.) → warnings, not hard
    # errors, so the user can still commit and refine.
    for trig in model.triggers:
        warnings.extend(trig.validate_payload())

    return (
        {
            "ok": True,
            "state": "validated",
            "errors": [],
            "warnings": warnings,
            "parse_error": None,
        },
        model.model_dump(),
    )


# ----------------------------------------------------------------------
# Service
# ----------------------------------------------------------------------


class SkillCreatorService:
    """Brain-assisted skill drafting + deterministic commit."""

    def __init__(
        self,
        *,
        brain: Any | None = None,
        registry: Any,
        bus: Any | None = None,
        config: Any | None = None,
        user_skills_root: Path | None = None,
    ) -> None:
        self._brain = brain
        self._registry = registry
        self._bus = bus
        self._config = config
        self._user_skills_root = user_skills_root

    async def draft(self, inp: SkillCreatorInput) -> SkillCreatorResult:
        return await self._draft_or_refine(inp)

    async def refine(self, inp: SkillCreatorInput) -> SkillCreatorResult:
        return await self._draft_or_refine(inp, refine=True)

    async def commit(self, draft: dict[str, Any]) -> Skill:
        """Persist a reviewed draft as a real skill via the shared writer.

        Always writes ``state="draft"`` frontmatter (AP-15): the content is
        LLM-generated (or an unreviewed deterministic skeleton), so it must
        land inactive until a human explicitly promotes it — matching what
        ``render_skill_md`` already previews to the user.
        """
        if not isinstance(draft, dict):
            raise ValueError("draft must be an object")
        req = SkillCreateRequest(
            name=str(draft.get("name", "")).strip(),
            description=str(draft.get("description", "")),
            category=str(draft.get("category", "general")) or "general",
            tags=tuple(draft.get("tags", []) or ()),
            triggers=tuple(draft.get("triggers", []) or ()),
            requires_tools=tuple(draft.get("requires_tools", []) or ()),
            risk_policy=draft.get("risk_policy") or None,
            body=str(draft.get("body", "")),
            state="draft",
        )
        service = SkillAuthoringService(
            registry=self._registry,
            bus=self._bus,
            user_skills_root=self._user_skills_root,
        )
        return await service.create(req)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------

    async def _draft_or_refine(
        self, inp: SkillCreatorInput, *, refine: bool = False
    ) -> SkillCreatorResult:
        skeleton = _skeleton_draft(inp)
        draft = skeleton
        brain_used = False

        brain_draft = await self._try_brain(inp, refine=refine)
        if brain_draft is not None:
            draft = _coerce_brain_draft(brain_draft, inp)
            brain_used = True

        skill_md = render_skill_md(draft)
        validation, _ = validate_skill_md(skill_md)
        # If a brain draft somehow failed validation, fall back to the skeleton
        # (which is constructed to always validate).
        if not validation["ok"] and brain_used:
            draft = skeleton
            brain_used = False
            skill_md = render_skill_md(draft)
            validation, _ = validate_skill_md(skill_md)

        return SkillCreatorResult(
            draft=draft,
            skill_md=skill_md,
            validation=validation,
            brain_used=brain_used,
        )

    def _resolve_brain(self) -> Any | None:
        """A brain with a ``.complete()`` method, or None.

        Multi-provider contract (AP-21): follow the user's ACTIVE selection,
        never a hardcoded frontier favourite. The injected ``brain`` is the live
        ``BrainManager``; ask it for the provider the user currently has active
        (e.g. Gemini) rather than letting the frontier resolver pick an unkeyed
        Claude-API and 401. Order:
          1. the BrainManager's active provider (``active_provider`` +
             ``_get_or_create``),
          2. an injected raw provider that already exposes ``.complete``,
          3. the frontier resolver from config (only when no usable brain was
             injected — e.g. the brain hasn't finished building yet).
        Any failure degrades to ``None`` → deterministic skeleton.
        """
        bm = self._brain
        if bm is not None:
            getter = getattr(bm, "_get_or_create", None)
            active = getattr(bm, "active_provider", None)
            if callable(getter) and isinstance(active, str):
                try:
                    prov = getter(active)
                    if hasattr(prov, "complete"):
                        return prov
                except Exception as exc:  # noqa: BLE001
                    _LOG.info("creator: active-provider resolve failed (%s)", exc)
            if hasattr(bm, "complete"):
                return bm
        if self._config is not None:
            try:
                from jarvis.brain.resolver import resolve_frontier_brain

                resolved = resolve_frontier_brain(self._config, bus=self._bus)
                if hasattr(resolved, "complete"):
                    return resolved
            except Exception as exc:  # noqa: BLE001
                _LOG.info("creator: frontier resolve failed (%s)", exc)
        return None

    async def _try_brain(
        self, inp: SkillCreatorInput, *, refine: bool
    ) -> dict[str, Any] | None:
        brain = self._resolve_brain()
        if brain is None:
            return None
        from jarvis.brain.streaming import aggregate
        from jarvis.core.protocols import BrainMessage, BrainRequest

        system, user = _build_prompt(inp, refine=refine)
        request = BrainRequest(
            messages=(BrainMessage(role="user", content=user),),
            system=system,
            max_tokens=_BRAIN_MAX_TOKENS,
            temperature=0.4,
            stream=True,
        )
        try:
            agg = await asyncio.wait_for(
                aggregate(brain.complete(request)), timeout=_BRAIN_TIMEOUT_S
            )
        except (TimeoutError, asyncio.TimeoutError):
            _LOG.warning("creator: brain timed out — using skeleton")
            return None
        except Exception as exc:  # noqa: BLE001
            _LOG.warning("creator: brain call failed (%s) — using skeleton", exc)
            return None
        return _extract_json(agg.text)


# ----------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------

_SYSTEM_PROMPT = """You are Skill Creator. The user wants a new "skill" — a small \
instruction card the assistant can trigger. Reply with ONE JSON object and nothing \
else (no prose, no markdown fence). The object MUST have these keys:
  name (short Title Case), description (one sentence), category, tags (array of \
strings), triggers (array of {type:"voice"|"hotkey"|"schedule", pattern/combo/cron}), \
requires_tools (array of tool names), risk_policy ({default_tier:"safe"|"monitor"|"ask"}), \
body (Markdown instructions), questions (array), assumptions (array), test_prompts (array).
Never put executable code with eval/exec/os.system/subprocess(shell=True) in the body."""


def _build_prompt(inp: SkillCreatorInput, *, refine: bool) -> tuple[str, str]:
    parts = [f"Intent: {inp.intent}"]
    if inp.name_hint:
        parts.append(f"Preferred name: {inp.name_hint}")
    if inp.category:
        parts.append(f"Category hint: {inp.category}")
    if inp.trigger_hint:
        parts.append(f"Trigger hint: {inp.trigger_hint}")
    if inp.extra_context:
        parts.append(f"Extra context: {inp.extra_context}")
    if refine and inp.existing_draft:
        parts.append(
            "Revise this previous draft:\n"
            + json.dumps(inp.existing_draft, ensure_ascii=False)
        )
    if refine and inp.feedback:
        parts.append(f"User feedback to apply: {inp.feedback}")
    parts.append("Return only the JSON object.")
    return _SYSTEM_PROMPT, "\n".join(parts)


__all__ = [
    "SkillCreatorInput",
    "SkillCreatorResult",
    "SkillCreatorService",
    "render_skill_md",
    "validate_skill_md",
]
