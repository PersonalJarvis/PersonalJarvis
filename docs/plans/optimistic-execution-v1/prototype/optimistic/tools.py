"""Tool implementations for the Optimistic Execution prototype.

Two categories, strictly separated (AD-OE3, AD-OE4):

- **DumbTool**: local scripts executed in-process, synchronously, in milliseconds.
  They never wake the HeavyDutyWorker.
- **SmartTool**: complex MCP calls executed asynchronously by the background worker.
  They simulate network latency with asyncio.sleep and may raise MissingInfoError
  to trigger the Oops correction protocol.

Factory helpers ``get_dumb_tool`` and ``get_smart_tool`` are used by the router
and worker so neither component needs to construct tools directly.
"""
from __future__ import annotations

import asyncio
import re

from optimistic.events import CorrectionReason

# ---------------------------------------------------------------------------
# Error types
# ---------------------------------------------------------------------------


class MissingInfoError(Exception):
    """Raised by a SmartTool when required information is absent from context.

    The ``reason`` attribute drives the Oops phrasing; ``detail`` names what is
    missing (e.g. the recipient name) so the OopsProtocol can surface it to the
    user organically.
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
    The method is ``async def`` for interface uniformity with SmartTool, but it
    must not perform any real async work.
    """

    def __init__(self, name: str) -> None:
        self.name = name

    async def fire(self, command: str) -> str:
        """Execute the local action and return a confirmation string immediately."""
        # Intentionally no await — dumb tools are instant.
        return f"[{self.name}] erledigt: {command}"


# ---------------------------------------------------------------------------
# SmartTool — async MCP simulation
# ---------------------------------------------------------------------------


def _extract_recipient(command: str) -> str | None:
    """Return the first capitalised word that is NOT the leading word of the sentence.

    The spec defines 'recipient' as the first capitalised word that is not the
    first word of the command (the leading word is likely a verb like "Schreib").
    Returns ``None`` if no such word is found.

    Examples:
      "Schreib Max eine Mail" → "Max"
      "Schreib Anna kurz" → "Anna"
      "mail senden" → None  (no capitalised word after the first)
    """
    words = command.split()
    if len(words) < 2:
        return None
    # Walk the words starting from index 1 (skip the leading word).
    for word in words[1:]:
        # Strip punctuation for the check, but return the clean word.
        clean = re.sub(r"[^A-Za-zÄÖÜäöüß]", "", word)
        if clean and clean[0].isupper():
            return clean
    return None


class SmartTool:
    """A tool that simulates an asynchronous MCP round-trip.

    The worker calls ``execute`` and awaits the result; the Talker is never
    blocked (AD-OE4: only the worker issues MCP calls).

    Args:
        name: Logical tool name (e.g. "gmail", "calendar", "drive").
        work_seconds: Simulated MCP latency. Default 0.15 s keeps tests fast
            while still being realistic enough for latency probes.
    """

    def __init__(self, name: str, *, work_seconds: float = 0.15) -> None:
        self.name = name
        self.work_seconds = work_seconds

    async def execute(self, command: str, context: dict) -> str:
        """Simulate the MCP round-trip, then return a result string.

        For the ``gmail`` tool: extracts the recipient name from ``command``
        and raises ``MissingInfoError(MISSING_INFO, ...)`` when the recipient is
        not found (case-insensitively) in ``context["contacts"]``.

        Optional: if the command contains the literal token ``"flaky"``, raises a
        generic ``RuntimeError`` to exercise the worker's retry path.
        """
        # Simulate async network/MCP latency.
        await asyncio.sleep(self.work_seconds)

        # Optional flaky-path: lets the worker exercise its retry logic.
        if "flaky" in command.lower():
            raise RuntimeError("simulated transient MCP failure (flaky)")

        # Canonical gmail / missing-info scenario.
        if self.name == "gmail":
            recipient = _extract_recipient(command)
            if recipient is not None:
                contacts: dict = context.get("contacts", {})
                # Case-insensitive lookup.
                contacts_lower = {k.lower(): v for k, v in contacts.items()}
                if recipient.lower() not in contacts_lower:
                    raise MissingInfoError(
                        CorrectionReason.MISSING_INFO,
                        f"no email address on file for {recipient}",
                    )

        return f"[{self.name}] '{command}' gesendet"


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------


def get_dumb_tool(name: str) -> DumbTool:
    """Return a DumbTool instance for the given name."""
    return DumbTool(name)


def get_smart_tool(name: str | None) -> SmartTool:
    """Return a SmartTool instance.

    If ``name`` is ``None``, returns a generic SmartTool named ``"generic"``.
    """
    return SmartTool(name if name is not None else "generic")
