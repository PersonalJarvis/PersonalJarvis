"""OopsProtocol — the "Oops" error-handling safety net for Optimistic Execution.

When the background Heavy-Duty Worker hits a failure it cannot recover from
silently, it emits a ``WorkerCorrectionNeeded`` event. This module intercepts
that event INVISIBLY (no immediate speech), injects it into the Talker context
window, and surfaces an organic German spoken correction ONLY at the next
Silero-VAD turn-boundary — never interrupting the user mid-utterance (AD-OE5).

Architecture note (AD-OE6):
    Zero silent drops. Every worker failure must either be silently retried by
    the worker itself (NETWORK_ERROR, one retry) OR arrive here as a
    WorkerCorrectionNeeded. There is no code path that drops a failure quietly.

Standard-library only — no third-party imports, no ``import jarvis.*``.
"""
from __future__ import annotations

import re
from typing import TYPE_CHECKING

from optimistic.events import CorrectionReason, WorkerCorrectionNeeded

if TYPE_CHECKING:
    pass


# ---------------------------------------------------------------------------
# Scrub patterns — regex only, AP-11: no LLM on the voice path
# ---------------------------------------------------------------------------

# Tool names that must never reach the TTS engine.
_TOOL_NAME_PAT = re.compile(
    r"\b(gmail|calendar|drive|mcp)\b", re.IGNORECASE
)
# Markdown formatting tokens.
_MARKDOWN_PAT = re.compile(r"[`*]+")
# Runs of whitespace (including those left behind after token removal).
_WHITESPACE_PAT = re.compile(r"\s{2,}")

# Pattern to extract a capitalised proper name from a detail/command string.
# Matches a word that starts with an uppercase letter and is not the very
# first word (which is usually a verb or article, not a recipient name).
_CAPITALISED_NAME_PAT = re.compile(r"(?<!\A)\b([A-ZÄÖÜ][a-zäöüß]{1,})\b")  # i18n-allow: speech input vocabulary pattern DE


class OopsProtocol:
    """Invisible error-injection + turn-boundary voice correction.

    Lifecycle:
        1. ``bus.subscribe(WorkerCorrectionNeeded, self._on_correction)`` wires
           the handler on construction.
        2. ``_on_correction(ev)`` appends to ``_pending`` — context injection,
           no speech.
        3. ``is_user_speaking()`` / ``set_user_speaking()`` track VAD state so
           corrections never fire mid-utterance.
        4. ``vad_turn_boundary()`` is called by the Talker at the end of every
           user turn; it drains ``_pending``, builds organic phrases, clears
           the buffer, and returns the phrase list to the Talker for TTS.
    """

    def __init__(self, bus) -> None:
        self._pending: list[WorkerCorrectionNeeded] = []
        self._user_speaking: bool = False
        bus.subscribe(WorkerCorrectionNeeded, self._on_correction)

    # ------------------------------------------------------------------
    # Public API — state inspection
    # ------------------------------------------------------------------

    @property
    def pending(self) -> list[WorkerCorrectionNeeded]:
        """Corrections that have been injected but not yet spoken."""
        return self._pending

    def is_user_speaking(self) -> bool:
        """True when the VAD reports the user is currently producing speech."""
        return self._user_speaking

    def set_user_speaking(self, speaking: bool) -> None:
        """Called by the Talker when the VAD raises or drops the speech flag."""
        self._user_speaking = speaking

    def injected_context(self) -> list[str]:
        """One internal context line per pending correction.

        These lines are fed into the Talker's context *window* (prompt
        injection) so the router-brain can reason about pending failures.
        They are NOT spoken and NOT scrubbed — they are machine-readable
        diagnostic strings.
        """
        return [
            f"[pending correction: {ev.reason.value}] {ev.detail}"
            for ev in self._pending
        ]

    # ------------------------------------------------------------------
    # Public API — turn-boundary actions
    # ------------------------------------------------------------------

    def vad_turn_boundary(self) -> list[str]:
        """End-of-user-turn signal (Silero VAD silence boundary).

        Returns a list of scrubbed, organic German correction phrases — one
        per pending correction — and clears the pending buffer. Also resets
        ``_user_speaking`` to False because the turn is over.

        The Talker feeds the returned strings to TTS in order.
        """
        phrases = [self.phrase(ev) for ev in self._pending]
        self._pending = []
        self._user_speaking = False
        return phrases

    # ------------------------------------------------------------------
    # Phrase generation
    # ------------------------------------------------------------------

    def phrase(self, ev: WorkerCorrectionNeeded) -> str:
        """Build an organic German correction phrase for ``ev``, then scrub it.

        The phrase must sound natural in spoken German — no raw enum values,
        no tool names, no markdown. The scrubber is a final safety net.

        Reason-specific phrasing:

        MISSING_INFO
            Extract the recipient/subject name from ``ev.detail`` (or
            ``ev.command`` as fallback) and produce a polite, conversational
            clarification request.

        AUTH_REQUIRED
            Inform the user that a login or permission is needed.

        NETWORK_ERROR
            Briefly note that a connection problem occurred and ask the user
            to try again shortly.

        FATAL
            A brief, warm apology — something went wrong and the task could
            not be completed.
        """
        raw = self._build_raw_phrase(ev)
        return self._scrub(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _on_correction(self, ev: WorkerCorrectionNeeded) -> None:
        """Bus handler: appends the event to the pending buffer.

        This IS the "inject the invisible event into the Talker context" step.
        It must NOT speak. It must NOT raise (AP-18: broken subscribers must
        never propagate).
        """
        self._pending.append(ev)

    def _build_raw_phrase(self, ev: WorkerCorrectionNeeded) -> str:
        """Construct the unscrubbed organic German phrase for ``ev``."""
        if ev.reason is CorrectionReason.MISSING_INFO:
            return self._phrase_missing_info(ev)
        if ev.reason is CorrectionReason.AUTH_REQUIRED:
            return self._phrase_auth_required(ev)
        if ev.reason is CorrectionReason.NETWORK_ERROR:
            return self._phrase_network_error(ev)
        # FATAL — and any future reasons default here
        return self._phrase_fatal(ev)

    def _phrase_missing_info(self, ev: WorkerCorrectionNeeded) -> str:
        """MISSING_INFO: ask the user for the missing piece, naming the recipient."""
        name = self._extract_recipient(ev.detail) or self._extract_recipient(ev.command)
        if name:
            return (
                f"Kurzer Nachtrag zur Mail an {name}: "  # i18n-allow: product voice output DE
                f"mir fehlt noch seine E-Mail-Adresse. "  # i18n-allow: product voice output DE
                f"Hast du die kurz für mich?"  # i18n-allow: product voice output DE
            )
        # No recognisable name — generic fallback
        return (
            "Ich brauche noch eine Information, um die Aufgabe abzuschließen. "  # i18n-allow: product voice output DE
            "Kannst du mir kurz helfen?"  # i18n-allow: product voice output DE
        )

    @staticmethod
    def _phrase_auth_required(ev: WorkerCorrectionNeeded) -> str:
        """AUTH_REQUIRED: inform the user that authorisation is needed."""
        return (
            "Ich brauche noch kurz deine Freigabe, um das erledigen zu können. "  # i18n-allow: product voice output DE
            "Könntest du dich einmal kurz einloggen?"  # i18n-allow: product voice output DE
        )

    @staticmethod
    def _phrase_network_error(ev: WorkerCorrectionNeeded) -> str:
        """NETWORK_ERROR: note the transient problem and suggest retrying."""
        return (
            "Ich hatte kurz ein Verbindungsproblem und konnte die Aufgabe "  # i18n-allow: product voice output DE
            "leider nicht abschließen. Soll ich es gleich noch einmal versuchen?"  # i18n-allow: product voice output DE
        )

    @staticmethod
    def _phrase_fatal(ev: WorkerCorrectionNeeded) -> str:
        """FATAL: brief, warm apology — the task could not be completed."""
        return (
            "Es tut mir leid — bei dieser Aufgabe ist leider etwas Unerwartetes "  # i18n-allow: product voice output DE
            "passiert und ich konnte sie nicht zu Ende bringen. "  # i18n-allow: product voice output DE
            "Magst du es mir noch einmal sagen?"  # i18n-allow: product voice output DE
        )

    @staticmethod
    def _extract_recipient(text: str) -> str | None:
        """Extract the first capitalised proper name from ``text``.

        Searches for a word beginning with an uppercase letter that is NOT the
        first word of the text (verbs / articles at position 0 are excluded).
        Returns the name with its original capitalisation, or None.
        """
        if not text:
            return None
        # Strip any leading whitespace so position 0 is the first real word.
        stripped = text.strip()
        match = _CAPITALISED_NAME_PAT.search(stripped)
        if match:
            return match.group(1)
        # Fallback: scan all words for a capitalised one, skip the very first.
        words = stripped.split()
        for word in words[1:]:
            # Remove punctuation tails
            clean = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", word)
            if clean and clean[0].isupper():
                return clean
        return None

    @staticmethod
    def _scrub(text: str) -> str:
        """Regex-only voice scrubber (AP-11: no LLM call on the voice path).

        Rules applied in order:
        1. Remove markdown backticks and asterisks.
        2. Remove tool-name tokens (gmail, calendar, drive, mcp) — case-insensitive.
        3. Collapse runs of whitespace to a single space and strip.
        """
        text = _MARKDOWN_PAT.sub("", text)
        text = _TOOL_NAME_PAT.sub("", text)
        text = _WHITESPACE_PAT.sub(" ", text)
        return text.strip()
