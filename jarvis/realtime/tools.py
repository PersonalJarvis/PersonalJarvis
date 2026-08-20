"""Provider-neutral realtime tool declarations and safe execution bridge."""

from __future__ import annotations

import hashlib
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, cast
from uuid import UUID, uuid4

from jarvis.brain.cu_gate import (
    CU_BLOCKED_MODEL_FEEDBACK,
    CU_VEHICLE_TOOL_NAMES,
    llm_computer_use_allowed,
)
from jarvis.brain.spawn_gate import (
    SPAWN_VEHICLE_TOOL_NAMES,
    llm_spawn_allowed,
    spawn_blocked_feedback,
)
from jarvis.brain.tool_use_loop import (
    _is_instructional_question,
    _is_meta_debug_intent,
    _is_self_identification,
    _is_side_effect_tool,
    _is_stt_hallucinated,
    _should_block_action_as_research,
)
from jarvis.brain.turn_planner import (
    is_action_order,
    is_assistant_tasking,
    plan_turn,
)
from jarvis.core import runtime_refs
from jarvis.core.protocols import (
    RiskTier,
    SupervisorToolDescriptor,
    SupervisorToolGateway,
    SupervisorToolRequest,
)
from jarvis.safety.tool_executor import VOICE_CONFIRM_SENTINEL
from jarvis.voice.echo_confirmation import classify_response
from jarvis.voice.tool_confirmation import format_tool_confirmation

_VALID_WIRE_NAME = re.compile(r"^[A-Za-z_][A-Za-z0-9_]{0,63}$")
_MAX_DESCRIPTION_CHARS = 4_000
_MAX_ARGUMENT_CHARS = 32_000
_MAX_RESULT_CHARS = 8_000
# ADR-0035 §4 compact rendering for the live model: router-brain tool
# descriptions run to 500-2 900 characters of usage prose written for a
# text model with a 12-round loop; the live model needs the purpose and the
# parameters, and the ToolExecutor enforces the risk rules regardless. A
# sentence boundary is preferred when cutting.
COMPACT_DESCRIPTION_CHARS = 450
COMPACT_PARAMETER_DESCRIPTION_CHARS = 120
#: Rough token estimate for the declaration budget (characters per token).
CHARS_PER_TOKEN = 4
# Drop order when a declaration set exceeds its budget (ADR-0035 §4): the
# lowest-priority family goes first, longest declaration first inside it.
# Dropped tools stay reachable through ``jarvis_action``. Families are
# matched on the tool name; anything unmatched is the last to go.
#
# Order (live lesson 2026-08-19 12:50, the first hybrid session): one freshly
# installed MCP plugin contributed ~80 namespaced tools and the budget then
# dropped the ENTIRE coding-workspace family (agentic-ide-*) while dozens of
# that plugin's tools stayed declared. Namespaced plugin/MCP tools go first —
# they are the most numerous, the most domain-specific, and each is gated at
# execute time on the user naming its service anyway — then connected CLIs,
# then the coding workspace, then everything else.
_BUDGET_DROP_FAMILIES: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("mcp", re.compile(r"^mcp__|/")),
    ("cli", re.compile(r"^cli_")),
    ("agentic-ide", re.compile(r"^agentic[-_]ide[-_]")),
)

log = logging.getLogger(__name__)


def _wire_name(name: str) -> str:
    """Return a deterministic identifier accepted by both provider families."""
    if _VALID_WIRE_NAME.fullmatch(name):
        return name
    normalized = re.sub(r"[^A-Za-z0-9_]", "_", name).strip("_") or "tool"
    if not normalized[0].isalpha() and normalized[0] != "_":
        normalized = f"tool_{normalized}"
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:10]
    return f"{normalized[:52]}_{digest}"


def _plain_wire_alias(name: str) -> str:
    """Hyphen-to-underscore form without the uniqueness hash.

    Vertex Live declares hyphenated catalog names as ``run_skill_<digest>``
    because ``run-skill`` is not a legal identifier. Models routinely drop
    the digest and call ``run_skill``. Live 2026-08-20: that miss surfaced as
    "unknown realtime tool" and the user heard that the skill could not load.
    """
    alias = re.sub(r"[^A-Za-z0-9_]", "_", str(name or "")).strip("_")
    if not alias or not _VALID_WIRE_NAME.fullmatch(alias):
        return ""
    return alias


def canonical_tool_wire_name(name: str) -> str:
    """Strip a Gemini/Vertex tool-set prefix from a function-call name.

    Vertex Live groups ``function_declarations`` under an unnamed Tool and
    reports calls as ``{toolset}:{function}`` (live 2026-08-19 16:07:
    ``default:run_shell``). AI Studio and OpenAI send the bare declared
    name. Only an identifier prefix plus a legal wire-name suffix is
    stripped — URLs and other colons stay intact. The original name is
    still what ``send_tool_result`` must echo back on the wire.
    """
    raw = str(name or "").strip()
    if not raw or ":" not in raw:
        return raw
    prefix, suffix = raw.split(":", 1)
    if suffix and prefix.isidentifier() and _VALID_WIRE_NAME.fullmatch(suffix):
        return suffix
    return raw


def _json_safe(value: Any) -> Any:
    return json.loads(json.dumps(value, ensure_ascii=False, default=str))


def _bounded_result(success: bool, output: Any, error: str | None) -> dict[str, Any]:
    payload = {
        "success": bool(success),
        "output": _json_safe(output),
        "error": str(error) if error else None,
    }
    serialized = json.dumps(payload, ensure_ascii=False, default=str)
    if len(serialized) <= _MAX_RESULT_CHARS:
        return payload
    return {
        "success": bool(success),
        "output": serialized[:_MAX_RESULT_CHARS],
        "error": (
            f"Tool output was truncated from {len(serialized)} characters "
            f"to {_MAX_RESULT_CHARS}."
        ),
        "truncated": True,
    }


def _compact_text(text: str, limit: int) -> str:
    """Cut ``text`` to ``limit`` characters, preferring a sentence boundary."""
    value = str(text or "").strip()
    if len(value) <= limit:
        return value
    cut = value[:limit]
    boundary = max(cut.rfind(". "), cut.rfind(".\n"), cut.rfind("! "), cut.rfind("? "))
    if boundary >= limit // 2:
        return cut[: boundary + 1].strip()
    return cut.rstrip() + "…"


def _compact_schema(schema: Any, parameter_description_limit: int) -> Any:
    """Return ``schema`` with every nested ``description`` capped; structure intact."""
    if isinstance(schema, dict):
        compacted: dict[str, Any] = {}
        for key, value in schema.items():
            if key == "description" and isinstance(value, str):
                compacted[key] = _compact_text(value, parameter_description_limit)
            else:
                compacted[key] = _compact_schema(value, parameter_description_limit)
        return compacted
    if isinstance(schema, list):
        return [_compact_schema(item, parameter_description_limit) for item in schema]
    return schema


def _plugin_id_of(name: str) -> str:
    """``"<plugin>/<tool>"`` → ``"<plugin>"``; ``""`` for native tools."""
    pid, sep, _rest = str(name or "").partition("/")
    return pid if sep else ""


_CALL_RISK_TIERS = frozenset({"safe", "monitor", "ask", "block"})


def _effective_call_tier(
    descriptor: SupervisorToolDescriptor,
    arguments: dict[str, Any] | None,
) -> str:
    """Static descriptor tier, refined by ``risk_tier_for_args`` when present.

    ``youtube_music`` / ``spotify`` are statically ``monitor`` because play
    mutates. ``now_playing`` is ``safe``. Live 2026-08-19 17:46: the shape
    guard used only the static tier and refused "Welches Lied".  # i18n-allow
    """
    static = str(getattr(descriptor, "risk_tier", "monitor") or "monitor")
    if static not in _CALL_RISK_TIERS:
        static = "monitor"
    hook = getattr(descriptor, "risk_tier_for_args", None)
    if not callable(hook):
        return static
    try:
        dynamic = hook(arguments if isinstance(arguments, dict) else {})
    except Exception:  # noqa: BLE001 — a broken hook must not brick the call
        return static
    if dynamic in _CALL_RISK_TIERS:
        return str(dynamic)
    return static


def _is_music_plugin(name: str) -> bool:
    try:
        from jarvis.core.music_constants import MUSIC_PLUGIN_IDS
    except Exception:  # noqa: BLE001 — a missing constant must not brick the guard
        return False
    return name in MUSIC_PLUGIN_IDS


def _skill_match_index() -> Any | None:
    """The deterministic skill index, or ``None`` when unavailable.

    Delegates to the one implementation in ``jarvis.skills.skill_context``
    so this guard and the session's routing decision cannot drift apart —
    a copy of it lived here and said "mirrors" in its own docstring, which
    is the shape of a future disagreement, not a safeguard against one.
    """
    try:
        from jarvis.skills.skill_context import current_match_index

        return current_match_index()
    except Exception:  # noqa: BLE001 — planning keeps its static fallbacks
        return None


def _turn_shape_refusal(
    descriptor: SupervisorToolDescriptor,
    name: str,
    user_text: str,
    arguments: dict[str, Any] | None = None,
) -> str:
    """Refuse a native call whose tool the user's words cannot have asked for.

    The live model holds the whole catalog (ADR-0035) and, on a garbled or
    conversational turn, picks SOMETHING: in the first hybrid session
    (2026-08-19 12:50) "Was genau, wie ist mein X-Shield?" called a
    freshly installed plugin's balance tool, and "Was oh mein Gott, wieso lag
    es so rum?" called app-restart — an ``ask``-tier tool whose two-turn
    confirmation the very next utterance could have answered. Prompt
    compliance is not a correctness boundary (BUG-047 class rule); the
    boundary is here, at execute time, on the user's own words:  # i18n-allow: quoted utterances

    * a namespaced plugin/MCP tool runs only when the turn names that plugin
      (its id, its usage-card keywords, or a noun of its own tools) — the same
      relevance rule the router brain applies before it even sees them;
    * an ``ask``-tier tool runs only on a turn that ORDERS an action (an
      action verb); a question, an exclamation, or a greeting never does;
    * any other non-``safe`` tool runs only on a turn that orders, tasks, or
      asks for the user's world (a planner reason) — a turn with none of
      those ("Was geht ab?", "Okay.") gets no side effect.  # i18n-allow: quoted utterances

    The effective tier is per-call: ``risk_tier_for_args`` can mark a read
    such as ``now_playing`` ``safe`` even when the tool is statically
    ``monitor``. A music *write* (play/pause/skip) still needs an order —
    "Welches Lied" must not restart the player.  # i18n-allow

    Read-only ``safe`` tools are never refused here. An empty utterance
    (non-conversational launch routes) fails open. Returns the model-facing
    refusal text, or ``""`` to allow.
    """
    text = str(user_text or "").strip()
    if not text:
        return ""
    plugin_id = _plugin_id_of(name)
    if plugin_id:
        try:
            from jarvis.marketplace.plugin_relevance import plugin_is_relevant

            relevant = plugin_is_relevant(text, plugin_id, [descriptor])
        except Exception:  # noqa: BLE001 — a relevance fault must not brick the plugin
            relevant = True
        if not relevant:
            return (
                f"{name} was not run: the user did not mention the {plugin_id} "
                "service, so its functions are not what this request asks for. "
                "Answer the user's actual request; call a plugin function only "
                "when the user names that service or clearly asks for it."
            )
    tier = _effective_call_tier(descriptor, arguments)
    if tier == "safe":
        return ""
    orders = is_action_order(text)
    if tier in {"ask", "block"} or _is_side_effect_tool(descriptor):
        if orders:
            return ""
        return (
            f"{name} was not run: the user's words do not order an action — "
            "they are a question, a remark, or a greeting. Never start a "
            "consequential action from such a turn. Answer the user directly, "
            "or ask what they want done."
        )
    if orders or is_assistant_tasking(text):
        return ""
    if _is_music_plugin(name):
        return (
            f"{name} was not run: the user's words do not order an action — "
            "they are a question, a remark, or a greeting. Never start a "
            "consequential action from such a turn. Answer the user directly, "
            "or ask what they want done."
        )
    try:
        # The skill index travels ONLY for ``run-skill``. Without it the
        # planner's vocabulary knows just the literal word "skill", so an
        # utterance that NAMES an installed skill scores no reason and this
        # guard refused ``run-skill`` on a turn the router had already routed
        # with ``reasons=skill`` moments earlier (live 2026-08-20,
        # "Morgenroutine"). One planner asked two ways is a coin flip.
        #
        # Scoped deliberately: an installed skill scoring FIRE says something
        # about whether a SKILL applies, and nothing about whether some
        # unrelated monitor-tier plugin call is warranted. Handing the index to
        # every tool would let "ich brauche Konzentration" — a remark, not an
        # order — buy a side effect for any tool the live model happened to
        # pick, which is the exact thing this gate exists to refuse.
        skill_index = _skill_match_index() if name == "run-skill" else None
        has_world_reason = bool(plan_turn(text, skill_index=skill_index).reasons)
    except Exception:  # noqa: BLE001 — the planner must not brick a tool call
        has_world_reason = True
    if has_world_reason:
        return ""
    return (
        f"{name} was not run: nothing in the user's words asks for it — no "
        "action, no request about their data or services. Answer the user "
        "directly; if you are unsure what they want, ask."
    )


def _declaration_size(declaration: dict[str, Any]) -> int:
    try:
        return len(json.dumps(declaration, ensure_ascii=False, default=str))
    except Exception:  # noqa: BLE001 - an unserializable schema counts as large
        return _MAX_DESCRIPTION_CHARS


def _drop_rank(name: str) -> int:
    """Lower rank = dropped earlier (ADR-0035 §4 family order)."""
    for rank, (_family, pattern) in enumerate(_BUDGET_DROP_FAMILIES):
        if pattern.search(name):
            return rank
    return len(_BUDGET_DROP_FAMILIES)


def _apply_declaration_budget(
    rendered: list[tuple[str, dict[str, Any]]],
    budget_chars: int,
) -> tuple[list[tuple[str, dict[str, Any]]], list[str]]:
    """Trim ``rendered`` (name, declaration) pairs to ``budget_chars``.

    Deterministic: the lowest-priority family goes first, the longest
    declaration first inside it, ties by name. Returns (kept in the original
    order, dropped names in drop order). ``budget_chars <= 0`` keeps all.
    """
    if budget_chars <= 0:
        return list(rendered), []
    sizes = {name: _declaration_size(decl) for name, decl in rendered}
    total = sum(sizes.values())
    if total <= budget_chars:
        return list(rendered), []
    drop_order = sorted(
        (name for name, _decl in rendered),
        key=lambda name: (_drop_rank(name), -sizes[name], name),
    )
    dropped: list[str] = []
    for name in drop_order:
        if total <= budget_chars:
            break
        dropped.append(name)
        total -= sizes[name]
    dropped_set = set(dropped)
    kept = [(name, decl) for name, decl in rendered if name not in dropped_set]
    return kept, dropped


@dataclass(slots=True)
class _PendingConfirmation:
    trace_id: UUID
    tool_name: str
    confirmed: bool = False


class RealtimeToolBridge:
    """Expose the live router tools and execute only through ``ToolExecutor``."""

    def __init__(
        self,
        *,
        tools: dict[str, Any] | None = None,
        executor: Any = None,
        gateway: SupervisorToolGateway | None = None,
        language: str,
        tools_source: Any = None,
        excluded_tool_names: frozenset[str] | set[str] | None = None,
        compact: bool = False,
        declaration_budget_chars: int = 0,
    ) -> None:
        """``excluded_tool_names`` are never declared AND never executed by
        this bridge (ADR-0035: the computer-use vehicles stay delegate-only).
        ``compact`` renders descriptions for the live model (ADR-0035 §4);
        ``declaration_budget_chars`` (0 = unbounded) trims the set in the
        documented priority order and records the dropped names."""
        self._tools = dict(tools or {})
        self._tools_source = tools_source
        self._executor = executor
        self._gateway = gateway
        self._language = language
        self._excluded_tool_names: frozenset[str] = frozenset(
            str(name) for name in (excluded_tool_names or ())
        )
        self._compact = bool(compact)
        self._declaration_budget_chars = max(0, int(declaration_budget_chars or 0))
        self._dropped_names: tuple[str, ...] = ()
        self._descriptors: dict[str, SupervisorToolDescriptor] = (
            self._read_descriptors()
        )
        self._wire_to_name: dict[str, str] = {}
        self._declarations: tuple[dict[str, Any], ...] = self._build_declarations()
        self._pending: _PendingConfirmation | None = None
        self._vetoed_tool = ""
        self._last_user_text = ""

    @classmethod
    def from_supervisor_gateway(
        cls,
        *,
        language: str,
        excluded_tool_names: frozenset[str] | set[str] | None = None,
        compact: bool = False,
        declaration_budget_chars: int = 0,
    ) -> RealtimeToolBridge | None:
        """Build from the safety gateway without requiring a classic brain call."""
        gateway = runtime_refs.get_supervisor_tool_gateway()
        if gateway is None or not gateway.catalog():
            return None
        return cls(
            gateway=gateway,
            language=language,
            excluded_tool_names=excluded_tool_names,
            compact=compact,
            declaration_budget_chars=declaration_budget_chars,
        )

    @classmethod
    def from_brain(cls, _brain: Any, *, language: str) -> RealtimeToolBridge | None:
        """Compatibility wrapper for callers using the former constructor."""
        return cls.from_supervisor_gateway(language=language)

    def _read_descriptors(
        self,
        tools_override: dict[str, Any] | None = None,
    ) -> dict[str, SupervisorToolDescriptor]:
        descriptors = self._read_descriptors_unfiltered(tools_override)
        if not self._excluded_tool_names:
            return descriptors
        return {
            name: item
            for name, item in descriptors.items()
            if name not in self._excluded_tool_names
        }

    def _read_descriptors_unfiltered(
        self,
        tools_override: dict[str, Any] | None = None,
    ) -> dict[str, SupervisorToolDescriptor]:
        if self._gateway is not None:
            try:
                return {item.name: item for item in self._gateway.catalog()}
            except Exception:  # noqa: BLE001 - a catalog refresh degrades safely
                return {}

        descriptors: dict[str, SupervisorToolDescriptor] = {}
        source_tools = self._tools if tools_override is None else tools_override
        for name, tool in source_tools.items():
            schema = getattr(tool, "schema", None)
            if not isinstance(schema, dict):
                continue
            raw_tier = str(getattr(tool, "risk_tier", "monitor"))
            risk_tier = cast(
                RiskTier,
                raw_tier if raw_tier in {"safe", "monitor", "ask", "block"}
                else "monitor",
            )
            hook = getattr(tool, "risk_tier_for_args", None)
            impact = getattr(tool, "describe_args", None)
            descriptors[str(name)] = SupervisorToolDescriptor(
                name=str(name),
                description=str(getattr(tool, "description", "")),
                input_schema=schema,
                risk_tier=risk_tier,
                is_action_tool=bool(getattr(tool, "is_action_tool", False)),
                risk_tier_for_args=hook if callable(hook) else None,
                describe_args=impact if callable(impact) else None,
            )
        return descriptors

    def _render_declaration(
        self, name: str, descriptor: SupervisorToolDescriptor
    ) -> dict[str, Any]:
        if not self._compact:
            return {
                "name": name,
                "description": descriptor.description[:_MAX_DESCRIPTION_CHARS],
                "parameters": descriptor.input_schema,
            }
        return {
            "name": name,
            "description": _compact_text(
                descriptor.description, COMPACT_DESCRIPTION_CHARS
            ),
            "parameters": _compact_schema(
                descriptor.input_schema, COMPACT_PARAMETER_DESCRIPTION_CHARS
            ),
        }

    def _build_declarations(self) -> tuple[dict[str, Any], ...]:
        self._wire_to_name.clear()
        rendered: list[tuple[str, dict[str, Any]]] = []
        for name, descriptor in sorted(self._descriptors.items()):
            wire = _wire_name(str(name))
            if wire in self._wire_to_name:
                continue
            self._wire_to_name[wire] = str(name)
            rendered.append((str(name), self._render_declaration(wire, descriptor)))
        # Second pass: hyphenated catalog names also answer to the un-hashed
        # underscore alias the live model actually sends. Never overwrite a
        # real tool that already owns that identifier.
        for name in sorted(self._descriptors):
            alias = _plain_wire_alias(str(name))
            if alias and alias not in self._wire_to_name:
                self._wire_to_name[alias] = str(name)
        kept, dropped = _apply_declaration_budget(
            rendered, self._declaration_budget_chars
        )
        self._dropped_names = tuple(dropped)
        if dropped:
            # Names are logged in full (AP-30): a silently trimmed catalog
            # looks exactly like a complete one. The dropped tools remain
            # reachable through jarvis_action.
            log.warning(
                "realtime tool bridge: %d declaration(s) over the %d-char "
                "(~%d-token) budget were dropped from the native set and stay "
                "reachable through jarvis_action: %s",
                len(dropped),
                self._declaration_budget_chars,
                self._declaration_budget_chars // CHARS_PER_TOKEN,
                ", ".join(dropped),
            )
            for name in dropped:
                self._wire_to_name.pop(_wire_name(name), None)
        return tuple(declaration for _name, declaration in kept)

    @property
    def declarations(self) -> tuple[dict[str, Any], ...]:
        return self._declarations

    @property
    def dropped_names(self) -> tuple[str, ...]:
        """Catalog tools NOT declared natively under the declaration budget."""
        return self._dropped_names

    @property
    def excluded_tool_names(self) -> frozenset[str]:
        return self._excluded_tool_names

    @property
    def declaration_chars(self) -> int:
        """Serialized size of the declared set (the budget's own unit)."""
        return sum(_declaration_size(item) for item in self._declarations)

    def set_language(self, language: str) -> None:
        self._language = language

    def refresh_from_source(self) -> bool:
        """Refresh a live BrainManager tool replacement safely.

        Returns ``True`` only when the provider-facing declarations changed.
        A tool awaiting voice confirmation is retained until that confirmation
        resolves, so a concurrent registry refresh cannot strand the pending
        ``ToolExecutor`` action.
        """
        if self._gateway is not None:
            refreshed_descriptors = self._read_descriptors()
            refreshed_tools: dict[str, Any] | None = None
        else:
            source = self._tools_source
            if not callable(source):
                return False
            current = source()
            if not isinstance(current, dict):
                return False
            try:
                refreshed_tools = dict(current)
            except RuntimeError:
                return False
            refreshed_descriptors = self._read_descriptors(refreshed_tools)
        pending = self._pending
        if (
            pending is not None
            and pending.tool_name not in refreshed_descriptors
            and pending.tool_name in self._descriptors
        ):
            refreshed_descriptors[pending.tool_name] = self._descriptors[
                pending.tool_name
            ]
            if (
                refreshed_tools is not None
                and pending.tool_name not in refreshed_tools
                and pending.tool_name in self._tools
            ):
                refreshed_tools[pending.tool_name] = self._tools[pending.tool_name]
        previous_declarations = self._declarations
        if refreshed_tools is not None:
            self._tools = refreshed_tools
        self._descriptors = refreshed_descriptors
        self._declarations = self._build_declarations()
        return self._declarations != previous_declarations

    async def handle_user_transcript(self, text: str) -> None:
        self._last_user_text = text
        self._vetoed_tool = ""
        pending = self._pending
        if pending is None:
            return
        verdict = classify_response(text, language=self._language)
        if verdict == "confirm":
            pending.confirmed = True
        elif verdict == "veto":
            await self._cancel_pending(pending.trace_id)
            self._vetoed_tool = pending.tool_name
            self._pending = None

    async def execute(
        self,
        *,
        wire_name: str,
        arguments: dict[str, Any],
        trace_id: UUID | None = None,
    ) -> tuple[str, dict[str, Any]]:
        name = self._declared_name_for_wire(wire_name)
        name, arguments = self._maybe_reroute_music(name, arguments)
        descriptor = self._descriptors.get(name)
        if descriptor is None:
            await self._publish_denied(wire_name, "unknown realtime tool")
            return wire_name, {
                "success": False,
                "error": "Tool is not available in this session.",
            }
        if self._vetoed_tool == name:
            return name, {
                "success": False,
                "blocked": True,
                "error": "The user declined this action. Do not ask again in this turn.",
            }
        validation_error = self._validate_arguments(descriptor, arguments)
        if validation_error:
            await self._publish_denied(name, validation_error)
            return name, {"success": False, "error": validation_error}

        guard_error = await self._guard(descriptor, name, arguments)
        if guard_error:
            # ``blocked`` separates a POLICY decision from a broken tool. The
            # error text here is an instruction addressed to the model ("Answer
            # the user's turn directly yourself, right now, inline") — the voice
            # layer must never read it out, and must never let it become the
            # spoken outcome of the turn. Live forensic 2026-08-20 13:41:24: a
            # spawn_worker block was the LAST result of a two-tool turn, so the
            # readback took its unspeakable text, fell through to the stock
            # "that didn't work", and the turn ended with the calendar reason
            # from the FIRST tool never spoken. See
            # ``RealtimeVoiceSession._direct_tool_fallback_text``.
            return name, {"success": False, "blocked": True, "error": guard_error}

        pending = self._pending
        if pending is not None and pending.tool_name == name:
            if not pending.confirmed:
                return name, {
                    "success": False,
                    "confirmation_required": True,
                    "message": format_tool_confirmation(
                        name, language=self._language
                    ),
                }
            result = await self._execute_confirmed(pending.trace_id)
            self._pending = None
            return name, _bounded_result(
                bool(getattr(result, "success", False)),
                getattr(result, "output", None),
                getattr(result, "error", None),
            )

        trace_id = trace_id or uuid4()
        result = await self._execute_tool(name, arguments, trace_id)
        if (
            getattr(result, "error", None) == VOICE_CONFIRM_SENTINEL
            and isinstance(getattr(result, "output", None), dict)
        ):
            self._pending = _PendingConfirmation(trace_id=trace_id, tool_name=name)
            impact = result.output.get("impact")
            if not isinstance(impact, dict):
                impact = {}
            return name, {
                "success": False,
                "confirmation_required": True,
                "message": format_tool_confirmation(
                    name,
                    language=self._language,
                    impact_level=impact.get("level"),
                    impact_commands=impact.get("commands"),
                ),
                "instruction": (
                    "Ask the user this question. Call the same function again only "
                    "after a clear affirmative answer."
                ),
            }
        return name, _bounded_result(
            bool(getattr(result, "success", False)),
            getattr(result, "output", None),
            getattr(result, "error", None),
        )

    async def _execute_tool(
        self,
        name: str,
        arguments: dict[str, Any],
        trace_id: UUID,
    ) -> Any:
        if self._gateway is not None:
            return await self._gateway.execute(
                name,
                arguments,
                SupervisorToolRequest(
                    trace_id=trace_id,
                    origin="realtime",
                    user_utterance=self._last_user_text,
                    rationale="Realtime model requested an available Jarvis tool.",
                    config_snapshot={
                        "output_language": self._language,
                        "voice_confirm": True,
                    },
                ),
            )
        tool = self._tools[name]
        return await self._executor.execute(
            tool,
            arguments,
            user_utterance=self._last_user_text,
            config_snapshot={
                "output_language": self._language,
                "voice_confirm": True,
            },
            trace_id=trace_id,
            rationale="Realtime model requested an available Jarvis tool.",
        )

    async def _execute_confirmed(self, trace_id: UUID) -> Any:
        if self._gateway is not None:
            return await self._gateway.execute_confirmed(
                trace_id,
                SupervisorToolRequest(
                    trace_id=trace_id,
                    origin="realtime",
                    user_utterance=self._last_user_text,
                    config_snapshot={"output_language": self._language},
                ),
            )
        return await self._executor.execute_confirmed(
            trace_id,
            user_utterance=self._last_user_text,
            config_snapshot={"output_language": self._language},
        )

    async def _cancel_pending(self, trace_id: UUID) -> bool:
        if self._gateway is not None:
            return await self._gateway.cancel_pending(trace_id)
        return bool(await self._executor.cancel_pending(trace_id))

    def _declared_name_for_wire(self, wire_name: str) -> str:
        """Map a provider function-call name onto a catalog tool name.

        Tries the wire name as sent, then a stripped tool-set prefix
        (``default:run_shell``), then the hashed form used for names
        that are not legal identifiers.
        """
        if not wire_name:
            return ""
        mapped = self._wire_to_name.get(wire_name, "")
        if mapped:
            return mapped
        canonical = canonical_tool_wire_name(wire_name)
        mapped = self._wire_to_name.get(canonical, "")
        if mapped:
            return mapped
        return self._wire_to_name.get(_wire_name(canonical), "")

    def _maybe_reroute_music(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[str, dict[str, Any]]:
        """Send an unnamed music call to the preferred/only connected service.

        Hybrid live models hold both music tools and pick by description
        (2026-08-19: preference YouTube Music, Spotify not connected, the
        model still called ``spotify``). Descriptions are a hint; this is
        the correctness boundary. A named service still wins. Never raises.
        """
        try:
            from jarvis.core.music_constants import MUSIC_PLUGIN_IDS
            from jarvis.core.music_service import (
                adapt_music_arguments,
                reroute_music_tool,
            )
        except Exception:  # noqa: BLE001 — a routing nicety must never break a turn
            return name, arguments
        if name not in MUSIC_PLUGIN_IDS:
            return name, arguments
        try:
            target = reroute_music_tool(name, self._last_user_text)
        except Exception as exc:  # noqa: BLE001
            log.debug("realtime music tool reroute skipped: %s", exc)
            return name, arguments
        if target == name or target not in self._descriptors:
            return name, arguments
        log.info(
            "realtime music tool rerouted %s -> %s",
            name,
            target,
        )
        args = arguments if isinstance(arguments, dict) else {}
        try:
            adapted = adapt_music_arguments(
                self._last_user_text, source=name, target=target, args=args
            )
        except Exception as exc:  # noqa: BLE001
            log.debug("realtime music arg adapt skipped: %s", exc)
            adapted = args
        return target, adapted

    def _validate_arguments(
        self,
        descriptor: SupervisorToolDescriptor,
        arguments: Any,
    ) -> str:
        if not isinstance(arguments, dict):
            return "Tool arguments must be a JSON object."
        try:
            size = len(json.dumps(arguments, ensure_ascii=False, default=str))
        except Exception:  # noqa: BLE001
            return "Tool arguments are not JSON serializable."
        if size > _MAX_ARGUMENT_CHARS:
            return f"Tool arguments exceed the {_MAX_ARGUMENT_CHARS}-character limit."
        schema = descriptor.input_schema
        required = schema.get("required", ())
        missing = [key for key in required if key not in arguments]
        if missing:
            return f"Missing required tool arguments: {', '.join(map(str, missing))}."
        return ""

    async def _guard(
        self,
        descriptor: SupervisorToolDescriptor,
        name: str,
        arguments: dict[str, Any],
    ) -> str:
        user_text = self._last_user_text
        blocked, reason = _is_stt_hallucinated(name, arguments)
        if blocked:
            message = f"Suspected speech-recognition argument error: {reason}"
        elif _is_instructional_question(user_text) and _is_side_effect_tool(descriptor):
            message = "The user asked for instructions; the side-effect tool was not run."
        elif _is_self_identification(user_text) and _is_side_effect_tool(descriptor):
            message = "The user was introducing themselves; the side-effect tool was not run."
        elif name == "spawn_worker" and _is_meta_debug_intent(user_text):
            message = "A meta/debug request must be answered directly, not delegated."
        elif name in SPAWN_VEHICLE_TOOL_NAMES and not llm_spawn_allowed(user_text):
            # Explicit-delegation gate (maintainer mandate 2026-07-18): the
            # realtime model may start a background agent ONLY when the user's
            # spoken turn asks for one (or confirms an offer one turn later).
            # Deterministic — prompt-side discouragement failed repeatedly.
            # See jarvis/brain/spawn_gate.py.
            message = spawn_blocked_feedback(user_text)
        elif name in CU_VEHICLE_TOOL_NAMES and not llm_computer_use_allowed(
            user_text
        ):
            # Explicit-desktop gate (live incident 2026-07-21 11:36): a pure
            # knowledge question must never be answered by driving the user's
            # browser. computer_use runs ONLY when the spoken turn asks for an
            # on-screen action or a desktop episode is already in progress.
            # See jarvis/brain/cu_gate.py.
            message = CU_BLOCKED_MODEL_FEEDBACK
        elif _should_block_action_as_research(
            descriptor,
            name,
            user_text,
            None,
            "",
        ):
            message = "This sounds like research, not an action on a connected system."
        elif self._answers_a_pending_question(name, user_text):
            # The second half of an order the model already asked about
            # (a two-turn confirmation, a confirmed delegation offer) is not
            # a fresh turn to shape-check: "Yes." orders nothing by itself.
            return ""
        else:
            message = _turn_shape_refusal(descriptor, name, user_text, arguments)
            if not message:
                return ""
        await self._publish_denied(name, message)
        return message

    def _answers_a_pending_question(self, name: str, user_text: str) -> bool:
        pending = self._pending
        if pending is not None and pending.tool_name == name:
            return True
        # A SHORT affirmative ("Ja.", "Yes, go ahead.") answers an offer the
        # model made in its own words (the confirmed-delegation unlock). Long
        # sentences are not confirmations even when they contain an
        # affirmative token — "Was genau, wie ist mein X-Shield?" carries
        # "genau" and is a question (live 2026-08-19).  # i18n-allow: quoted utterance
        if len(str(user_text or "").split()) > 4:
            return False
        try:
            return classify_response(user_text, language=self._language) == "confirm"
        except Exception:  # noqa: BLE001 — the classifier must not brick a call
            return False

    async def _publish_denied(self, name: str, reason: str) -> None:
        publisher = getattr(
            self._gateway if self._gateway is not None else self._executor,
            "publish_guard_denied",
            None,
        )
        if callable(publisher):
            try:
                await publisher(name, reason, trace_id=uuid4())
            except Exception:  # noqa: BLE001, S110 — observability cannot break safety
                pass

    async def close(self) -> None:
        if self._pending is not None:
            await self._cancel_pending(self._pending.trace_id)
            self._pending = None


__all__ = ["RealtimeToolBridge"]
