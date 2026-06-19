"""Capability Registry — single source of truth for Jarvis action surface.

Every tool, MCP endpoint, harness adapter, and local-action pattern that
Jarvis can execute MUST have a registered Capability before it may be
invoked through the voice path.

Public API (binding — other modules depend on this):
    Capability     — frozen dataclass describing one action surface entry.
    CapabilityRegistry — register / query / render the set.
    get_registry() — module-level singleton accessor.

Seeding: call ``capabilities_seed.seed_registry(get_registry())`` at boot.
MCP tools auto-register via ``jarvis.mcp.adapter.register_mcp_tools_in_registry``.
"""
from __future__ import annotations

import re
import threading
from dataclasses import dataclass
from typing import Literal, Optional


# ---------------------------------------------------------------------------
# Umlaut normalisation helpers
# ---------------------------------------------------------------------------

_UMLAUT_MAP: list[tuple[str, str]] = [
    ("ä", "ae"),
    ("ö", "oe"),
    ("ü", "ue"),
    ("Ä", "ae"),
    ("Ö", "oe"),
    ("Ü", "ue"),
    ("ß", "ss"),
]


def _normalize(text: str) -> str:
    """Lower-case and transliterate German umlauts for uniform matching."""
    t = text.lower()
    for src, dst in _UMLAUT_MAP:
        t = t.replace(src, dst)
    return t


# ---------------------------------------------------------------------------
# Universal action-verb catalogue
# ---------------------------------------------------------------------------
#
# This list is the SUPERSET of action verbs the gate should classify as an
# action request, *independent* of whether a capability covers it.  The whole
# point of the UNSUPPORTED gate is to catch action verbs that resolve to NO
# registered capability — so we cannot derive this list from the registered
# capabilities alone (chicken-and-egg).
#
# Sources:
#  - BrainRoutingConfig.spawn_verbs (jarvis/core/config.py:213)
#  - Hard-negatives from docs/plans/capability-coupling/SPEC.md
#    (email/calendar/whatsapp/order/post — the canonical hallucination cases)
#  - Common German + English imperatives for digital actions
#
# Already umlaut-normalised (no ä/ö/ü/ß).
_UNIVERSAL_ACTION_VERBS: frozenset[str] = frozenset({
    # Existing spawn_verbs (synced with config.py for backwards compatibility)
    "umsetz", "reparier", "fix", "behebe", "korrigier",
    "implementier", "entwickel", "refactor", "debug", "repair",
    "lies", "lese", "liest", "schreib", "schreibe", "schreibt",
    "bau", "baue", "baut", "oeffne", "oeffnet",
    "installier", "deinstallier", "deploy",
    "zeig", "zeige", "zeigt",
    "mach", "mache", "macht", "machen",
    "read", "write", "build", "open", "install", "show", "make",
    "spawn", "starte", "start", "starten", "startet",
    "delegier", "delegiere",
    # Hallucination-prone verbs missing from spawn_verbs (this is the gap
    # that made the email/calendar/whatsapp utterances slip past the gate).
    "schick", "schicke", "schickt", "sende", "send", "sendet",
    "verschick", "verschicke", "verschickt",
    "trag", "trage", "tragt", "eintrag", "eintrage",
    "bestell", "bestelle", "bestellt", "order", "orders",
    "kauf", "kaufe", "kauft", "buy", "purchase",
    "buch", "buche", "bucht", "book", "reserviere", "reservier", "reserve",
    "ruf", "rufe", "ruft", "call",
    "post", "poste", "postet", "tweet", "tweete",
    # NB: bare "halt" is intentionally NOT listed — on its own it is the German
    # discourse particle (a filler word, roughly "just"/"simply"), not a
    # command, and collided with the stop-verb stem to force-spawn pure chat
    # turns (live bug 2026-06-19). Genuine stop/pause commands stay covered by
    # "stop"/"stoppe" (also matches "stopp"/"stoppen") and "anhalt"/"anhalte".
    "anhalt", "anhalte", "halte", "stop", "stoppe",
    "loesch", "loesche", "loescht", "delete", "remove",
    "speichere", "speicher", "speichert", "save",
    "frag", "frage", "fragt", "ask",
    "antwort", "antworte", "antwortet", "reply", "respond",
    "drucke", "drucken", "print",
    "lade", "laden", "ladet", "download", "upload",
    "such", "suche", "sucht", "search", "find", "finde",
})


# ---------------------------------------------------------------------------
# Core dataclass
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class Capability:
    """Describes one discrete action surface entry.

    Attributes:
        id:               Stable dotted identifier, e.g. ``"tool.run-shell"``
                          or ``"mcp.gmail/send_mail"``.
        source:           Where this capability comes from.
        verbs:            DE+EN action verbs (already umlaut-normalised) that
                          indicate the user is requesting this capability.
        objects:          Nouns / domains this capability acts on.
        description:      Single English sentence shown to the brain in the
                          system prompt.
        risk_tier:        Jarvis risk classification for the capability.
        requires_evidence: True when the Critic must see a tool-call artefact
                          before ratifying success.  False for read-only /
                          smalltalk-adjacent capabilities.
    """

    id: str
    source: Literal["router_tool", "mcp", "harness", "local_action", "skill", "cli"]
    verbs: tuple[str, ...]
    objects: tuple[str, ...]
    description: str
    risk_tier: Literal["safe", "monitor", "ask", "block"]
    requires_evidence: bool


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class CapabilityRegistry:
    """Thread-safe registry of all registered Capability entries.

    The registry is intentionally simple: no persistence, no persistence
    hooks, no pub/sub.  It is populated at boot from the seed map and from
    dynamic MCP discovery.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._caps: dict[str, Capability] = {}

    # ------------------------------------------------------------------
    # Mutation
    # ------------------------------------------------------------------

    def register(self, cap: Capability) -> None:
        """Register a Capability.  Re-registering the same id is allowed and
        silently replaces the previous entry (MCP hot-reload support)."""
        with self._lock:
            self._caps[cap.id] = cap

    def deregister(self, cap_id: str) -> None:
        """Remove a capability by id. Unknown id is a silent no-op.

        Needed for plugin-disconnect: a paired plugin capability must be
        withdrawn when the user disconnects the plugin, so resolve_intent
        stops resolving (and the honest refusal / force-spawn returns)."""
        with self._lock:
            self._caps.pop(cap_id, None)

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    def all(self) -> tuple[Capability, ...]:
        """Return all registered capabilities as an immutable tuple."""
        with self._lock:
            return tuple(self._caps.values())

    def resolve_intent(self, utterance: str) -> Optional[Capability]:
        """Deterministic verb+object match against the registered surface.

        Matching strategy (in order):
          1. Normalise the utterance (lower-case, umlaut transliteration).
          2. For each registered Capability compare the normalised utterance
             against the capability's normalised verbs and objects using
             whole-word (``\\b``) boundaries.
          3. A hit requires at least one verb match.  An additional object
             match boosts priority — the most specific capability wins.
          4. Returns the best match or None.

        The algorithm is deterministic: ties are broken by registration order
        (first registered wins), then by specificity (verb+object > verb-only).
        No LLM call, no fuzzy scoring.
        """
        normalised = _normalize(utterance)
        best: Optional[Capability] = None
        best_score = 0

        with self._lock:
            caps = list(self._caps.values())

        for cap in caps:
            # Build per-capability compiled patterns on demand (small N, no
            # caching needed at this scale).
            verb_hit = any(
                re.search(r"\b" + re.escape(_normalize(v)) + r"\b", normalised)
                for v in cap.verbs
            )
            if not verb_hit:
                continue
            obj_hit = any(
                re.search(r"\b" + re.escape(_normalize(o)) + r"\b", normalised)
                for o in cap.objects
            )
            # Plugin/paired-skill AND CLI capabilities are DOMAIN-SPECIFIC:
            # they must match a domain object (noun), not just a generic
            # dispatch verb. Without this, gmail's generic "sende"/"schick"
            # would hijack a different domain's request ("Sende eine
            # WhatsApp"), and a CLI's generic "zeig"/"list" would hijack
            # unrelated requests (AD-CLI2/AD-CLI6). Seed tool/harness/local
            # caps keep their verb-only match (unchanged).
            if cap.source in ("skill", "cli") and not obj_hit:
                continue
            score = 2 if obj_hit else 1
            # A domain-specific paired-skill match (verb + its own domain noun)
            # is the most specific signal -- it beats a generic seed cap that
            # merely shares the verb/object on a tie (e.g. "check mein Postfach"
            # must reach gmail, not dispatch-with-review).
            if cap.source == "skill" and obj_hit:
                score = 3
            if score > best_score:
                best = cap
                best_score = score

        return best

    def has_action_intent(self, utterance: str) -> bool:
        """Return True when the utterance contains any action verb.

        Matches against the UNION of:
          (a) ``_UNIVERSAL_ACTION_VERBS`` — the static catalogue of all
              imperative verbs Jarvis should recognise as an action request,
              INCLUDING verbs that resolve to no registered capability
              (e.g. ``schick``, ``trag``, ``bestelle``).  This is the
              hallucination-prone surface the UNSUPPORTED gate exists to
              guard.
          (b) every registered capability's own verb list — so a freshly
              added MCP whose verbs are not in (a) still counts.

        Smalltalk / Q&A utterances ("wie spaet ist es", "was ist Python")
        match neither and return False.
        """
        normalised = _normalize(utterance)
        # (a) universal catalogue — covers email/calendar/order/etc. even
        # when no capability is registered for them.
        for v in _UNIVERSAL_ACTION_VERBS:
            if re.search(r"\b" + re.escape(v) + r"\w*\b", normalised):
                return True
        # (b) registered capabilities — picks up MCP-provided verbs that
        # are not in the universal list.
        with self._lock:
            caps = list(self._caps.values())
        for cap in caps:
            for v in cap.verbs:
                if re.search(r"\b" + re.escape(_normalize(v)) + r"\b", normalised):
                    return True
        return False

    def render_for_prompt(self, lang: Literal["de", "en"] = "de") -> str:
        """Render the registered capabilities as a bullet list for the system
        prompt, replacing the old hard-coded ``NUTZE: search_web`` block.

        Each line: ``• <id> — <description>``

        The *lang* parameter is reserved for future localisation.  Currently
        all descriptions are English (CLAUDE.md policy) regardless of lang.
        """
        caps = self.all()
        if not caps:
            return "No capabilities registered."
        lines = [f"• {cap.id} — {cap.description}" for cap in caps]
        return "\n".join(lines)


# ---------------------------------------------------------------------------
# Singleton accessor
# ---------------------------------------------------------------------------

_registry_lock = threading.Lock()
_registry_instance: Optional[CapabilityRegistry] = None


def get_registry() -> CapabilityRegistry:
    """Return the process-wide singleton CapabilityRegistry.

    Thread-safe double-checked locking.  Safe to call from any thread at
    any boot stage; the instance is created lazily on first access.
    """
    global _registry_instance  # noqa: PLW0603
    if _registry_instance is None:
        with _registry_lock:
            if _registry_instance is None:
                _registry_instance = CapabilityRegistry()
    return _registry_instance


def _reset_registry_for_tests() -> None:
    """Drop the singleton so the next get_registry() call creates a fresh one.

    Test-only helper — call in teardown when a test constructs MCPToolAdapter
    or other objects that register capabilities as a side effect, to prevent
    cross-test contamination of the shared CapabilityRegistry.
    """
    global _registry_instance  # noqa: PLW0603
    with _registry_lock:
        _registry_instance = None
