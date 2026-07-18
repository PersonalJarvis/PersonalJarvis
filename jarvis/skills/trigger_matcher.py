"""TriggerMatcher: mappt Voice-Utterances / Hotkey-Presses / Cron-Ticks auf Skills.

Prioritäten bei Collision: hotkey > voice > cron.
"""
from __future__ import annotations

import asyncio
import logging
import re
import time
from collections.abc import AsyncIterator
from datetime import datetime

from .registry import SkillRegistry
from .schema import Skill, SkillLifecycleState

try:
    from croniter import croniter  # type: ignore
    _HAVE_CRONITER = True
except Exception:  # pragma: no cover
    croniter = None  # type: ignore
    _HAVE_CRONITER = False

log = logging.getLogger(__name__)


def normalize_hotkey(combo: str) -> str:
    """Canonicalisiert 'Ctrl+Alt+J' → 'alt+ctrl+j' (alphabetisch sortierte Mods)."""
    if not combo:
        return ""
    parts = [p.strip().lower() for p in combo.split("+") if p.strip()]
    if not parts:
        return ""
    mods_set = {"ctrl", "shift", "alt", "win", "cmd", "super", "meta",
                "left_ctrl", "right_ctrl", "left_alt", "right_alt",
                "left_shift", "right_shift"}
    mods = sorted([p for p in parts if p in mods_set])
    keys = [p for p in parts if p not in mods_set]
    return "+".join(mods + keys)


# Politeness / address fillers peeled off the head and tail of an utterance
# before an ``^...$``-anchored voice pattern is re-tried (Step 2: tolerant
# matching). DELIBERATELY CONSERVATIVE — only address tokens, courtesy words
# and imperative command glue. Narrative words ("ich", "nur", "wollte",
# "sagen", ...) are intentionally absent: that asymmetry keeps a casual
# mention ("ich wollte nur guten morgen sagen") from firing the skill while a
# real command ("Jarvis, bitte starte die Morgenroutine") still does, because
# only the *edges* are stripped and the surviving core must satisfy the
# anchored pattern on its own.
_FILLER_WORDS: frozenset[str] = frozenset({
    # address / wake
    "jarvis", "hey", "ok", "okay", "hallo", "hello", "yo", "computer",
    # courtesy
    "bitte", "please", "danke", "thanks", "mal", "doch", "kurz",
    # imperative command glue (NOT narrative verbs)
    "mach", "machst", "kannst", "könntest", "koenntest", "würdest",
    "wuerdest", "kann", "du", "mir", "uns", "lass", "lasst",
    "let", "lets", "us", "can", "could", "would", "you",
    # temporal courtesy
    "jetzt", "now", "gleich", "schnell", "für", "fuer", "for", "me", "mich",
})

# Penalty applied to a filler-stripped match so a direct (unstripped) hit on
# any skill always wins over a tolerant fallback hit on another.
_FILLER_MATCH_PENALTY = 100_000

_PUNCT_RE = re.compile(r"[^\w\s]", re.UNICODE)


def _strip_fillers(utterance: str) -> str:
    """Return ``utterance`` with leading/trailing politeness fillers removed.

    Punctuation is flattened to spaces first; then filler tokens are peeled
    off the head and the tail. The middle is never touched — so the surviving
    core must still satisfy the anchored pattern by itself, which is exactly
    the "phrase must carry the sentence" contract.
    """
    cleaned = _PUNCT_RE.sub(" ", utterance)
    tokens = cleaned.split()
    if not tokens:
        return ""
    start, end = 0, len(tokens)
    while start < end and tokens[start].lower() in _FILLER_WORDS:
        start += 1
    while end > start and tokens[end - 1].lower() in _FILLER_WORDS:
        end -= 1
    return " ".join(tokens[start:end])


class TriggerMatcher:
    """Zentrale Match-Instanz — hält keinen State außer Cache der kompilierten Regexes."""

    def __init__(self, registry: SkillRegistry) -> None:
        self.registry = registry
        self._voice_cache: dict[str, re.Pattern[str]] = {}

    # ------------------------------------------------------------------
    # Activation-Filter (Skills-Brain-Integration: Phase Skills-1)
    # ------------------------------------------------------------------

    @staticmethod
    def _is_matchable(sk: Skill) -> bool:
        """A skill may only trigger when it is ACTIVE or VALIDATED.

        DRAFT skills either have parser/validator errors or are
        Jarvis-Agent-generated drafts (Phase 7.5) — those must never fire
        automatically. DISABLED skills were explicitly turned off by the user.
        """
        return sk.state in (
            SkillLifecycleState.ACTIVE,
            SkillLifecycleState.VALIDATED,
        )

    # ------------------------------------------------------------------
    # Voice
    # ------------------------------------------------------------------

    def _get_pattern(self, raw: str) -> re.Pattern[str] | None:
        if raw in self._voice_cache:
            return self._voice_cache[raw]
        try:
            pat = re.compile(raw, re.IGNORECASE)
        except re.error:
            return None
        self._voice_cache[raw] = pat
        return pat

    def match_voice(self, utterance: str, lang: str = "auto") -> Skill | None:
        result = self.match_voice_with_match(utterance, lang)
        return result[0] if result else None

    def match_voice_with_match(
        self,
        utterance: str,
        lang: str = "auto",
    ) -> tuple[Skill, re.Match[str]] | None:
        """Wie ``match_voice``, gibt zusaetzlich das Match-Objekt zurueck.

        Caller (z.B. die Speech-Pipeline) brauchen die Capture-Groups —
        memory-save speichert die letzte Group, andere Skills mappen sie
        in ihren Jinja-Context.
        """
        if not utterance:
            return None
        # Step 2: politeness-tolerant fallback for ``^...$``-anchored patterns.
        # The raw utterance is tried first (preserves all existing behaviour,
        # incl. un-anchored "contains" patterns); only on a miss do we retry
        # against the filler-stripped core. A stripped hit carries a score
        # penalty so a direct hit on any skill always outranks it.
        stripped = _strip_fillers(utterance)
        best: tuple[int, Skill, re.Match[str]] | None = None
        for sk in self.registry.by_trigger("voice"):
            if not self._is_matchable(sk):
                continue
            if sk.frontmatter is None:
                continue
            for t in sk.frontmatter.triggers:
                if t.type != "voice" or not t.pattern:
                    continue
                if lang != "auto" and t.language and lang not in t.language:
                    continue
                pat = self._get_pattern(t.pattern)
                if pat is None:
                    continue
                penalty = 0
                m = pat.search(utterance)
                if m is None and stripped and stripped != utterance:
                    m = pat.search(stripped)
                    penalty = _FILLER_MATCH_PENALTY
                if m is None:
                    continue
                score = len(m.group(0)) - penalty
                if best is None or score > best[0]:
                    best = (score, sk, m)
        return (best[1], best[2]) if best else None

    # ------------------------------------------------------------------
    # Hotkey
    # ------------------------------------------------------------------

    def match_hotkey(self, combo: str) -> Skill | None:
        target = normalize_hotkey(combo)
        if not target:
            return None
        for sk in self.registry.by_trigger("hotkey"):
            if not self._is_matchable(sk):
                continue
            if sk.frontmatter is None:
                continue
            for t in sk.frontmatter.triggers:
                if t.type != "hotkey" or not t.combo:
                    continue
                if normalize_hotkey(t.combo) == target:
                    return sk
        return None

    # ------------------------------------------------------------------
    # Cron
    # ------------------------------------------------------------------

    def _next_fire(self, cron_expr: str, base: datetime) -> datetime | None:
        if not _HAVE_CRONITER:
            return None
        try:
            it = croniter(cron_expr, base)  # type: ignore[operator]
            return it.get_next(datetime)
        except Exception:  # noqa: BLE001
            return None

    async def run_cron_scheduler(
        self,
        stop_event: asyncio.Event,
        now_fn=datetime.now,
    ) -> AsyncIterator[Skill]:
        """Langläufiger Scheduler — yielded Skill, wenn ein Cron-Trigger feuert.

        Pseudo-Code:
            while not stop:
                compute soonest (skill, next_fire) pair
                sleep until next_fire
                yield skill
        """
        if not _HAVE_CRONITER:
            log.warning("croniter nicht installiert — cron-scheduler inactive")
            return
        while not stop_event.is_set():
            now = now_fn()
            soonest: tuple[datetime, Skill] | None = None
            for sk in self.registry.by_trigger("schedule"):
                if not self._is_matchable(sk):
                    continue
                if sk.frontmatter is None:
                    continue
                for t in sk.frontmatter.triggers:
                    if t.type != "schedule" or not t.cron:
                        continue
                    nxt = self._next_fire(t.cron, now)
                    if nxt is None:
                        continue
                    if soonest is None or nxt < soonest[0]:
                        soonest = (nxt, sk)
            if soonest is None:
                # Keine Cron-Skills — in 60s nochmal gucken
                try:
                    await asyncio.wait_for(stop_event.wait(), timeout=60.0)
                except TimeoutError:
                    pass
                continue
            fire_at, skill = soonest
            delay = max(0.0, (fire_at - now).total_seconds())
            try:
                await asyncio.wait_for(stop_event.wait(), timeout=delay)
                # stop_event wurde gesetzt → Ende
                return
            except TimeoutError:
                pass
            yield skill

    # ------------------------------------------------------------------
    # Priority-Arbitration
    # ------------------------------------------------------------------

    def resolve(
        self,
        *,
        hotkey: str | None = None,
        utterance: str | None = None,
        lang: str = "auto",
    ) -> Skill | None:
        """Prüft Trigger in Prioritäts-Reihenfolge: hotkey > voice."""
        if hotkey:
            sk = self.match_hotkey(hotkey)
            if sk:
                return sk
        if utterance:
            return self.match_voice(utterance, lang)
        return None


# Re-export zur Convenience
__all__ = ["TriggerMatcher", "normalize_hotkey"]

# Silence unused-import for `time`
_ = time
