"""User-owned secure fields whose values never enter the chat event stream.

A field stays open across turn completion, interruption and reconnect until
its owner saves a validated value or explicitly cancels. Only secret-free
metadata is persisted, allowing the service to rebuild fields after a restart.
"""

from __future__ import annotations

import asyncio
import inspect
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

from jarvis.agent_chat.events import make_event

# Retained for callers that used the old argument. It no longer expires fields.
CREDENTIAL_TIMEOUT_S: Final[float] = 0.0
MAX_REQUESTS_PER_TURN: Final[int] = 3
MAX_PENDING_PER_SESSION: Final[int] = 12
MAX_PENDING_REQUESTS: Final[int] = 256
MAX_CLOSED_REQUESTS: Final[int] = 256
SAVED: Final[str] = "saved"
DECLINED: Final[str] = "declined"
TIMEOUT: Final[str] = "timeout"  # Historical receipts only.
CANCELLED: Final[str] = "cancelled"  # Historical receipts only.

Save = Callable[[str], Awaitable[None] | None]
Emit = Callable[[str, dict[str, Any]], Awaitable[None]]
RunningTurn = Callable[[str], str | None]
History = Callable[[str], list[dict[str, Any]]]
RestoreSave = Callable[[str, "CredentialSpec"], Save | None]


class TooManyCredentialRequests(RuntimeError):
    """The turn or session already has enough credential fields."""


class CredentialRequestBusy(RuntimeError):
    """Validation or storage is running; cancellation must not race its commit."""


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


@dataclass(frozen=True, slots=True)
class CredentialSpec:
    """What the field asks for. ``env`` is validated by the asking tool."""

    env: str
    label: str
    description: str = ""
    placeholder: str = ""
    replace: bool = False

    @classmethod
    def build(
        cls, env: str, label: Any, description: Any = "", placeholder: Any = "",
        *, replace: bool = False,
    ) -> CredentialSpec:
        name = _text(label, 80)
        if not name:
            raise ValueError("label is required: say what the credential is, e.g. 'GitHub token'")
        return cls(env, name, _text(description, 400), _text(placeholder, 80), replace)

    def to_payload(self) -> dict[str, Any]:
        return {
            "env": self.env, "label": self.label, "description": self.description,
            "placeholder": self.placeholder, "replace": self.replace,
        }


class _OpenCredential:
    def __init__(
        self, session_id: str, turn_id: str, spec: CredentialSpec, save: Save,
    ) -> None:
        self.session_id = session_id
        self.turn_id = turn_id
        self.spec = spec
        self.save = save
        self.result: asyncio.Future[str] = asyncio.get_running_loop().create_future()
        self.saving: asyncio.Task[bool] | None = None

    @property
    def done(self) -> bool:
        return self.result.done()


class CredentialRequests:
    """Bounded pending fields, independent of the lifetime of any agent turn."""

    def __init__(
        self, emit: Emit, running_turn: RunningTurn, *, history: History | None = None,
        restore_save: RestoreSave | None = None,
    ) -> None:
        self._emit = emit
        self._running_turn = running_turn
        self._history = history
        self._restore_save = restore_save
        self._open: dict[str, _OpenCredential] = {}
        self._asked: dict[tuple[str, str], int] = {}
        self._restored: set[str] = set()
        self._restore_lock = asyncio.Lock()

    async def restore(self, session_id: str) -> None:
        if session_id in self._restored or self._history is None or self._restore_save is None:
            return
        async with self._restore_lock:
            if session_id in self._restored:
                return
            events = await asyncio.to_thread(self._history, session_id)
            self._restore_events(session_id, events)
            self._restored.add(session_id)

    def _restore_events(self, session_id: str, events: list[dict[str, Any]]) -> None:
        pending: dict[str, dict[str, Any]] = {}
        statuses: dict[str, str] = {}
        for event in events:
            payload = event.get("payload") or {}
            rid = str(payload.get("request_id") or "")
            if not rid:
                continue
            if event.get("kind") == "credential_required":
                pending[rid] = payload
            elif event.get("kind") == "credential_resolved":
                statuses[rid] = str(payload.get("status") or CANCELLED)
        for rid, payload in pending.items():
            if rid in self._open:
                continue
            spec = CredentialSpec.build(
                str(payload.get("env") or ""), payload.get("label"),
                payload.get("description"), payload.get("placeholder"),
                replace=bool(payload.get("replace")),
            )
            assert self._restore_save is not None
            save = self._restore_save(session_id, spec)
            if save is not None:
                card = self._open[rid] = _OpenCredential(
                    session_id, str(payload.get("turn_id") or ""), spec, save,
                )
                if rid in statuses:
                    card.result.set_result(statuses[rid])

    async def open(
        self, session_id: str, spec: CredentialSpec, save: Save, *, asker: str = "",
        timeout_s: float = CREDENTIAL_TIMEOUT_S,
    ) -> str:
        """Show a field; legacy ``timeout_s`` deliberately has no closing effect."""
        await self.restore(session_id)
        turn_id = self._running_turn(session_id)
        if not turn_id:
            raise RuntimeError("this chat has no running turn to ask in")
        # Repeated asks for the same variable reuse the person's existing field.
        for rid, card in self._open.items():
            if card.session_id == session_id and not card.done and card.spec.env == spec.env:
                return rid
        key = (session_id, turn_id)
        if self._asked.get(key, 0) >= MAX_REQUESTS_PER_TURN:
            raise TooManyCredentialRequests("this turn already asked for three credentials")
        if len(self.pending(session_id)) >= MAX_PENDING_PER_SESSION or sum(
            not card.done for card in self._open.values()
        ) >= MAX_PENDING_REQUESTS:
            raise TooManyCredentialRequests("complete or cancel existing credential fields first")
        rid = uuid.uuid4().hex
        self._open[rid] = _OpenCredential(session_id, turn_id, spec, save)
        try:
            await self._emit(session_id, make_event("credential_required", {
                "turn_id": turn_id, "request_id": rid, "asker": asker,
                **spec.to_payload(), "timeout_s": 0, "expires_ms": 0,
            }))
        except BaseException:
            self._open.pop(rid, None)
            raise
        self._asked[key] = self._asked.get(key, 0) + 1
        return rid

    async def wait(
        self, session_id: str, request_id: str, timeout_s: float | None = None,
    ) -> str | None:
        await self.restore(session_id)
        card = self._card(session_id, request_id)
        try:
            return await asyncio.wait_for(asyncio.shield(card.result), timeout_s)
        except TimeoutError:  # Only the caller's polling slice ends.
            return None

    def spec(self, session_id: str, request_id: str) -> CredentialSpec:
        return self._card(session_id, request_id).spec

    def pending(self, session_id: str) -> list[str]:
        return [rid for rid, c in self._open.items() if c.session_id == session_id and not c.done]

    async def submit(self, session_id: str, request_id: str, value: str) -> bool:
        await self.restore(session_id)
        card = self._open.get(request_id)
        if card is None or card.session_id != session_id:
            return False
        if card.done:
            return card.result.result() == SAVED
        if card.saving is not None:
            raise CredentialRequestBusy("The credential is still being checked. Please wait.")
        # A dropped HTTP connection cannot interrupt a storage transaction.
        card.saving = asyncio.create_task(self._save(request_id, card, value))
        card.saving.add_done_callback(self._observe_save)
        return await asyncio.shield(card.saving)

    @staticmethod
    def _observe_save(task: asyncio.Task[bool]) -> None:
        if not task.cancelled():
            task.exception()  # Retrieve failures even if the HTTP caller disconnected.

    async def _save(self, request_id: str, card: _OpenCredential, value: str) -> bool:
        try:
            outcome = card.save(value)
            if inspect.isawaitable(outcome):
                await outcome
            await self._close(request_id, SAVED)
            return True
        finally:
            card.saving = None

    async def decline(self, session_id: str, request_id: str) -> bool:
        await self.restore(session_id)
        card = self._open.get(request_id)
        if card is None or card.session_id != session_id:
            return False
        if card.done:
            return card.result.result() == DECLINED
        if card.saving is not None:
            raise CredentialRequestBusy("The credential is still being checked. Please wait.")
        card.saving = asyncio.create_task(self._decline(request_id, card))
        card.saving.add_done_callback(self._observe_save)
        return await asyncio.shield(card.saving)

    async def _decline(self, request_id: str, card: _OpenCredential) -> bool:
        try:
            await self._close(request_id, DECLINED)
            return True
        finally:
            card.saving = None

    def finish_turn(self, session_id: str) -> None:
        """Release turn accounting without closing the person's pending fields."""
        for key in [key for key in self._asked if key[0] == session_id]:
            self._asked.pop(key, None)
        closed = [rid for rid, card in self._open.items() if card.done]
        for rid in closed[:-MAX_CLOSED_REQUESTS]:
            self._open.pop(rid, None)

    async def discard_session(self, session_id: str) -> None:
        """Explicit session deletion waits for storage before forgetting fields."""
        saves = [
            card.saving for card in self._open.values()
            if card.session_id == session_id and card.saving is not None
        ]
        if saves:
            # Save errors have already reached their HTTP caller; deletion still
            # removes their now-unneeded metadata after all writes have stopped.
            await asyncio.gather(*(asyncio.shield(task) for task in saves), return_exceptions=True)
        for rid, card in list(self._open.items()):
            if card.session_id == session_id:
                if not card.done:
                    card.result.set_result(CANCELLED)
                self._open.pop(rid, None)
        self._restored.discard(session_id)
        self.finish_turn(session_id)

    def _card(self, session_id: str, request_id: str) -> _OpenCredential:
        card = self._open.get(request_id)
        if card is None or card.session_id != session_id:
            raise KeyError(request_id)
        return card

    async def _close(self, request_id: str, status: str) -> None:
        card = self._open.get(request_id)
        if card is None or card.done:
            return
        # Persist before reporting success. A failed receipt remains retryable,
        # so a reload cannot resurrect a field that was reported closed.
        await self._emit(card.session_id, make_event("credential_resolved", {
            "turn_id": card.turn_id, "request_id": request_id,
            "env": card.spec.env, "status": status,
        }))
        card.result.set_result(status)
