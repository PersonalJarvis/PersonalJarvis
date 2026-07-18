"""Self-echo TEXT guard shared by every voice surface (BUG-084, BUG-089).

Last line of defense behind the acoustic gates (barge-in energy floor,
post-TTS suppression window): when a "user" utterance that arrives during or
right after the assistant's own playback consists — fuzzily, STT garbles
echo — of nothing but words the assistant itself just spoke, it is the
speaker echo that slipped every acoustic gate, never a turn to answer.
Without this, ONE missed false barge loops forever: reply → echo transcribed
→ brain answers itself → new reply → new echo (the Mac test machine's
multi-turn self-conversation, 2026-07-18). Conservative by design:
near-total containment in the assistant's own recent words is required, so a
genuine user answer that ADDS anything is always kept (fail-open).

The logic originated as pipeline-private methods (BUG-084); it lives here so
the classic pipeline and the realtime session (BUG-089) share ONE
implementation instead of drifting copies. Deliberately dependency-light
(``re``/``difflib``/``deque``/``time`` only) so the optional realtime stack
imports it without touching the pipeline module.
"""

from __future__ import annotations

import difflib
import re
import time
from collections import deque

__all__ = ["SelfEchoGuard"]


class SelfEchoGuard:
    """Remembers recently voiced assistant text and judges suspected echoes."""

    WINDOW_S = 6.0
    REF_TTL_S = 30.0
    MIN_TOKENS = 3
    MIN_OVERLAP = 0.8
    # 0.8, not lower: at 0.75 near-misses like "gut"→"guten" (ratio exactly
    # 0.75) count as contained and a genuine short user answer built from the
    # assistant's own words plus one inflected token would be eaten. Real STT
    # echo garble sits above 0.8:
    # "misch"→"mich" 0.89, "hörn"→"hören" 0.89.  # i18n-allow: garble anchors
    FUZZY_CUTOFF = 0.8

    def __init__(self) -> None:
        # (slot, tokens, registered_ns); slot=None entries are append-only.
        self._refs: deque[tuple[str | None, list[str], int]] = deque(maxlen=8)
        self.activity_ns: int = 0

    @staticmethod
    def tokens(text: str) -> list[str]:
        return re.findall(r"\w+", text.lower())

    def register(self, text: str, *, slot: str | None = None) -> None:
        """Remember text the assistant is about to voice as an echo reference.

        ``slot`` makes a reference replaceable: re-registering the same slot
        removes the previous snapshot and appends the new one, so a cumulative
        per-turn transcript occupies ONE deque entry instead of evicting every
        other reference with its own growing prefixes.
        """
        tokens = self.tokens(text)
        if not tokens:
            return
        if slot is not None:
            for index, (ref_slot, _tokens, _ns) in enumerate(self._refs):
                if ref_slot == slot:
                    del self._refs[index]
                    break
        self._refs.append((slot, tokens, time.time_ns()))
        self.touch()

    def touch(self, activity_ns: int | None = None, *, force: bool = False) -> None:
        """Stamp "assistant audio is active around ``activity_ns``".

        Accepts a FUTURE stamp: the realtime session dates activity forward to
        the estimated physical playback drain, because provider audio arrives
        faster than realtime and the surface never reports the drain back. By
        default the stamp only ever advances, so a plain touch cannot pull an
        armed horizon back; ``force=True`` overwrites (barge-in/cancel resets
        the horizon to "now", tests set a synthetic past).
        """
        stamp = int(time.time_ns() if activity_ns is None else activity_ns)
        if force:
            self.activity_ns = stamp
        else:
            self.activity_ns = max(self.activity_ns, stamp)

    def is_echo(self, text: str) -> bool:
        """True when ``text`` is (fuzzily) contained in recent assistant speech.

        Only consulted while playback activity is recent (``WINDOW_S``);
        outside that window a user may echo the assistant verbatim all they
        want. Tokens match fuzzily (``difflib``-ratio ≥ ``FUZZY_CUTOFF``)
        because STT garbles echo (see the ratio anchors at ``FUZZY_CUTOFF``
        above). Utterances shorter than ``MIN_TOKENS`` are never judged —
        short commands ("stopp") must always reach their handlers. References
        are checked per spoken phrase plus the concatenation of the two newest
        phrases, so an echo spanning a sentence boundary still matches without
        building a big vocabulary union that common words could false-match.
        """
        if self.activity_ns <= 0:
            return False
        now_ns = time.time_ns()
        if now_ns - self.activity_ns > int(self.WINDOW_S * 1e9):
            return False
        utterance = self.tokens(text)
        if len(utterance) < self.MIN_TOKENS:
            return False
        ttl_ns = int(self.REF_TTL_S * 1e9)
        fresh = [tokens for _slot, tokens, ref_ns in self._refs if now_ns - ref_ns <= ttl_ns]
        if not fresh:
            return False
        candidates = list(fresh)
        if len(fresh) >= 2:
            candidates.append(fresh[-2] + fresh[-1])
        allowed_novel = len(utterance) // 6
        for reference in candidates:
            matched = sum(
                1
                for token in utterance
                if token in reference
                or difflib.get_close_matches(token, reference, n=1, cutoff=self.FUZZY_CUTOFF)
            )
            if (
                matched / len(utterance) >= self.MIN_OVERLAP
                and (len(utterance) - matched) <= allowed_novel
            ):
                return True
        return False
