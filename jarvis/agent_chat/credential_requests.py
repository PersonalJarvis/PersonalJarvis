"""Credential cards: an agent asks the person to paste a secret into a secure field.

The card is a sibling of the question card (questions.py) with one rule the
question card does not need: the answer never travels through the chat. The
person's value goes from the card's field to a POST route, from there to the
``save`` callback the asking tool handed over (the agent's credential vault),
and nowhere else. The timeline only records that a credential was asked for
and how the card closed — ``credential_required`` and ``credential_resolved``
carry the variable name, a label and a status, never the value.

The card belongs to the turn that opened it: it closes when the person saves
or declines, after ``CREDENTIAL_TIMEOUT_S`` without an answer, or when the
turn ends. The agent waits on it in slices, like on a question card.
"""

from __future__ import annotations

import asyncio
import inspect
import logging
import time
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from jarvis.agent_chat.events import make_event

log = logging.getLogger(__name__)

__all__ = [
    "CANCELLED",
    "CREDENTIAL_TIMEOUT_S",
    "DECLINED",
    "MAX_REQUESTS_PER_TURN",
    "SAVED",
    "TIMEOUT",
    "CredentialRequests",
    "CredentialSpec",
    "TooManyCredentialRequests",
]

#: How long the card waits for the person to paste the value.
CREDENTIAL_TIMEOUT_S: Final[float] = 600.0

#: Cards per turn: an agent that keeps asking gets a refusal instead.
MAX_REQUESTS_PER_TURN: Final[int] = 3

#: How a card closed.
SAVED: Final[str] = "saved"
DECLINED: Final[str] = "declined"
TIMEOUT: Final[str] = "timeout"
CANCELLED: Final[str] = "cancelled"

_MAX_LABEL: Final[int] = 80
_MAX_DESCRIPTION: Final[int] = 400
_MAX_PLACEHOLDER: Final[int] = 80

Save = Callable[[str], Awaitable[None] | None]


class TooManyCredentialRequests(RuntimeError):
    """This turn already showed ``MAX_REQUESTS_PER_TURN`` credential cards."""


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    """What the card asks for. ``env`` is validated by the asking tool."""

    env: str
    label: str
    description: str = ""
    placeholder: str = ""
    #: The agent already has a value and asks for a replacement.
    replace: bool = False

    @classmethod
    def build(
        cls,
        env: str,
        label: Any,
        description: Any = "",
        placeholder: Any = "",
        *,
        replace: bool = False,
    ) -> CredentialSpec:
        name = _text(label, _MAX_LABEL)
        if not name:
            raise ValueError("label is required: say what the credential is, e.g. 'GitHub token'")
        return cls(
            env=env,
            label=name,
            description=_text(description, _MAX_DESCRIPTION),
            placeholder=_text(placeholder, _MAX_PLACEHOLDER),
            replace=replace,
        )

    def to_payload(self) -> dict[str, Any]:
        return {
            "env": self.env,
            "label": self.label,
            "description": self.description,
            "placeholder": self.placeholder,
            "replace": self.replace,
        }


class _OpenCredential:
    __slots__ = (
        "expired", "result", "save", "saving", "session_id", "spec", "turn_id", "watcher",
    )

    def __init__(
        self,
        session_id: str,
        turn_id: str,
        spec: CredentialSpec,
        save: Save,
        result: asyncio.Future[str],
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.spec = spec
        self.save = save
        self.result = result
        #: A save is in flight: a second click must not store twice.
        self.saving = False
        #: The timeout passed during a save; a failed save then closes the card.
        self.expired = False
        self.watcher: asyncio.Task[None] | None = None

    @property
    def done(self) -> bool:
        return self.result.done()


Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
RunningTurn = Callable[[str], str | None]


class CredentialRequests:
    """The open credential cards of one chat service."""

    def __init__(self, emit: Emit, running_turn: RunningTurn) -> None:
        self._emit = emit
        self._running_turn = running_turn
        self._open: dict[str, _OpenCredential] = {}
        self._asked: dict[tuple[str, str], int] = {}
        self._tasks: set[asyncio.Task[None]] = set()

    async def open(
        self,
        session_id: str,
        spec: CredentialSpec,
        save: Save,
        *,
        asker: str = "",
        timeout_s: float = CREDENTIAL_TIMEOUT_S,
    ) -> str:
        """Show the card in the running turn; returns its request id.

        Raises ``RuntimeError`` without a running turn and
        ``TooManyCredentialRequests`` once the turn used its cards.
        """
        turn_id = self._running_turn(session_id)
        if not turn_id:
            raise RuntimeError("this chat has no running turn to ask in")
        key = (session_id, turn_id)
        if self._asked.get(key, 0) >= MAX_REQUESTS_PER_TURN:
            raise TooManyCredentialRequests(f"this turn already asked {self._asked[key]} times")
        self._asked[key] = self._asked.get(key, 0) + 1
        request_id = uuid.uuid4().hex
        loop = asyncio.get_running_loop()
        card = _OpenCredential(session_id, turn_id, spec, save, loop.create_future())
        self._open[request_id] = card
        await self._emit(
            session_id,
            make_event(
                "credential_required",
                {
                    "turn_id": turn_id,
                    "request_id": request_id,
                    "asker": asker,
                    **spec.to_payload(),
                    "timeout_s": timeout_s,
                    "expires_ms": int(time.time() * 1000 + timeout_s * 1000),
                },
            ),
        )
        card.watcher = asyncio.create_task(
            self._expire(request_id, timeout_s), name=f"agent-chat-credential-{request_id[:8]}"
        )
        return request_id

    async def wait(
        self, session_id: str, request_id: str, timeout_s: float | None = None
    ) -> str | None:
        """How the card closed, or ``None`` while it is still open after ``timeout_s``.

        Raises ``KeyError`` for an unknown or foreign request id.
        """
        card = self._card(session_id, request_id)
        try:
            return await asyncio.wait_for(asyncio.shield(card.result), timeout_s)
        except TimeoutError:  # still open: the caller polls again
            return None

    def spec(self, session_id: str, request_id: str) -> CredentialSpec:
        return self._card(session_id, request_id).spec

    def pending(self, session_id: str) -> list[str]:
        return [rid for rid, c in self._open.items() if c.session_id == session_id and not c.done]

    async def submit(self, session_id: str, request_id: str, value: str) -> bool:
        """Store the person's ``value`` through the card's ``save``.

        False for an unknown, foreign or closed card. A value the vault
        refuses raises ``ValueError`` and leaves the card open for another try.
        """
        card = self._open.get(request_id)
        if card is None or card.session_id != session_id or card.done or card.saving:
            return False
        card.saving = True
        try:
            outcome = card.save(value)
            if inspect.isawaitable(outcome):
                await outcome
        except BaseException:
            card.saving = False
            if card.expired:
                # The field timed out while this save ran: it closes now.
                await self._close(request_id, TIMEOUT)
            raise
        card.saving = False
        await self._close(request_id, SAVED)
        return True

    async def decline(self, session_id: str, request_id: str) -> bool:
        card = self._open.get(request_id)
        if card is None or card.session_id != session_id or card.done:
            return False
        await self._close(request_id, DECLINED)
        return True

    def cancel_session(self, session_id: str) -> None:
        """The turn ended: every card it still shows closes (callable from sync code)."""
        for request_id, card in list(self._open.items()):
            if card.session_id != session_id:
                continue
            self._open.pop(request_id, None)
            if card.done:
                continue
            card.result.set_result(CANCELLED)
            if card.watcher is not None:
                card.watcher.cancel()
            try:
                task = asyncio.get_running_loop().create_task(
                    self._record(card, request_id, CANCELLED)
                )
            except RuntimeError:  # no loop (a sync test double): nothing to show it in
                log.debug("agent chat: credential card %s closed without a loop", request_id)
                continue
            self._tasks.add(task)
            task.add_done_callback(self._tasks.discard)
        for key in [k for k in self._asked if k[0] == session_id]:
            self._asked.pop(key, None)

    def _card(self, session_id: str, request_id: str) -> _OpenCredential:
        card = self._open.get(request_id)
        if card is None or card.session_id != session_id:
            raise KeyError(request_id)
        return card

    async def _expire(self, request_id: str, timeout_s: float) -> None:
        await asyncio.sleep(timeout_s)
        card = self._open.get(request_id)
        if card is not None and card.saving:
            # A save in flight finishes first; a failed one closes the card itself.
            card.expired = True
            return
        if card is not None and not card.done:
            log.info("agent chat: credential card %s timed out", request_id)
            await self._close(request_id, TIMEOUT)

    async def _close(self, request_id: str, status: str) -> None:
        card = self._open.get(request_id)
        if card is None or card.done:
            return
        card.result.set_result(status)
        watcher = card.watcher
        if watcher is not None and watcher is not asyncio.current_task():
            watcher.cancel()
        await self._record(card, request_id, status)

    async def _record(self, card: _OpenCredential, request_id: str, status: str) -> None:
        try:
            await self._emit(
                card.session_id,
                make_event(
                    "credential_resolved",
                    {
                        "turn_id": card.turn_id,
                        "request_id": request_id,
                        "env": card.spec.env,
                        "status": status,
                    },
                ),
            )
        except Exception:  # noqa: BLE001 — the outcome already reached the waiter
            log.warning(
                "agent chat: could not record credential card %s", request_id, exc_info=True
            )
