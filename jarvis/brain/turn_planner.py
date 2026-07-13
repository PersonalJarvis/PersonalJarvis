"""Shared local turn planner for Pipeline and Realtime.

The realtime transport must not maintain a second, narrower vocabulary for
deciding whether Jarvis needs private, local, current, or connected evidence.
This module provides one deterministic decision that is safe to call on the
voice hot path: no model call, disk access, or network access is performed.
"""

from __future__ import annotations

import re
import unicodedata
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class TurnPath(StrEnum):
    """Execution surface selected for one user turn."""

    NATIVE_REALTIME = "native_realtime"
    ORCHESTRATOR = "orchestrator"


class TurnReason(StrEnum):
    """Stable reasons why a turn needs the Jarvis orchestrator."""

    ACTION = "action"
    CAPABILITY = "capability"
    CONNECTED_DATA = "connected_data"
    CURRENT_DATA = "current_data"
    LOCAL_STATE = "local_state"
    MISSION = "mission"
    PRIVATE_DATA = "private_data"
    SKILL = "skill"
    UNCERTAIN = "uncertain"


@dataclass(frozen=True)
class TurnPlan:
    """Provider-neutral plan consumed by Pipeline and Realtime."""

    path: TurnPath
    reasons: frozenset[TurnReason] = frozenset()
    required_capabilities: tuple[str, ...] = ()
    requires_evidence: bool = False

    @property
    def requires_orchestrator(self) -> bool:
        return self.path is TurnPath.ORCHESTRATOR


_LOOKUP_SHAPE_RE = re.compile(
    r"\b(?:what|when|where|which|who|how many|show|read|list|find|lookup|"
    r"check|summarize|search|do i have|is there|are there|"
    r"was|wann|wo|welch\w*|wer|wie viele|zeig\w*|lies|lese|list\w*|"
    r"find\w*|such\w*|pruef\w*|fass\w*|habe ich|hab ich|gibt es|"
    r"que|cuando|donde|cual\w*|quien|cuantos|muestra|lee|lista|"
    r"busca|revisa|resume|tengo|hay)\b"  # i18n-allow: multilingual speech-input matching data
)
_DEFINITION_RE = re.compile(
    r"\b(?:what is (?:a|an|the)?|what are|what does .{0,40} mean|explain|"
    r"was ist (?:ein|eine|der|die|das)?|"  # i18n-allow: speech input
    r"was sind|was bedeutet|erklaer\w*|"  # i18n-allow: speech input
    r"que es|que son|explica)\b"  # i18n-allow: multilingual speech-input matching data
)
_INSTRUCTIONAL_RE = re.compile(
    r"\b(?:how (?:do|can|would) (?:i|you)|how to|"
    r"wie (?:kann|koennte|wuerde) (?:ich|man)|"  # i18n-allow: speech input
    r"como (?:puedo|se puede)|como hacer)\b"  # i18n-allow: multilingual speech-input matching data
)
_OWNERSHIP_RE = re.compile(
    r"\b(?:my|mine|our|about me|remember me|"
    r"mein\w*|unser\w*|mir|ueber mich|uber mich|erinner\w* mich|"  # i18n-allow: speech input
    r"mi|mis|mio|nuestr\w*|sobre mi|recuerd\w* de mi)\b"  # i18n-allow: speech input
)
_CURRENT_RE = re.compile(
    r"\b(?:current|currently|latest|today|tonight|tomorrow|now|recent|"
    r"news|weather|status|available|online|"
    r"aktuell\w*|neueste\w*|heute|morgen|jetzt|kuerzlich|nachrichten|"
    r"wetter|status|verfuegbar|online|"  # i18n-allow: speech input
    r"actual\w*|ultimo\w*|hoy|manana|ahora|reciente\w*|noticias|"
    r"clima|tiempo|estado|disponible)\b"  # i18n-allow: multilingual speech-input matching data
)
_LOCAL_STATE_RE = re.compile(
    r"\b(?:wiki|mcp\w*|cli\w*|tool\w*|plugin\w*|connector\w*|"
    r"integration\w*|setting\w*|configuration\w*|api[\s-]?key\w*|"
    r"jarvis|installed\w*|connected\w*|capabilit\w*|activity history|"
    r"werkzeug\w*|einstellung\w*|konfiguration\w*|"  # i18n-allow: speech input
    r"installiert\w*|verbunden\w*|faehigkeit\w*|"  # i18n-allow: speech input
    r"aktivitaetsverlauf|"  # i18n-allow: speech input
    r"herramient\w*|ajuste\w*|configuracion\w*|integracion\w*|"
    r"instalad\w*|conectad\w*|capacidad\w*)\b"  # i18n-allow: speech input
)
_CONNECTED_DOMAIN_RE = re.compile(
    r"\b(?:gmail|email|e-mail|mailbox|inbox|calendar|sap|salesforce|"
    r"github|gitlab|drive|notion|slack|discord|telegram|whatsapp|"
    r"repository|pull request|deployment|cloud billing|contact\w*|"
    r"postfach|posteingang|kalender|termin\w*|kontakt\w*|abrechnung\w*|"
    r"correo|bandeja|calendario|cita\w*|contacto\w*)\b"  # i18n-allow: speech input
)
_MISSION_RE = re.compile(
    r"\b(?:jarvis[\s-]?agent\w*|agent\w*|mission\w*|worker\w*|"
    r"background task\w*|subagent\w*|sub-agent\w*|"
    r"hintergrund\w*|agente\w*|mision\w*)\b"  # i18n-allow: multilingual speech-input matching data
)
_SKILL_RE = re.compile(
    r"\b(?:skill\w*|macro\w*|faehigkeit\w*|makro\w*|habilidad\w*)\b"  # i18n-allow: speech input
)
# Over-matching costs only latency (the orchestrator still answers
# conversationally); under-matching loses the user's action — so common
# assistant verbs (media, reminders/notes, settings switches, calendar,
# on/off) are included even where a noun reading exists ("playlist",
# "agenda", "activity"). Guarded stems exclude the frequent non-action
# words ("merkwürdig", "tragisch", "legal").  # i18n-allow: names the excluded German tokens
_ACTION_FALLBACK_RE = re.compile(
    r"\b(?:open|close|start|stop|create|write|save|add|change|set|restart|"
    r"install|connect|delete|move|send|run|build|research|call|click|type|"
    r"upload|download|book|buy|post|reply|switch\w*|turn\w*|play\w*|"
    r"paus\w*|resume|remember|notes?|schedule|remind\w*|cancel\w*|"
    r"update\w*|rename|enable|disable|mute|record|activ\w*|deactiv\w*|"
    r"oeffn\w*|schliess\w*|start\w*|stopp\w*|erstell\w*|schreib\w*|"
    r"speicher\w*|aender\w*|installier\w*|verbind\w*|loesch\w*|"
    r"verschieb\w*|schick\w*|send\w*|fuehr\w*|bau\w*|ruf\w*|klick\w*|"
    r"tipp\w*|buch\w*|kauf\w*|antwort\w*|wechsel\w*|wechsl\w*|schalt\w*|"
    r"stell\w*|spiel\w*|merk(?!wuerdig)\w*|notier\w*|trag(?!isch|oedi)\w*|"
    r"leg(?:e|st|t|en)?\b|setz\w*|pausier\w*|aktivier\w*|deaktivier\w*|"
    r"erinner\w*|dreh\w*|"
    r"abre\w*|cierra\w*|inicia\w*|crea\w*|escrib\w*|guarda\w*|"
    r"cambia\w*|instala\w*|conecta\w*|elimina\w*|envia\w*|"
    r"ejecuta\w*|llama\w*|haz\w*|recuerd\w*|anot\w*|apunt\w*|pon\w*|"
    r"reproduc\w*|reanud\w*|apag\w*|enciend\w*|agend\w*|"
    r"reserv\w*)\b"  # i18n-allow: multilingual speech-input matching data
)
_FOLLOW_UP_REFERENCE_RE = re.compile(
    r"\b(?:that|there|those|them|inside|what else|"
    r"da|darin|drin|dort|dazu|davon|darueber|was noch|"  # i18n-allow
    r"eso|esto|ahi|alli|dentro|que mas)\b|"  # i18n-allow
    r"\b(?:what\s+does\s+it|what(?:'s|\s+is)\s+in\s+it|in\s+it)\b"
)
_CONTEXT_MAX_CHARS = 2_000


# German umlaut characters must become their transliterated digraphs
# (a-umlaut -> ae, o-umlaut -> oe, u-umlaut -> ue; casefold already yields
# "ss" for the sharp s) because every German vocabulary entry above is
# written in that form ("loesch", "aender", "fuehr"). A plain NFKD
# combining-strip would produce the OTHER ascii form ("losche", "andere"),
# which silently disables the entire German action/lookup vocabulary
# against real STT output.
_UMLAUT_TRANSLITERATION = str.maketrans(
    {"ä": "ae", "ö": "oe", "ü": "ue"}  # i18n-allow: umlaut mapping data
)


def _normalize(text: str) -> str:
    folded = str(text or "").casefold().translate(_UMLAUT_TRANSLITERATION)
    decomposed = unicodedata.normalize("NFKD", folded)
    return "".join(ch for ch in decomposed if not unicodedata.combining(ch))


def _tokens_from_capability(capability: Any) -> set[str]:
    tokens: set[str] = set()
    for value in (
        getattr(capability, "id", ""),
        *tuple(getattr(capability, "objects", ()) or ()),
    ):
        normalized = _normalize(str(value)).replace("_", " ").replace("/", " ")
        for token in re.split(r"[^a-z0-9-]+", normalized):
            if len(token) >= 3:
                tokens.add(token)
    return tokens


def _matched_capabilities(
    text: str,
    *,
    capability_registry: Any | None,
    tool_names: Iterable[str],
) -> tuple[str, ...]:
    matched: set[str] = set()
    capabilities: Sequence[Any] = ()
    if capability_registry is not None:
        try:
            resolved = capability_registry.resolve_intent(text)
            if resolved is not None:
                matched.add(str(getattr(resolved, "id", "")))
            capabilities = capability_registry.all()
        except Exception:  # noqa: BLE001 - planner must fail safely
            capabilities = ()

    normalized = _normalize(text)
    if _LOOKUP_SHAPE_RE.search(normalized):
        for capability in capabilities:
            tokens = _tokens_from_capability(capability)
            if any(re.search(r"\b" + re.escape(token) + r"\b", normalized) for token in tokens):
                matched.add(str(getattr(capability, "id", "")))
        for name in tool_names:
            normalized_name = _normalize(name).replace("_", " ").replace("/", " ")
            tokens = [
                token
                for token in re.split(r"[^a-z0-9-]+", normalized_name)
                if len(token) >= 3
            ]
            if any(
                re.search(r"\b" + re.escape(token) + r"\b", normalized)
                for token in tokens
            ):
                matched.add(str(name))
    return tuple(sorted(item for item in matched if item))


def plan_turn(
    text: str,
    *,
    capability_registry: Any | None = None,
    tool_names: Iterable[str] = (),
    evidence_domains: Mapping[str, Sequence[str]] | None = None,
) -> TurnPlan:
    """Return the conservative shared execution plan for ``text``.

    Uncertainty is resolved toward the orchestrator because it can still
    answer conversationally, while a native realtime model cannot recover
    private or connected evidence it never received.
    """
    normalized = _normalize(text).strip()
    if not normalized:
        return TurnPlan(path=TurnPath.NATIVE_REALTIME)

    reasons: set[TurnReason] = set()
    definition = bool(_DEFINITION_RE.search(normalized))
    instructional = bool(_INSTRUCTIONAL_RE.search(normalized))
    required = () if definition or instructional else _matched_capabilities(
        text,
        capability_registry=capability_registry,
        tool_names=tool_names,
    )
    if required:
        reasons.add(TurnReason.CAPABILITY)

    action_intent = bool(_ACTION_FALLBACK_RE.search(normalized))
    if capability_registry is not None:
        try:
            action_intent = action_intent or bool(
                capability_registry.has_action_intent(text)
            )
        except Exception:  # noqa: BLE001,S110 - local fallback remains available
            pass

    lookup = bool(_LOOKUP_SHAPE_RE.search(normalized))
    private = bool(_OWNERSHIP_RE.search(normalized))

    if action_intent and not instructional:
        reasons.add(TurnReason.ACTION)
    if private and (lookup or action_intent):
        reasons.add(TurnReason.PRIVATE_DATA)
    if _LOCAL_STATE_RE.search(normalized) and not definition:
        reasons.add(TurnReason.LOCAL_STATE)
    if (
        _CONNECTED_DOMAIN_RE.search(normalized)
        and not definition
        and (lookup or action_intent or private)
    ):
        reasons.add(TurnReason.CONNECTED_DATA)
    if _CURRENT_RE.search(normalized) and (lookup or normalized.endswith("?")):
        reasons.add(TurnReason.CURRENT_DATA)
    if _MISSION_RE.search(normalized) and not definition:
        reasons.add(TurnReason.MISSION)
    if _SKILL_RE.search(normalized) and not definition:
        reasons.add(TurnReason.SKILL)

    if evidence_domains and lookup and not definition:
        for keywords in evidence_domains.values():
            if any(
                re.search(r"\b" + re.escape(_normalize(keyword)) + r"\b", normalized)
                for keyword in keywords
            ):
                reasons.add(TurnReason.CONNECTED_DATA)
                break

    # A lookup that names a live capability/tool but no stronger category is
    # still connected evidence. This catches arbitrary future MCP objects.
    if required and lookup and not definition:
        reasons.add(TurnReason.CONNECTED_DATA)

    # Questions that clearly request fresh or private evidence but are phrased
    # outside the known lookup vocabulary fail toward the orchestrator.
    if (private or _CURRENT_RE.search(normalized)) and normalized.endswith("?") and not definition:
        reasons.add(TurnReason.UNCERTAIN)

    if not reasons:
        return TurnPlan(path=TurnPath.NATIVE_REALTIME)
    return TurnPlan(
        path=TurnPath.ORCHESTRATOR,
        reasons=frozenset(reasons),
        required_capabilities=required,
        requires_evidence=bool(
            reasons
            & {
                TurnReason.CAPABILITY,
                TurnReason.CONNECTED_DATA,
                TurnReason.CURRENT_DATA,
                TurnReason.LOCAL_STATE,
                TurnReason.PRIVATE_DATA,
            }
        ),
    )


__all__ = ["TurnPath", "TurnPlan", "TurnReason", "plan_turn"]
