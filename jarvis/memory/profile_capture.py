"""Deterministic profile capture — the missing writer for USER.md.

USER.md has a working pen and a working notebook and nobody telling it to
write. The legacy auto-curator that used to maintain it is soft-disabled by
design (``memory.legacy_curator.enabled = false``, 2026-05-17), which left
exactly one automatic path: the brain deciding, mid-turn, to call
``update_profile`` on its own. That is a request in a system prompt, and
ignoring it is free — measured on this box, the file went 26 days and every
single turn without gaining one fact.

This module is the deterministic half of the answer. It reads the user's own
sentence and recognises the handful of shapes in which a person actually
states a durable fact about themselves — "call me Chef", "my pronouns are
they/them", "I also speak Spanish", "no emojis please", and their German
equivalents. No model call, so it cannot be dropped from a tool budget, cannot
time out, costs nothing per turn and runs the same offline (AP-11: regex only,
never an LLM, on a path the voice loop touches).

Recall is deliberately traded away for precision: a fact that
is not recognised costs nothing, a fact recognised WRONGLY writes a lie into
the user's profile, so every pattern here demands an explicit first-person
self-statement and every value is validated before it is offered.

The writer half is :func:`apply_profile_facts`, which goes through the SAME
canonical allow-list, list/bool typing and privacy filter as the
``update_profile`` tool (imported from it, so the two can never drift), and
publishes ``ProfileUpdated`` so the Profile view refreshes live.

What this is NOT: a general fact extractor. Anything conversational, implied
or hedged is left to the LLM paths. This one only catches the sentences whose
meaning is unambiguous in the words themselves.
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

# The single source of truth for what may be written and how it is typed —
# shared with the brain's update_profile tool and the REST editor so the three
# writers can never disagree about the vocabulary (the BUG-008 enum-drift
# class). profile_routes.py imports the same private names.
from jarvis.plugins.tool.profile_update import (
    _BOOL_FIELDS,
    _CANONICAL_FIELDS,
    _DO_NOT_RECORD,
    _LIST_FIELDS,
)

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus

log = logging.getLogger(__name__)

# A captured scalar longer than this is not a profile fact, it is a sentence
# that happened to start with a matching prefix. Names are held tighter still.
_MAX_VALUE_CHARS = 60
_MAX_NAME_CHARS = 40

# Trailing noise a spoken sentence leaves on the captured group.
_TRIM_CHARS = " \t\"'“”„«»().,;:!?-–—"

# Words that mean the speaker did not actually name anything ("call me back",
# "just call me"). A captured group that IS one of these is noise.
_NOISE_VALUES: frozenset[str] = frozenset({
    "mal", "auch", "einfach", "bitte", "doch", "so", "wieder", "nochmal",  # i18n-allow
    "gerne", "immer", "jetzt", "kurz", "back", "later", "please", "just",  # i18n-allow
    "again", "now", "me", "you", "it", "that", "this", "es", "das", "dich",  # i18n-allow
})

# Spoken language name -> the code USER.md stores in identity.primary_language
# and identity.languages. Both spellings of each language are listed because
# the user may say either in either sentence.
_LANGUAGE_CODES: dict[str, str] = {
    "deutsch": "de", "german": "de",  # i18n-allow
    "englisch": "en", "english": "en",  # i18n-allow
    "spanisch": "es", "spanish": "es", "espanol": "es", "español": "es",  # i18n-allow
    "französisch": "fr", "franzoesisch": "fr", "french": "fr",  # i18n-allow
    "italienisch": "it", "italian": "it",  # i18n-allow
    "niederländisch": "nl", "niederlaendisch": "nl", "dutch": "nl",  # i18n-allow
    "portugiesisch": "pt", "portuguese": "pt",  # i18n-allow
    "polnisch": "pl", "polish": "pl",  # i18n-allow
    "türkisch": "tr", "tuerkisch": "tr", "turkish": "tr",  # i18n-allow
    "russisch": "ru", "russian": "ru",  # i18n-allow
}

_LANG_ALTERNATION = "|".join(sorted(map(re.escape, _LANGUAGE_CODES), key=len, reverse=True))


@dataclass(frozen=True, slots=True)
class ProfileFact:
    """One durable fact recognised in the user's own sentence."""

    cluster: str
    field: str
    value: Any
    #: The sentence fragment the fact was read from — shown in the audit log
    #: and carried on ``ProfileUpdated`` so a write is never anonymous.
    evidence: str


# ----------------------------------------------------------------------
# Value shaping
# ----------------------------------------------------------------------


def _clean(raw: str) -> str:
    """Trim spoken punctuation and collapse whitespace."""
    return re.sub(r"\s+", " ", str(raw or "")).strip(_TRIM_CHARS).strip()


def _usable_scalar(raw: str, *, limit: int = _MAX_VALUE_CHARS) -> str | None:
    """The captured group as a storable scalar, or ``None`` to discard it.

    Rejects the shapes that mean the pattern matched a sentence rather than a
    value: empty, noise-only, over-long (a clause, not a value), or carrying a
    question mark (the user was asking, not stating).
    """
    value = _clean(raw)
    if not value or len(value) > limit:
        return None
    if "?" in value:
        return None
    if value.casefold() in _NOISE_VALUES:
        return None
    # The first token decides whether the pattern caught a value or ran on
    # into the rest of the sentence: "call me back later" captures "back
    # later", which is a phrasal verb plus an adverb, not what to call someone.
    # Nobody's name or form of address starts with a filler word.
    if value.split()[0].casefold() in _NOISE_VALUES:
        return None
    # A value made only of punctuation or digits is never a profile fact.
    if not any(ch.isalpha() for ch in value):
        return None
    return value


def _language_code(raw: str) -> str | None:
    return _LANGUAGE_CODES.get(_clean(raw).casefold())


def _violates_privacy(value: Any, evidence: str) -> bool:
    """The USER.md "Do Not Record" contract, applied to what we captured.

    Identical rule to ``update_profile``: politics, religion, health and
    pseudo-scientific typologies never reach the file, however they are
    phrased.
    """
    haystack = f"{value} {evidence}".casefold()
    return any(term in haystack for term in _DO_NOT_RECORD)


# ----------------------------------------------------------------------
# The patterns
# ----------------------------------------------------------------------
#
# Each entry is (cluster, field, compiled pattern, transform). The pattern
# must capture the value as group 1 (or match with no group for the fixed
# ones, where the transform supplies the value). Every pattern is anchored on
# an explicit first-person self-statement — "call me X" and never a bare "X".

def _p(pattern: str) -> re.Pattern[str]:
    return re.compile(pattern, re.IGNORECASE)


# --- identity ---------------------------------------------------------

_NAME_PATTERNS = (
    _p(r"\bich hei(?:ß|ss)e\s+(.+)"),  # i18n-allow
    _p(r"\bmein name ist\s+(.+)"),  # i18n-allow
    _p(r"\bmy name(?:'s| is)\s+(.+)"),
    _p(r"\bi'?m called\s+(.+)"),
)

_ADDRESS_PATTERNS = (
    _p(r"\bnenn(?:e|st)?\s+mich\s+(?:doch\s+|einfach\s+|bitte\s+)*(.+)"),  # i18n-allow
    _p(r"\bdu kannst mich\s+(.+?)\s+nennen"),  # i18n-allow
    _p(r"\bsag(?:e)?\s+(?:einfach\s+|bitte\s+)*(.+?)\s+zu mir"),  # i18n-allow
    _p(r"\bcall me\s+(?:just\s+|simply\s+)*(.+)"),
)

_PRONOUN_PATTERNS = (
    _p(r"\bmeine pronomen sind\s+(.+)"),  # i18n-allow
    _p(r"\bmy pronouns are\s+(.+)"),
)

_TIMEZONE_PATTERNS = (
    _p(r"\bmeine zeitzone ist\s+(.+)"),  # i18n-allow
    _p(r"\bich bin in der zeitzone\s+(.+)"),  # i18n-allow
    _p(r"\bmy time\s?zone is\s+(.+)"),
    _p(r"\bi'?m in (?:the )?time\s?zone\s+(.+)"),
)

# A second language the user mentions speaking — appended, never replacing.
_EXTRA_LANGUAGE_PATTERNS = (
    _p(rf"\bich spreche auch\s+({_LANG_ALTERNATION})\b"),  # i18n-allow
    _p(rf"\bich kann auch\s+({_LANG_ALTERNATION})\b"),  # i18n-allow
    _p(rf"\bi also speak\s+({_LANG_ALTERNATION})\b"),
)

# The language the conversation should be held in.
_PRIMARY_LANGUAGE_PATTERNS = (
    _p(rf"\b(?:sprich|rede)\s+(?:bitte\s+)?({_LANG_ALTERNATION})\s+mit mir\b"),  # i18n-allow
    _p(rf"\blass uns\s+({_LANG_ALTERNATION})\s+(?:sprechen|reden)\b"),  # i18n-allow
    _p(rf"\bspeak\s+({_LANG_ALTERNATION})\s+with me\b"),
    _p(rf"\blet'?s speak\s+({_LANG_ALTERNATION})\b"),
)

# --- communication ----------------------------------------------------
#
# These are the fixed-value fields: the sentence does not carry a value, its
# mere shape IS the value ("no emojis" means emoji_ok = false).

_EMOJI_OFF = (
    _p(r"\bkeine emojis?\b"),  # i18n-allow
    _p(r"\b(?:lass|lasse)\s+die emojis? weg\b"),  # i18n-allow
    _p(r"\bno emojis?\b"),
    _p(r"\bskip the emojis?\b"),
)
_EMOJI_ON = (
    _p(r"\bemojis? (?:sind|find(?:e)? ich)\s+(?:ok|okay|gut|super)\b"),  # i18n-allow
    _p(r"\bemojis? are (?:fine|ok|okay|good)\b"),
)

_DIRECTNESS_HIGH = (
    _p(r"\bsei\s+(?:ruhig\s+|bitte\s+)*direkt\b"),  # i18n-allow
    _p(r"\b(?:sag|sags)\s+(?:es\s+)?mir\s+(?:gerade heraus|geradeheraus|direkt)\b"),  # i18n-allow
    _p(r"\bred(?:e)?\s+nicht um den hei(?:ß|ss)en brei\b"),  # i18n-allow
    _p(r"\bbe (?:blunt|direct) with me\b"),
    _p(r"\bdon'?t sugarcoat\b"),
)

_VERBOSITY_SHORT = (
    _p(r"\bfass dich kurz\b"),  # i18n-allow
    _p(r"\bhalt(?:e)? (?:es |dich )?kurz\b"),  # i18n-allow
    _p(r"\bkurze antworten\b"),  # i18n-allow
    _p(r"\bkeep (?:it|your answers) (?:short|brief)\b"),
    _p(r"\bbe (?:brief|concise)\b"),
)
_VERBOSITY_LONG = (
    _p(r"\berklär(?:e)? mir das ausführlich\b"),  # i18n-allow
    _p(r"\bgib mir (?:immer )?das ganze bild\b"),  # i18n-allow
    _p(r"\bgive me the full picture\b"),
)

_FORMALITY_CASUAL = (
    _p(r"\bduz(?:e)?\s+mich\b"),  # i18n-allow
    _p(r"\bwir k(?:ö|oe)nnen uns duzen\b"),  # i18n-allow
    _p(r"\bkeep it casual\b"),
)
_FORMALITY_FORMAL = (
    _p(r"\bsiez(?:e)?\s+mich\b"),  # i18n-allow
    _p(r"\bbleib(?:en)? wir beim sie\b"),  # i18n-allow
)

# --- values / relationship / work style -------------------------------

_PET_PEEVE_PATTERNS = (
    _p(r"\bmich nervt(?:\s+es)?(?:\s+total)?,?\s+wenn\s+(.+)"),  # i18n-allow
    _p(r"\bich hasse es,?\s+wenn\s+(.+)"),  # i18n-allow
    _p(r"\bi hate it when\s+(.+)"),
    _p(r"\bit drives me (?:crazy|nuts) when\s+(.+)"),
)

_FEEDBACK_DIRECT = (
    _p(r"\bgib mir (?:das )?feedback (?:bitte )?(?:immer )?direkt\b"),  # i18n-allow
    _p(r"\bfeedback (?:bitte )?(?:immer )?direkt\b"),  # i18n-allow
    _p(r"\bgive me feedback straight\b"),
    _p(r"\bbe straight with me about feedback\b"),
)

_FOCUS_DEEP = (
    _p(r"\bst(?:ö|oe)r(?:e)? mich nicht,?\s+wenn ich fokussiert bin\b"),  # i18n-allow
    _p(r"\bich arbeite (?:am liebsten )?in langen bl(?:ö|oe)cken\b"),  # i18n-allow
    _p(r"\bdon'?t interrupt my focus\b"),
)


def _first_capture(text: str, patterns: tuple[re.Pattern[str], ...]) -> tuple[str, str] | None:
    """First pattern that matches: (captured group 1, the matched fragment)."""
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return m.group(1), m.group(0)
    return None


def _first_match(text: str, patterns: tuple[re.Pattern[str], ...]) -> str | None:
    """First pattern that matches, returning the matched fragment as evidence."""
    for pattern in patterns:
        m = pattern.search(text)
        if m:
            return m.group(0)
    return None


# ----------------------------------------------------------------------
# Extraction
# ----------------------------------------------------------------------


def extract_profile_facts(text: str) -> tuple[ProfileFact, ...]:
    """Recognise durable self-statements in one user utterance.

    Pure and side-effect free — the whole point is that it is cheap enough to
    run on every turn and deterministic enough to be pinned by tests. Returns
    at most one fact per field; an utterance that states nothing recognisable
    returns an empty tuple, which is the overwhelmingly common case and costs
    a handful of regex scans.
    """
    raw = str(text or "").strip()
    if not raw:
        return ()

    out: list[ProfileFact] = []

    def add(cluster: str, field: str, value: Any, evidence: str) -> None:
        # Defence in depth: never emit a field the canonical map does not own,
        # even if a pattern above is edited to name one.
        if field not in _CANONICAL_FIELDS.get(cluster, frozenset()):
            log.debug("profile_capture: pattern named unknown field %s.%s", cluster, field)
            return
        if _violates_privacy(value, evidence):
            log.debug(
                "profile_capture: privacy filter discarded %s.%s", cluster, field
            )
            return
        out.append(
            ProfileFact(
                cluster=cluster, field=field, value=value, evidence=_clean(evidence)
            )
        )

    # -- scalar captures ------------------------------------------------
    scalars: tuple[tuple[str, tuple[re.Pattern[str], ...], int], ...] = (
        ("name", _NAME_PATTERNS, _MAX_NAME_CHARS),
        ("preferred_address", _ADDRESS_PATTERNS, _MAX_NAME_CHARS),
        ("pronouns", _PRONOUN_PATTERNS, _MAX_NAME_CHARS),
        ("timezone", _TIMEZONE_PATTERNS, _MAX_VALUE_CHARS),
    )
    for field, patterns, limit in scalars:
        hit = _first_capture(raw, patterns)
        if hit:
            value = _usable_scalar(hit[0], limit=limit)
            if value:
                add("identity", field, value, hit[1])

    # -- language captures ----------------------------------------------
    hit = _first_capture(raw, _PRIMARY_LANGUAGE_PATTERNS)
    if hit:
        code = _language_code(hit[0])
        if code:
            add("identity", "primary_language", code, hit[1])

    hit = _first_capture(raw, _EXTRA_LANGUAGE_PATTERNS)
    if hit:
        code = _language_code(hit[0])
        if code:
            add("identity", "languages", code, hit[1])

    # -- pet peeve (a clause, appended) ---------------------------------
    hit = _first_capture(raw, _PET_PEEVE_PATTERNS)
    if hit:
        value = _usable_scalar(hit[0], limit=_MAX_VALUE_CHARS)
        if value:
            add("values", "pet_peeves", value, hit[1])

    # -- fixed-value shapes ---------------------------------------------
    fixed: tuple[tuple[str, str, Any, tuple[re.Pattern[str], ...]], ...] = (
        ("communication", "emoji_ok", False, _EMOJI_OFF),
        ("communication", "emoji_ok", True, _EMOJI_ON),
        ("communication", "directness", "direct", _DIRECTNESS_HIGH),
        ("communication", "verbosity", "short", _VERBOSITY_SHORT),
        ("communication", "verbosity", "deep-dive", _VERBOSITY_LONG),
        ("communication", "formality", "casual", _FORMALITY_CASUAL),
        ("communication", "formality", "formal", _FORMALITY_FORMAL),
        ("relationship", "feedback_pref", "direct", _FEEDBACK_DIRECT),
        ("work_style", "focus_mode", "deep-work", _FOCUS_DEEP),
    )
    for cluster, field, value, patterns in fixed:
        # A field already captured by an earlier (more specific) shape wins —
        # "no emojis" and "emojis are fine" can never both be written.
        if any(f.cluster == cluster and f.field == field for f in out):
            continue
        evidence = _first_match(raw, patterns)
        if evidence:
            add(cluster, field, value, evidence)

    return tuple(out)


# ----------------------------------------------------------------------
# Writing
# ----------------------------------------------------------------------


async def apply_profile_facts(
    profile: Any,
    facts: tuple[ProfileFact, ...] | list[ProfileFact],
    *,
    bus: EventBus | None = None,
) -> tuple[ProfileFact, ...]:
    """Persist ``facts`` to USER.md; return the ones that actually changed it.

    List fields append (deduped), booleans and scalars set. A fact whose value
    the file already carries is dropped silently — re-saying "call me Chef"
    must not republish ``ProfileUpdated`` and flash the UI on every turn.
    """
    written: list[ProfileFact] = []
    for fact in facts:
        try:
            key = (fact.cluster, fact.field)
            # UserProfile owns the typing and the dedupe: append_list skips a
            # value the list already holds, set() skips an unchanged scalar, and
            # both answer "did this change anything". Re-stating "call me Chef"
            # therefore writes nothing and publishes nothing.
            if key in _LIST_FIELDS:
                changed = profile.append_list(fact.cluster, fact.field, fact.value)
            elif key in _BOOL_FIELDS:
                changed = profile.set(fact.cluster, fact.field, bool(fact.value))
            else:
                changed = profile.set(fact.cluster, fact.field, fact.value)

            if changed:
                written.append(fact)
                log.info(
                    "profile_capture: wrote %s.%s from the user's own words (%r)",
                    fact.cluster, fact.field, fact.evidence[:60],
                )
        except Exception:  # noqa: BLE001 — one bad fact must not lose the others
            log.warning(
                "profile_capture: could not write %s.%s", fact.cluster, fact.field, exc_info=True
            )

    if not written:
        return ()

    try:
        profile.save()
    except Exception:  # noqa: BLE001 — a failed save is reported, never silent
        log.error("profile_capture: USER.md save failed; facts not persisted", exc_info=True)
        return ()

    if bus is not None:
        for fact in written:
            try:
                from jarvis.core.events import ProfileUpdated

                await bus.publish(
                    ProfileUpdated(
                        subject="user",
                        cluster=fact.cluster,
                        field=fact.field,
                        operation="append" if (fact.cluster, fact.field) in _LIST_FIELDS else "set",
                        confidence=1.0,
                        evidence=fact.evidence,
                    )
                )
            except Exception:  # noqa: BLE001 — the write already landed; the badge is cosmetic
                log.debug("profile_capture: ProfileUpdated publish failed", exc_info=True)

    return tuple(written)


async def capture_from_utterance(
    profile: Any,
    text: str,
    *,
    bus: EventBus | None = None,
) -> tuple[ProfileFact, ...]:
    """Extract and persist in one call — what the bus subscriber runs."""
    facts = extract_profile_facts(text)
    if not facts:
        return ()
    return await apply_profile_facts(profile, facts, bus=bus)


# ----------------------------------------------------------------------
# The bus subscriber
# ----------------------------------------------------------------------


class ProfileCaptureBridge:
    """Runs the capture on every finished turn, both voice engines and chat.

    Deliberately independent of the wiki integration: the wiki path depends on
    an external curator provider and a vault, and USER.md must keep being
    maintained when that is unavailable — which is the state this box was
    actually in. The only dependencies here are the bus and the profile.

    Every handler is guarded: capture is a background nicety, and a fault in it
    must never propagate into the turn that triggered it (AP-18 — a subscriber
    exception never escapes into the dispatch).
    """

    def __init__(self, *, bus: EventBus, profile: Any) -> None:
        self._bus = bus
        self._profile = profile
        # EventBus.subscribe returns None — detaching needs the (type, handler)
        # pair handed back to unsubscribe(), so keep them rather than a
        # closure that does not exist.
        self._subscriptions: list[tuple[type[Any], Any]] = []
        self._started = False

    def start(self) -> None:
        """Subscribe to the completed-turn events of both engines."""
        if self._started:
            return
        from jarvis.core.events import MessageSent, VoiceTurnCompleted

        for event_type, handler in (
            (VoiceTurnCompleted, self._on_voice_turn),
            (MessageSent, self._on_message),
        ):
            self._bus.subscribe(event_type, handler)
            self._subscriptions.append((event_type, handler))
        self._started = True
        log.info("ProfileCaptureBridge started — USER.md is maintained from user turns")

    def stop(self) -> None:
        """Detach from the bus. Idempotent."""
        for event_type, handler in self._subscriptions:
            try:
                self._bus.unsubscribe(event_type, handler)
            except Exception:  # noqa: BLE001 — teardown must not raise
                log.debug("ProfileCaptureBridge: unsubscribe failed", exc_info=True)
        self._subscriptions.clear()
        self._started = False

    async def _on_voice_turn(self, event: Any) -> None:
        await self._capture(getattr(event, "user_text", ""))

    async def _on_message(self, event: Any) -> None:
        # MessageSent carries both directions; only the user's own words are a
        # source of facts about the user.
        if str(getattr(event, "role", "user")).lower() != "user":
            return
        await self._capture(getattr(event, "text", ""))

    async def _capture(self, text: str) -> None:
        try:
            await capture_from_utterance(self._profile, text, bus=self._bus)
        except Exception:  # noqa: BLE001 — never let capture break a turn
            log.warning("ProfileCaptureBridge: capture failed", exc_info=True)


__all__ = [
    "ProfileCaptureBridge",
    "ProfileFact",
    "apply_profile_facts",
    "capture_from_utterance",
    "extract_profile_facts",
]
