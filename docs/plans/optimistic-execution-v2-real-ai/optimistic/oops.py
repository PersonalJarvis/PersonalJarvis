"""OopsProtocol — per-session "Oops" error-handling safety net (v2).

When the background Heavy-Duty Worker hits a failure it cannot recover from
silently, it emits a ``WorkerCorrectionNeeded`` event. This module intercepts
that event INVISIBLY (no immediate speech), buffers it per session_id, and
surfaces organic German spoken corrections ONLY when the VAD endpoint signals a
turn boundary — never interrupting the user mid-utterance (AD-OE5).

Architecture note (AD-OE6):
    Zero silent drops. Every worker failure must either be silently retried by
    the worker itself (NETWORK_ERROR, one retry) OR arrive here as a
    WorkerCorrectionNeeded. There is no code path that drops a failure quietly.

v2 change from v1:
    The single global buffer and speaking-flag are replaced by per-session_id
    dicts. The VAD endpoints in vad.py call flush(session_id) at turn-boundary
    time; the speaking flag is owned entirely by VADRegistry (vad.py).

Standard-library only — no third-party imports, no ``import jarvis.*``.
"""
from __future__ import annotations

import logging
import re
from collections import defaultdict

from optimistic.events import CorrectionReason, WorkerCorrectionNeeded

_log = logging.getLogger("optimistic.oops")


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
# Matches a word starting with an uppercase letter that is NOT the very
# first word (verbs / articles at position 0 are excluded).
_CAPITALISED_NAME_PAT = re.compile(r"(?<!\A)\b([A-ZÄÖÜ][a-zäöüß]{1,})\b")


class OopsProtocol:
    """Invisible per-session error-injection + turn-boundary voice correction.

    Lifecycle (v2):
        1. ``bus.subscribe(WorkerCorrectionNeeded, self._on_correction)`` wires
           the handler on construction.
        2. ``_on_correction(ev)`` appends ``ev`` to the per-``session_id``
           buffer — context injection, no speech.
        3. ``pending(session_id)`` / ``injected_context(session_id)`` let the
           orchestrator inspect buffered corrections without consuming them.
        4. ``flush(session_id)`` is called by the VAD endpoint at turn-boundary
           time; it drains the buffer, builds organic phrases, clears the
           session buffer, and returns the phrase list to the orchestrator for
           SSE delivery or TTS.
    """

    def __init__(self, bus) -> None:
        # Maps session_id -> list of pending WorkerCorrectionNeeded events.
        self._buffers: dict[str, list[WorkerCorrectionNeeded]] = defaultdict(list)
        bus.subscribe(WorkerCorrectionNeeded, self._on_correction)

    # ------------------------------------------------------------------
    # Public API — state inspection
    # ------------------------------------------------------------------

    def pending(self, session_id: str = "default") -> list[WorkerCorrectionNeeded]:
        """Return a snapshot (copy) of buffered corrections for ``session_id``."""
        return list(self._buffers[session_id])

    def injected_context(self, session_id: str = "default") -> list[str]:
        """One internal machine-readable context line per pending correction.

        These lines are fed into the Talker's context window (prompt injection)
        so the router-brain can reason about pending failures. They are NOT
        spoken and NOT scrubbed.
        """
        return [
            f"[pending correction: {ev.reason.value}] {ev.detail}"
            for ev in self._buffers[session_id]
        ]

    # ------------------------------------------------------------------
    # Public API — turn-boundary action
    # ------------------------------------------------------------------

    def flush(self, session_id: str = "default") -> list[str]:
        """Drain the buffer for ``session_id`` and return scrubbed German phrases.

        Called by the VAD endpoint (vad.py) when a turn boundary is detected for
        the given session. Returns one phrase per buffered correction, then clears
        that session's buffer. Other sessions are not affected.
        """
        pending = self._buffers[session_id]
        phrases = [self.phrase(ev) for ev in pending]
        self._buffers[session_id] = []
        return phrases

    # ------------------------------------------------------------------
    # Phrase generation
    # ------------------------------------------------------------------

    def phrase(self, ev: WorkerCorrectionNeeded) -> str:
        """Build an organic German correction phrase for ``ev``, then scrub it.

        The phrase must sound natural in spoken German — no raw enum values,
        no tool names, no markdown. The scrubber is a final safety net.
        """
        raw = self._build_raw_phrase(ev)
        return self._scrub(raw)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _on_correction(self, ev: WorkerCorrectionNeeded) -> None:
        """Bus handler: appends the event to the per-session pending buffer.

        Invisible injection into the Talker context — must NOT speak and must
        NOT raise (AP-18: broken subscribers must never propagate).
        """
        try:
            self._buffers[ev.session_id].append(ev)
        except Exception:  # noqa: BLE001
            # Swallow to comply with AP-18 (subscriber exceptions never propagate).
            _log.debug("OopsProtocol._on_correction swallowed an exception", exc_info=True)

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
                f"Kurzer Nachtrag zur Mail an {name}: "
                f"mir fehlt noch seine E-Mail-Adresse. "
                f"Hast du die kurz für mich?"
            )
        # No recognisable name — generic fallback
        return (
            "Ich brauche noch eine Information, um die Aufgabe abzuschließen. "
            "Kannst du mir kurz helfen?"
        )

    @staticmethod
    def _phrase_auth_required(ev: WorkerCorrectionNeeded) -> str:
        """AUTH_REQUIRED: inform the user that authorisation is needed."""
        return (
            "Ich brauche noch kurz deine Freigabe, um das erledigen zu können. "
            "Könntest du dich einmal kurz einloggen?"
        )

    @staticmethod
    def _phrase_network_error(ev: WorkerCorrectionNeeded) -> str:
        """NETWORK_ERROR: note the transient problem and suggest retrying."""
        return (
            "Ich hatte kurz ein Verbindungsproblem und konnte die Aufgabe "
            "leider nicht abschließen. Soll ich es gleich noch einmal versuchen?"
        )

    @staticmethod
    def _phrase_fatal(ev: WorkerCorrectionNeeded) -> str:
        """FATAL: brief, warm apology — the task could not be completed."""
        return (
            "Es tut mir leid — bei dieser Aufgabe ist leider etwas Unerwartetes "
            "passiert und ich konnte sie nicht zu Ende bringen. "
            "Magst du es mir noch einmal sagen?"
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
        stripped = text.strip()
        match = _CAPITALISED_NAME_PAT.search(stripped)
        if match:
            return match.group(1)
        # Fallback: scan all words for a capitalised one, skip the very first.
        words = stripped.split()
        for word in words[1:]:
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
