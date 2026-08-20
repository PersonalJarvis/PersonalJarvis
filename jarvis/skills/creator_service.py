"""AI Skill Creator — backs ``/api/skills/creator/{draft,refine,validate,commit,author}``
and the brain's ``create-skill`` router tool.

Turns a free-text intent into a structured SKILL.md draft with brain assistance,
and commits the draft as a real (inactive) skill.

Two callers, two contracts:

* **The UI creator dialog** (``draft`` / ``refine`` → user reviews → ``commit``)
  is brain-*assisted*, never brain-*dependent*: ``draft`` always returns a valid
  deterministic skeleton; if a brain is reachable and returns parseable JSON,
  the skeleton is replaced by the brain's draft (``brain_used=True``). A missing
  brain, a timeout, a refusal or malformed JSON degrade to the skeleton, which
  the user then edits before committing — so the dialog works on a headless
  box with no provider configured.

* **The voice / chat path** (``author`` — one shot: draft + commit) is
  brain-*required*. Nobody edits between draft and commit there, and a skeleton
  is the user's sentence pasted into a template, not a skill: the description
  is the raw intent, the body says "1. …", there is no schedule trigger and no
  step names a tool. Committing that would be exactly the "1:1 copy" a spoken
  request must never produce. So ``author`` raises
  :class:`SkillCreatorUnavailable` when no brain authored the draft, and the
  caller says so honestly instead of pretending a skill exists.

The brain call itself is a LADDER, not a single provider (AP-21/AP-22): the
BrainManager's active provider first (the user's selection), then the pinned
Tool Model, then the API quality tier, then the frontier chain. A dead rung —
no key, a timeout, garbage output — crosses to the next one. Measured reason:
on a box whose shell environment pins ``brain.primary`` to an unkeyed provider,
the old single-provider call died on the first attempt and every voice-authored
skill would have been a skeleton.

What makes the draft a WORKING skill rather than a paraphrase is the prompt:
the model receives the live tool inventory (which connectors are attached
right now, by name) and the installed skills, plus the Jarvis skill contract —
voice trigger = the short spoken phrase, schedule trigger = cron when a time
was named, body = numbered steps that each name the tool to use and skip
gracefully when it is missing, one flowing spoken answer at the end.

``commit`` persists the reviewed draft through the same deterministic writer as
the manual "New skill" form (``SkillAuthoringService``), so there is one code
path that touches the registry, and it ALWAYS lands as ``state: draft``
(AP-15): the content is LLM-generated, a human activates it in the Skills view.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from collections.abc import Iterator
from dataclasses import dataclass
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

# Bounded brain calls. One attempt may take a while — a good skill body is
# 600-1200 tokens and the first token on a cold cloud provider is measured in
# seconds — but a hung provider must not wedge a voice turn: the ladder crosses
# to the next rung after ``_BRAIN_TIMEOUT_S`` and stops for good after
# ``_BRAIN_TOTAL_BUDGET_S``.
_BRAIN_TIMEOUT_S = 40.0
_BRAIN_TOTAL_BUDGET_S = 80.0
_BRAIN_MAX_TOKENS = 2200

# How much of the live surface the model gets to see. Names + one line each;
# a full tool schema dump would cost more than the skill it produces.
_MAX_TOOLS_IN_PROMPT = 48
_MAX_SKILLS_IN_PROMPT = 32
_TOOL_DESCRIPTION_CHARS = 170
_SKILL_DESCRIPTION_CHARS = 110

_WORD_RE = re.compile(r"[A-Za-z0-9]+")
_CRON_RE = re.compile(r"^\s*(\S+\s+){4}\S+\s*$")

_ALLOWED_TIERS = ("safe", "monitor", "ask")

# Voice-trigger patterns that would match every utterance. A model that pastes
# the whole description, or writes ``.*``, hands the skill every turn.
_OVERBROAD_PATTERN_RE = re.compile(r"^\W*(\.\*|\.\+|\^\s*\$|\(\.\*\))\W*$")
_MAX_PATTERN_CHARS = 200


class SkillCreatorUnavailable(RuntimeError):
    """``author`` could not get a brain to write the skill.

    Raised instead of committing a deterministic skeleton: on the voice/chat
    path nobody reviews the draft, and a template with the sentence pasted in
    is not a skill the user asked for.
    """


# ----------------------------------------------------------------------
# Inputs / outputs
# ----------------------------------------------------------------------


@dataclass(frozen=True)
class SkillCreatorInput:
    """A creator request. ``intent`` is the user's free-text description; the
    rest are optional hints. ``existing_draft`` + ``feedback`` drive
    ``refine`` (a revision of a prior draft).

    ``schedule_hint`` is a cron expression or a spoken recurrence ("every
    morning at 6") the caller already extracted; ``language`` is the user's
    language code so the skill's text and trigger phrases are written in it.
    """

    intent: str
    name_hint: str = ""
    category: str = "general"
    trigger_hint: str = ""
    extra_context: str = ""
    existing_draft: dict[str, Any] | None = None
    feedback: str = ""
    schedule_hint: str = ""
    language: str = ""


@dataclass(frozen=True)
class SkillCreatorResult:
    """What ``draft``/``refine`` return — mirrors the frontend response type.

    ``brain_source`` names the provider that authored the draft (empty when the
    deterministic skeleton is what came back).
    """

    draft: dict[str, Any]
    skill_md: str
    validation: dict[str, Any]
    brain_used: bool
    brain_source: str = ""


@dataclass(frozen=True)
class AuthoredSkill:
    """``author``'s answer: the committed (draft-state) skill plus the draft."""

    skill: Skill
    result: SkillCreatorResult
    #: The name actually written — differs from the draft's when a collision
    #: forced a numeric suffix.
    name: str
    slug: str


@dataclass(frozen=True)
class AuthoringContext:
    """The live surface the author gets to see: attached tools and installed
    skills, as ``(name, one-line description)`` pairs. Empty tuples are fine —
    the prompt then simply says nothing is attached."""

    tools: tuple[tuple[str, str], ...] = ()
    skills: tuple[tuple[str, str], ...] = ()

    @property
    def tool_names(self) -> frozenset[str]:
        return frozenset(name for name, _ in self.tools)


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

    This is the always-available fallback for the UI dialog. It is
    intentionally minimal but complete enough to validate and commit unchanged
    — after the user has edited it. ``author`` never commits it.
    """
    draft = _empty_draft()
    name = inp.name_hint.strip() or _title_from_intent(inp.intent)
    draft["name"] = name
    draft["description"] = inp.intent.strip()[:400] or name
    draft["category"] = (inp.category or "general").strip() or "general"
    triggers: list[dict[str, Any]] = []
    if inp.trigger_hint.strip():
        triggers.append({"type": "voice", "pattern": inp.trigger_hint.strip()})
    if _CRON_RE.match(inp.schedule_hint or ""):
        triggers.append({"type": "schedule", "cron": inp.schedule_hint.strip()})
    draft["triggers"] = triggers
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
    draft["assumptions"] = ["Generated deterministically without a brain — edit before committing."]
    return draft


def _clean_triggers(raw: Any, inp: SkillCreatorInput) -> list[dict[str, Any]]:
    """Keep only triggers the loader will accept and that cannot hijack turns.

    * a voice trigger needs a compilable, non-empty, non-overbroad pattern;
    * a schedule trigger needs a 5-field cron (croniter-validated when the
      library is present);
    * a hotkey trigger needs a combo;
    * when the caller supplied a cron ``schedule_hint`` and the model produced
      no schedule trigger, the hint is added — the user said "every morning
      at 6" and a skill without that trigger would not be what they asked for.
    """
    cleaned: list[dict[str, Any]] = []
    have_schedule = False
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        kind = str(item.get("type", "")).strip().lower()
        if kind == "voice":
            pattern = str(item.get("pattern", "") or "").strip()
            if not pattern or len(pattern) > _MAX_PATTERN_CHARS:
                continue
            if _OVERBROAD_PATTERN_RE.match(pattern):
                continue
            try:
                re.compile(pattern)
            except re.error as exc:
                _LOG.debug(
                    "creator: dropped voice trigger with invalid regex %r (%s)", pattern, exc
                )
                continue
            trig: dict[str, Any] = {"type": "voice", "pattern": pattern}
            langs = item.get("language")
            if isinstance(langs, list) and all(isinstance(x, str) for x in langs) and langs:
                trig["language"] = [x.strip().lower()[:5] for x in langs if x.strip()]
            cleaned.append(trig)
        elif kind == "schedule":
            cron = str(item.get("cron", "") or "").strip()
            if not _cron_is_valid(cron):
                continue
            have_schedule = True
            cleaned.append({"type": "schedule", "cron": cron})
        elif kind == "hotkey":
            combo = str(item.get("combo", "") or "").strip()
            if combo:
                cleaned.append({"type": "hotkey", "combo": combo})
    hint = (inp.schedule_hint or "").strip()
    if not have_schedule and _cron_is_valid(hint):
        cleaned.append({"type": "schedule", "cron": hint})
    return cleaned


def _cron_is_valid(cron: str) -> bool:
    if not cron or not _CRON_RE.match(cron):
        return False
    try:
        from croniter import croniter  # type: ignore

        return bool(croniter.is_valid(cron))
    except Exception:  # noqa: BLE001 — croniter optional; superficial check stands
        return True


def _coerce_brain_draft(
    data: dict[str, Any],
    inp: SkillCreatorInput,
    context: AuthoringContext | None = None,
) -> dict[str, Any]:
    """Merge a brain-produced dict onto the empty-draft shape, dropping unknown
    keys, backfilling required ones from the skeleton, and normalising the
    fields the loader is strict about."""
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
        "requires_tools",
        "questions",
        "assumptions",
        "test_prompts",
    ):
        if not isinstance(draft[list_key], list):
            draft[list_key] = []
        else:
            draft[list_key] = [str(x) for x in draft[list_key] if isinstance(x, (str, int, float))]
    draft["triggers"] = _clean_triggers(draft.get("triggers"), inp)
    if not isinstance(draft["risk_policy"], dict):
        draft["risk_policy"] = {"default_tier": "ask"}
    tier = str(draft["risk_policy"].get("default_tier", "") or "").strip().lower()
    if tier not in _ALLOWED_TIERS:
        draft["risk_policy"]["default_tier"] = "ask"
    else:
        draft["risk_policy"]["default_tier"] = tier
    # ``requires_tools`` must name tools that exist, or the skill carries a
    # permanent validator warning for a name the model invented. With a live
    # inventory the list is filtered against it; without one it stays as is.
    if context is not None and context.tools:
        known = context.tool_names
        draft["requires_tools"] = [t for t in draft["requires_tools"] if t in known]
    draft["name"] = str(draft["name"]).strip()[:80] or skeleton["name"]
    draft["description"] = str(draft["description"]).strip() or skeleton["description"]
    draft["category"] = str(draft["category"]).strip() or "general"
    body = str(draft["body"]).strip()
    draft["body"] = body if body else skeleton["body"]
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
# Live authoring context (what the model gets to see)
# ----------------------------------------------------------------------


def _one_line(text: Any, limit: int) -> str:
    flat = " ".join(str(text or "").split())
    return flat if len(flat) <= limit else flat[: limit - 1] + "…"


def build_authoring_context(
    *,
    brain_manager: Any | None = None,
    registry: Any | None = None,
) -> AuthoringContext:
    """Collect the attached tools and installed skills for the author prompt.

    ``brain_manager`` is the live BrainManager (its ``_tools`` dict IS the
    surface the assistant will use at run time, so the names here are the
    names the skill body may reference); ``registry`` is the skill registry.
    Both optional; both read defensively — this must never break a turn.
    """
    tools: list[tuple[str, str]] = []
    try:
        live = getattr(brain_manager, "_tools", None) or {}
        for name in sorted(live):
            tool = live[name]
            desc = _one_line(getattr(tool, "description", ""), _TOOL_DESCRIPTION_CHARS)
            tools.append((str(name), desc))
            if len(tools) >= _MAX_TOOLS_IN_PROMPT:
                break
    except Exception:  # noqa: BLE001 — a broken tool object costs one line, not the draft
        _LOG.debug("authoring context: tool inventory unreadable", exc_info=True)
    skills: list[tuple[str, str]] = []
    try:
        active = registry.list_active() if registry is not None else []
        for skill in active:
            fm = getattr(skill, "frontmatter", None)
            desc = _one_line(getattr(fm, "description", ""), _SKILL_DESCRIPTION_CHARS)
            skills.append((str(getattr(skill, "name", "")), desc))
            if len(skills) >= _MAX_SKILLS_IN_PROMPT:
                break
    except Exception:  # noqa: BLE001
        _LOG.debug("authoring context: skill list unreadable", exc_info=True)
    return AuthoringContext(tools=tuple(tools), skills=tuple(skills))


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
        context: AuthoringContext | None = None,
    ) -> None:
        self._brain = brain
        self._registry = registry
        self._bus = bus
        self._config = config
        self._user_skills_root = user_skills_root
        self._context = context

    async def draft(self, inp: SkillCreatorInput) -> SkillCreatorResult:
        return await self._draft_or_refine(inp)

    async def refine(self, inp: SkillCreatorInput) -> SkillCreatorResult:
        return await self._draft_or_refine(inp, refine=True)

    async def author(self, inp: SkillCreatorInput) -> AuthoredSkill:
        """One shot for voice/chat: brain-authored draft, committed as a draft.

        Raises :class:`SkillCreatorUnavailable` when no brain produced the
        draft — the deterministic skeleton is never committed from here (see
        the module docstring). Raises :class:`SkillAuthoringError` when the
        writer refuses (bad name, dead body, disk error). A name collision is
        resolved by suffixing "-2", "-3", … — the user asked for the skill and
        gets it, under a name the answer reports.

        The committed name is SLUGGED. ``name`` is the registry key, and the
        authoring prompt asks the model for a Title Case display name, so this
        path used to write keys like ``Morning Routine 2`` into a registry
        whose builtins are keyed ``morning-routine``. Two naming conventions in
        one key space is what made ``run-skill`` unable to find a skill by any
        name a human would say (live 2026-08-20). One convention, everywhere.
        """
        result = await self._draft_or_refine(inp)
        if not result.brain_used:
            raise SkillCreatorUnavailable(
                "no brain could author the skill right now — nothing was written"
            )
        from jarvis.skills.authoring.service import slugify

        draft = dict(result.draft)
        raw_name = str(draft.get("name", "")).strip()
        # Fall back to the raw name only when it has no slugable characters at
        # all (a name in a non-Latin script); the writer then rejects it with a
        # clear message instead of this method inventing one.
        base_name = slugify(raw_name) or raw_name
        last_error: SkillAuthoringError | None = None
        for attempt in range(1, 6):
            draft["name"] = base_name if attempt == 1 else f"{base_name}-{attempt}"
            try:
                skill = await self.commit(draft)
            except SkillAuthoringError as exc:
                if exc.status != 409:
                    raise
                last_error = exc
                continue
            return AuthoredSkill(
                skill=skill,
                result=SkillCreatorResult(
                    draft=draft,
                    skill_md=render_skill_md(draft),
                    validation=result.validation,
                    brain_used=True,
                    brain_source=result.brain_source,
                ),
                name=draft["name"],
                slug=slugify(draft["name"]),
            )
        assert last_error is not None
        raise last_error

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
        brain_source = ""

        context = self._authoring_context()
        brain_answer = await self._try_brain(inp, refine=refine, context=context)
        if brain_answer is not None:
            brain_draft, brain_source = brain_answer
            draft = _coerce_brain_draft(brain_draft, inp, context)
            brain_used = True

        skill_md = render_skill_md(draft)
        validation, _ = validate_skill_md(skill_md)
        # If a brain draft somehow failed validation, fall back to the skeleton
        # (which is constructed to always validate).
        if not validation["ok"] and brain_used:
            _LOG.warning(
                "creator: brain draft from %s failed validation (%s) — using skeleton",
                brain_source,
                "; ".join(validation.get("errors") or []),
            )
            draft = skeleton
            brain_used = False
            brain_source = ""
            skill_md = render_skill_md(draft)
            validation, _ = validate_skill_md(skill_md)

        return SkillCreatorResult(
            draft=draft,
            skill_md=skill_md,
            validation=validation,
            brain_used=brain_used,
            brain_source=brain_source,
        )

    def _authoring_context(self) -> AuthoringContext:
        """The injected context, or one collected from what was injected.

        Reads ONLY the objects this service was given (the injected brain when
        it is the live BrainManager, and the registry) — never a process-wide
        reference. A service built without a brain must stay inert: no tool
        inventory, no provider probing.
        """
        if self._context is not None:
            return self._context
        bm = self._brain
        manager = bm if isinstance(getattr(bm, "_tools", None), dict) else None
        return build_authoring_context(brain_manager=manager, registry=self._registry)

    def _candidate_brains(self) -> Iterator[tuple[Any, str]]:
        """Brains to try, best first, each with a label for the log and the
        result. Never raises; yields nothing when nothing is reachable.

        Multi-provider contract (AP-21/AP-22): follow the user's ACTIVE
        selection first, never a hardcoded frontier favourite; then the model
        the user pinned for the assistant's own work (Tool Model); then the API
        quality tier; then the frontier chain. Each rung is only *tried* — a
        rung that cannot answer (no key, timeout, garbage) crosses to the next
        in :meth:`_try_brain`.
        """
        seen: set[int] = set()

        def _fresh(brain: Any) -> bool:
            if brain is None or not hasattr(brain, "complete"):
                return False
            key = id(brain)
            if key in seen:
                return False
            seen.add(key)
            return True

        bm = self._brain
        if bm is not None:
            getter = getattr(bm, "_get_or_create", None)
            active = getattr(bm, "active_provider", None)
            if callable(getter) and isinstance(active, str) and active:
                try:
                    prov = getter(active)
                except Exception as exc:  # noqa: BLE001
                    _LOG.info("creator: active-provider resolve failed (%s)", exc)
                    prov = None
                if _fresh(prov):
                    yield prov, f"active:{active}"
            elif _fresh(bm):
                # A raw provider was injected (tests, embedded use).
                yield bm, "injected"

        # The resolver rungs need a config; without one the ladder ends here.
        # Deliberately no process-wide lookup: a service built without brain
        # and config (a headless route, a test) must not start probing
        # providers on its own.
        config = self._config
        if config is None:
            return

        try:
            from jarvis.brain.resolver import (
                resolve_frontier_brain,
                resolve_quality_brain,
                resolve_tool_model_brain,
            )
        except Exception as exc:  # noqa: BLE001
            _LOG.info("creator: brain resolver unavailable (%s) — provider ladder ends here", exc)
            return

        for label, resolver in (
            ("tool_model", resolve_tool_model_brain),
            ("quality", resolve_quality_brain),
            ("frontier", resolve_frontier_brain),
        ):
            try:
                brain = resolver(config, bus=self._bus)
            except Exception as exc:  # noqa: BLE001
                _LOG.info("creator: %s resolve failed (%s)", label, exc)
                continue
            if _fresh(brain):
                name = getattr(brain, "name", None) or label
                yield brain, f"{label}:{name}"

    async def _try_brain(
        self,
        inp: SkillCreatorInput,
        *,
        refine: bool,
        context: AuthoringContext | None = None,
    ) -> tuple[dict[str, Any], str] | None:
        """Ask the brains in ladder order; the first parseable draft wins.

        Returns ``(draft_dict, source_label)`` or ``None`` when every rung
        failed or the total budget is spent.
        """
        from jarvis.brain.streaming import aggregate
        from jarvis.core.protocols import BrainMessage, BrainRequest

        system, user = _build_prompt(inp, refine=refine, context=context)
        request = BrainRequest(
            messages=(BrainMessage(role="user", content=user),),
            system=system,
            max_tokens=_BRAIN_MAX_TOKENS,
            temperature=0.4,
            stream=True,
        )
        started = time.monotonic()
        for brain, label in self._candidate_brains():
            remaining = _BRAIN_TOTAL_BUDGET_S - (time.monotonic() - started)
            if remaining <= 1.0:
                _LOG.warning("creator: brain budget spent — no more rungs tried")
                return None
            timeout = min(_BRAIN_TIMEOUT_S, remaining)
            try:
                agg = await asyncio.wait_for(aggregate(brain.complete(request)), timeout=timeout)
            except TimeoutError:
                _LOG.warning("creator: %s timed out after %.0fs — next rung", label, timeout)
                continue
            except Exception as exc:  # noqa: BLE001
                _LOG.warning("creator: %s failed (%s) — next rung", label, exc)
                continue
            data = _extract_json(agg.text)
            if data is None:
                _LOG.warning("creator: %s returned no JSON draft — next rung", label)
                continue
            _LOG.info("creator: %s authored the draft in %.1fs", label, time.monotonic() - started)
            return data, label
        return None


# ----------------------------------------------------------------------
# Prompt
# ----------------------------------------------------------------------

_SYSTEM_PROMPT = """You are the Skill Author of a personal voice assistant. A "skill" is a \
SKILL.md card: YAML frontmatter plus Markdown instructions that the assistant follows \
step by step when the skill fires, using the tools it has connected. Your job is to turn \
the user's spoken description into a COMPLETE, WORKING skill — not a paraphrase of what \
they said. Reply with ONE JSON object and nothing else (no prose, no markdown fence).

Keys of the object:
- name: short Title Case name, 1-4 words, in the user's language ("Morgenroutine").
- description: ONE sentence in the user's language: what the skill does AND when it \
fires. This text is also how the assistant recognises the skill in new wording, so name \
the actions and objects the user would say.
- category: one of general, productivity, media, communication, dev, system, memory, home.
- tags: 3-6 lowercase keywords.
- triggers: array. Voice: {"type":"voice","pattern":"<regex>","language":["de","en"]} \
where the pattern is the SHORT spoken phrase the user would say to start it — 1-4 words, \
lowercase, alternatives allowed, no anchors, e.g. "(morgenroutine|morning routine)". \
NEVER the description and never ".*". Schedule: {"type":"schedule","cron":"m h * * *"} \
whenever the user named a time or recurrence ("jeden Morgen um 6" → "0 6 * * *", local \
time). Include BOTH kinds when both apply. Omit hotkeys unless the user asked for one.
- requires_tools: [] — unless you copy exact names from AVAILABLE TOOLS.
- risk_policy: {"default_tier":"monitor"} for reading, playing, summarising, showing; \
"ask" when the skill sends messages, deletes, pays, posts publicly or changes settings.
- body: the instructions in Markdown, in the user's language, that the assistant will \
follow at run time. Structure it as: one line stating the goal; a numbered "Steps" \
section where EACH step names the connector/tool from AVAILABLE TOOLS it uses (or says \
"if a tool for X is connected"), what exactly to fetch or do, and how to keep it short \
for speech; the standing rule "skip a step gracefully in one short clause when its \
integration is unavailable — never invent data"; then an "Answer format" section: ONE \
flowing spoken reply of 3-6 short sentences, no lists, no markdown, no tool names, in the \
user's language, ending with the most actionable item. Keep every specific the user gave \
(times, counts, genres, examples such as "an 80s classic like Country Roads, a different \
one each time", who or what to include). If the user described a routine with several \
parts, every part becomes a step in the order they said it.
- questions: things you genuinely could not infer (usually []).
- assumptions: decisions you made on the user's behalf, one short line each.
- test_prompts: 2-3 utterances that should start this skill.

The user's description is a speech transcript: it may contain filler words, self-\
corrections and misheard brand names (a ticket tool heard as a first name, a service \
name split into two words). Infer what they meant and map it to the closest entry in \
AVAILABLE TOOLS; mention that mapping under assumptions. Never put shell code, \
eval/exec, credentials or API keys in the body."""


def _build_prompt(
    inp: SkillCreatorInput,
    *,
    refine: bool,
    context: AuthoringContext | None = None,
) -> tuple[str, str]:
    parts = [f"USER'S DESCRIPTION (spoken transcript):\n{inp.intent.strip()}"]
    if inp.name_hint:
        parts.append(f"Preferred name: {inp.name_hint}")
    if inp.category and inp.category != "general":
        parts.append(f"Category hint: {inp.category}")
    if inp.trigger_hint:
        parts.append(f"Trigger hint (spoken phrase or schedule): {inp.trigger_hint}")
    if inp.schedule_hint:
        parts.append(f"Schedule hint: {inp.schedule_hint}")
    if inp.language:
        parts.append(
            f"User's language: {inp.language} — write name, description, body and "
            "voice trigger phrases in it."
        )
    if inp.extra_context:
        parts.append(f"Extra context: {inp.extra_context}")
    if context is not None:
        if context.tools:
            lines = "\n".join(
                f"- {name} — {desc}" if desc else f"- {name}" for name, desc in context.tools
            )
            parts.append(
                "AVAILABLE TOOLS (connected right now — use these names in the "
                f"steps; nothing else exists):\n{lines}"
            )
        else:
            parts.append(
                "AVAILABLE TOOLS: none attached right now — write the steps as "
                "'if a tool for X is connected' and keep them honest."
            )
        if context.skills:
            lines = "\n".join(
                f"- {name} — {desc}" if desc else f"- {name}" for name, desc in context.skills
            )
            parts.append(
                "INSTALLED SKILLS (do not duplicate one; you may model the new "
                f"skill on one):\n{lines}"
            )
    if refine and inp.existing_draft:
        parts.append(
            "Revise this previous draft:\n" + json.dumps(inp.existing_draft, ensure_ascii=False)
        )
    if refine and inp.feedback:
        parts.append(f"User feedback to apply: {inp.feedback}")
    parts.append("Return only the JSON object.")
    return _SYSTEM_PROMPT, "\n\n".join(parts)


__all__ = [
    "AuthoredSkill",
    "AuthoringContext",
    "SkillCreatorInput",
    "SkillCreatorResult",
    "SkillCreatorService",
    "SkillCreatorUnavailable",
    "build_authoring_context",
    "render_skill_md",
    "validate_skill_md",
]
