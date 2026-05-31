"""Tool implementations for the Optimistic Execution prototype (v2).

In v2 the heavy async work is done by the LLM (``optimistic/llm.py``), so
``SmartTool`` is removed. The worker now calls ``llm.complete`` directly and
uses ``check_missing_info`` for the gmail recipient pre-check.

What remains:
- ``DumbTool``       — local, in-process, instant (AD-OE3: false-spawn rate = 0)
- ``MissingInfoError`` — kept for backward compat (oops.py may import it)
- ``check_missing_info`` — extracted recipient logic, now a pure function
- ``get_dumb_tool``  — factory helper

What was removed:
- ``SmartTool``       — superseded by ``llm.complete``
- ``get_smart_tool``  — superseded
"""
from __future__ import annotations

import re

from optimistic.events import CorrectionReason

# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class MissingInfoError(Exception):
    """Raised when required information is absent from context.

    Kept for any code that still catches it (e.g. oops.py). The worker's
    gmail pre-check now uses ``check_missing_info`` instead of raising.
    """

    def __init__(self, reason: CorrectionReason, detail: str) -> None:
        super().__init__(detail)
        self.reason = reason
        self.detail = detail


# ---------------------------------------------------------------------------
# DumbTool — local, in-process, instant
# ---------------------------------------------------------------------------


class DumbTool:
    """A tool that executes a trivial local action in-process.

    No I/O, no awaiting, no worker involved (AD-OE3: false-spawn rate = 0).
    ``async def`` for interface uniformity, but must not perform any real
    async work.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    async def fire(self, command: str) -> str:
        """Execute the local action and return a confirmation string immediately."""
        # Intentionally no await — dumb tools are instant.
        return f"[{self.name}] erledigt: {command}"


# ---------------------------------------------------------------------------
# Missing-info check (gmail recipient logic)
# ---------------------------------------------------------------------------


def _extract_capitalised_name(command: str) -> str | None:
    """Return the first capitalised word after the leading word of the command.

    The leading word (typically a verb like "Schreib") is skipped. Returns
    ``None`` when no such word exists.

    Examples:
        "Schreib Max eine Mail"        → "Max"
        "Schreib Anna kurz"            → "Anna"
        "mail senden"                  → None  (no capitalised word after first)
    """
    words = command.split()
    if len(words) < 2:
        return None
    for word in words[1:]:
        # Strip punctuation before the capital check.
        clean = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", word)
        if clean and clean[0].isupper():
            return clean
    return None


def check_missing_info(
    command: str,
    context: dict,
) -> tuple[CorrectionReason, str] | None:
    """Check whether required contact info is missing for a gmail command.

    Extracts the first capitalised name after the leading word of ``command``.
    If found and the name is NOT present (case-insensitively) in
    ``context.get("contacts", {})``, returns a ``(MISSING_INFO, detail)``
    tuple so the worker can publish ``WorkerCorrectionNeeded``.

    Returns ``None`` when:
    - No capitalised name is found in the command, or
    - The name IS present in contacts (case-insensitive match).

    This is a pure function — no I/O, no async, no side effects.
    """
    name = _extract_capitalised_name(command)
    if name is None:
        return None

    contacts: dict = context.get("contacts", {})
    contacts_lower = {k.lower(): v for k, v in contacts.items()}
    if name.lower() not in contacts_lower:
        return (
            CorrectionReason.MISSING_INFO,
            f"no email address on file for {name}",
        )
    return None


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def get_dumb_tool(name: str) -> DumbTool:
    """Return a DumbTool instance for the given name."""
    return DumbTool(name)
