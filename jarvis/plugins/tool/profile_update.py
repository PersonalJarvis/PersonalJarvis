"""``update-profile`` tool — deterministic brain-driven writer for the
structured user profile (the five USER.md clusters).

Why this tool exists
--------------------
The Desktop App "Knowledge matrix" and the brain's per-turn system prompt both
read the structured profile from ``data/workspace/USER.md`` — the five clusters
``identity / communication / work_style / values / relationship`` (see
``jarvis/memory/user_profile.py`` and ``jarvis/brain/manager.py``:
``_build_system_prompt`` calls ``UserProfile.render_for_prompt()`` every turn).

The legacy background ``Curator`` that used to auto-write those clusters is
soft-disabled (``[memory.legacy_curator] enabled = false``, 2026-05-17) to avoid
the "two diverging notebooks" drift with the WikiCurator. The active WikiCurator
only writes free-form wiki *prose* — it never touches the structured clusters.
Net effect: durable personal facts the user states ("call me Boss", "my
favourite food is pizza") never reach the profile the brain and the matrix use,
so the matrix froze and the brain stopped learning structured facts.

This tool is the deterministic replacement. When the router-brain recognises a
durable personal fact, it calls ``update_profile`` to persist it directly — no
second background extractor, no drift, immediate effect. It mutates the SAME
live ``UserProfile`` instance the ``BrainManager`` renders from (injected via a
resolver in ``factory._load_tools_for_tier``), so the very next turn's system
prompt reflects the change, and it emits ``ProfileUpdated`` so the Desktop
matrix live-updates without a reload.

Risk tier
---------
``monitor``: it writes USER.md (a real side effect) but must run *without* a
confirmation prompt (anti-confirmation-fatigue). Every invocation is logged for
audit, exactly like ``wiki-ingest``.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from jarvis.core.protocols import ToolResult

log = logging.getLogger(__name__)

# Canonical field allow-list per cluster. The tool refuses fields outside this
# map so the brain cannot write fields the Knowledge matrix never renders (that
# would be silent multi-layer enum drift — BUG-008 class, CLAUDE.md §recurring
# bugs #2). It must stay BYTE-FOR-BYTE in sync with ProfileView.tsx's
# CLUSTER_FIELD_KEYS (the UI is the authority for what is visible). The parity is
# pinned by test_profile_update.py::test_canonical_fields_match_matrix_ui.
# NOTE: when adding a *list* field here, also add it to _LIST_FIELDS below.
_CANONICAL_FIELDS: dict[str, frozenset[str]] = {
    "identity": frozenset({
        "name", "preferred_address", "pronouns", "primary_language",
        "languages", "timezone", "devices",
    }),
    "communication": frozenset({
        "directness", "formality", "verbosity", "humor_types", "emoji_ok",
    }),
    "work_style": frozenset({"focus_mode", "planning_horizon"}),
    "values": frozenset({"top_values", "pet_peeves", "motivations"}),
    "relationship": frozenset({"feedback_pref"}),
}

# Fields that hold a list — an append (deduped) rather than a scalar set. The
# field type is authoritative: a list field is ALWAYS appended, even if the
# model passes operation="set" (so it can never clobber a list into a scalar).
_LIST_FIELDS: frozenset[tuple[str, str]] = frozenset({
    ("identity", "languages"),
    ("identity", "devices"),
    ("communication", "humor_types"),
    ("values", "top_values"),
    ("values", "pet_peeves"),
    ("values", "motivations"),
})

# Boolean fields — coerce the truthy/falsey spellings the model may emit.
_BOOL_FIELDS: frozenset[tuple[str, str]] = frozenset({
    ("communication", "emoji_ok"),
})

# Privacy contract (USER.md "Do Not Record" + the legacy curator validator):
# never persist these categories, however the model phrases them. Matched
# case-insensitively as substrings of value + evidence.
_DO_NOT_RECORD: tuple[str, ...] = (
    "politik", "politisch", "political", "partei", "wahl",
    "religion", "religiös", "religioes", "religious", "kirche", "glaube an gott",  # i18n-allow (DE match vocabulary for a privacy-category filter)
    "diagnose", "diagnos", "depression", "angststörung", "anxiety",  # i18n-allow (DE match vocabulary for a privacy-category filter)
    "therapie", "therapy", "medikament", "krankheit", "mental health",
    "suizid", "suicide", "mbti", "myers-briggs", "enneagram",
)

_TRUE_WORDS: frozenset[str] = frozenset({"true", "ja", "yes", "1", "on", "erlaubt", "ok", "okay"})  # i18n-allow (DE boolean tokens a bilingual model may emit)
_FALSE_WORDS: frozenset[str] = frozenset({"false", "nein", "no", "0", "off", "keine", "verboten"})  # i18n-allow (DE boolean tokens a bilingual model may emit)


def _coerce_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    s = str(value).strip().lower()
    if s in _TRUE_WORDS:
        return True
    if s in _FALSE_WORDS:
        return False
    return None


def _short(value: Any, n: int = 80) -> str:
    s = str(value).strip()
    return s if len(s) <= n else s[: n - 1] + "…"


class UpdateProfileTool:
    """Router-tier deterministic writer for the structured USER.md profile."""

    name: str = "update_profile"
    description: str = (
        "Persist a durable, factual detail the user states (or corrects) ABOUT "
        "THEMSELVES into their structured profile, so you and the Knowledge "
        "matrix remember it across sessions. Call this the moment the user says "
        "things like 'my name is …', 'call me …', 'I speak … / switch to …', "
        "'my favourite … is …', 'I hate it when …', 'I'm in timezone …', 'I work "
        "on a …'. Do NOT use it for transient state, tasks, one-off requests, or "
        "facts about OTHER people. Do NOT ask for confirmation — store it "
        "silently in addition to your normal reply. One call per fact."
    )
    # `monitor`: real side effect (writes USER.md) but no confirmation prompt
    # (anti-confirmation-fatigue); every call is logged. Mirrors wiki-ingest.
    risk_tier: str = "monitor"
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "cluster": {
                "type": "string",
                "enum": ["identity", "communication", "work_style", "values", "relationship"],
                "description": "Which profile cluster the fact belongs to.",
            },
            "field": {
                "type": "string",
                "description": (
                    "Canonical field within the cluster. "
                    "identity: name, preferred_address, pronouns, primary_language, "
                    "languages, timezone, devices. "
                    "communication: directness, formality, verbosity, humor_types, "
                    "emoji_ok. "
                    "work_style: focus_mode, planning_horizon. "
                    "values: top_values, pet_peeves, motivations. "
                    "relationship: feedback_pref."
                ),
            },
            "value": {
                "description": (
                    "The value to store: a string/number/boolean for scalar "
                    "fields; for list fields (languages, devices, humor_types, "
                    "top_values, pet_peeves, motivations) pass a SINGLE item to "
                    "append."
                ),
            },
            "operation": {
                "type": "string",
                "enum": ["set", "append"],
                "description": (
                    "Optional hint. List fields are always appended (deduped); "
                    "scalar fields are always set. The field type decides."
                ),
            },
            "evidence": {
                "type": "string",
                "description": "The user's own words that justify this fact (audit trail).",
            },
        },
        "required": ["cluster", "field", "value"],
    }
    input_examples: list[dict[str, Any]] = [
        {"cluster": "identity", "field": "preferred_address", "value": "Boss",
         "evidence": "Call me Boss from now on."},
        {"cluster": "values", "field": "top_values", "value": "Sushi",
         "evidence": "My favourite food is sushi."},
        {"cluster": "identity", "field": "primary_language", "value": "German",
         "evidence": "Let's switch everything to German."},
    ]

    def __init__(self, *, profile_resolver: Callable[[], Any], bus: Any = None) -> None:
        # Lazy resolver so the tool always mutates the live UserProfile the
        # BrainManager renders from, even though both are wired at build time
        # (mirrors the wiki-ingest curator-resolver pattern).
        self._resolve_profile = profile_resolver
        self._bus = bus

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        cluster = str(args.get("cluster", "")).strip().lower()
        field = str(args.get("field", "")).strip().lower()
        value = args.get("value")
        evidence = str(args.get("evidence") or "").strip()

        if cluster not in _CANONICAL_FIELDS:
            return ToolResult(
                success=False, output="",
                error=f"unknown cluster {cluster!r}; expected one of "
                      f"{sorted(_CANONICAL_FIELDS)}",
            )
        if field not in _CANONICAL_FIELDS[cluster]:
            return ToolResult(
                success=False, output="",
                error=f"unknown field {field!r} for cluster {cluster!r}; expected "
                      f"one of {sorted(_CANONICAL_FIELDS[cluster])}",
            )
        if value is None or (isinstance(value, str) and not value.strip()):
            return ToolResult(success=False, output="", error="missing 'value'")

        # Privacy gate — never persist do-not-record categories. Return success
        # (not an error) so the brain does not retry or claim a failure.
        haystack = f"{value} {evidence}".lower()
        if any(p in haystack for p in _DO_NOT_RECORD):
            log.info("update_profile: declined (privacy category) for %s.%s", cluster, field)
            return ToolResult(
                success=True,
                output=(
                    "Not stored — that touches a do-not-record category "
                    "(politics / religion / health). Profile unchanged."
                ),
            )

        profile = self._resolve_profile()
        if profile is None:
            return ToolResult(success=False, output="", error="user profile not available")

        if (cluster, field) in _BOOL_FIELDS:
            coerced = _coerce_bool(value)
            if coerced is None:
                return ToolResult(
                    success=False, output="",
                    error=f"field {cluster}.{field} expects a boolean (true/false)",
                )
            value = coerced

        is_list = (cluster, field) in _LIST_FIELDS
        try:
            if is_list:
                op = "append"
                if isinstance(value, (list, tuple)):
                    changed = False
                    for item in value:
                        changed = profile.append_list(cluster, field, item) or changed
                else:
                    changed = profile.append_list(cluster, field, value)
            else:
                op = "set"
                changed = profile.set(cluster, field, value)
        except ValueError as exc:  # unknown cluster slipped past (defensive)
            return ToolResult(success=False, output="", error=str(exc))

        if not changed:
            return ToolResult(
                success=True,
                output=f"Already known — {cluster}.{field} already had that value. No change.",
            )

        # Audit observation (mirrors the legacy curator merger) + atomic persist.
        try:
            profile.append_observation(
                f"{cluster}.{field}", _short(value), evidence or "via update_profile tool"
            )
        except Exception:  # noqa: BLE001 — the audit line must never block the write
            log.debug("update_profile: append_observation failed", exc_info=True)

        try:
            profile.save()
        except Exception as exc:  # noqa: BLE001
            log.warning("update_profile: save failed: %s", exc)
            return ToolResult(success=False, output="", error=f"could not persist profile: {exc}")

        # Live-notify the UI (and any subscriber) so the Knowledge matrix updates
        # without a reload. A bus hiccup must never fail the (already-persisted)
        # write.
        if self._bus is not None:
            try:
                from jarvis.core.events import ProfileUpdated

                await self._bus.publish(
                    ProfileUpdated(
                        subject="user", cluster=cluster, field=field,
                        operation=op, confidence=1.0, evidence=evidence[:200],
                    )
                )
            except Exception:  # noqa: BLE001
                log.debug("update_profile: ProfileUpdated publish failed", exc_info=True)

        log.info("update_profile: %s %s.%s = %r", op, cluster, field, value)
        return ToolResult(
            success=True,
            output=f"Stored {cluster}.{field} = {value} ({op}). I'll remember that.",
        )
