"""Unit tests for the ``call-contact`` router tool (Chunk B).

``call-contact`` is the integrator's headline glue: it consumes BOTH frozen
contracts — Contract 1 (``ContactStore.find_by_alias`` -> phone) and Contract 2
(``jarvis.telephony.outbound.place_call`` -> places a real outbound call and
speaks an opening). Both are stubbed here so B is testable before Chunk A/C land.

Risk tier ``ask``: dialing a real person is consequential, so the
ToolExecutor's approval workflow echo-confirms before the call. Telephony being
absent/unconfigured degrades to a clear English no-op pointing at the Telephony
section (cloud-first €5-VPS doctrine).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import pytest

import jarvis.plugins.tool.call_contact as call_contact_mod
from jarvis.plugins.tool.call_contact import CallContactTool


# --------------------------------------------------------------------------- #
# Contract-1 stub
# --------------------------------------------------------------------------- #
@dataclass
class _FakeContact:
    name: str
    slug: str = "christoph"
    emails: list[str] = field(default_factory=list)
    phones: list[str] = field(default_factory=list)

    @property
    def primary_phone(self) -> str | None:
        return self.phones[0] if self.phones else None


class _FakeStore:
    def __init__(self, contacts: list[_FakeContact]) -> None:
        self._by_name = {c.name.strip().lower(): c for c in contacts}

    def find_by_alias(self, query: str) -> _FakeContact | None:
        return self._by_name.get((query or "").strip().lower())


# --------------------------------------------------------------------------- #
# Contract-2 stub
# --------------------------------------------------------------------------- #
class _RecordingPlaceCall:
    """Records the kwargs ``place_call`` was invoked with; returns a call_sid."""

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    def __call__(
        self,
        *,
        to: str,
        opening: str = "",
        account_sid: str,
        auth_token: str,
        from_number: str,
        public_base_url: str,
    ) -> str:
        self.calls.append(
            {
                "to": to,
                "opening": opening,
                "account_sid": account_sid,
                "auth_token": auth_token,
                "from_number": from_number,
                "public_base_url": public_base_url,
            }
        )
        return "CA_test_sid_123"


_VALID_CONFIG = {
    "account_sid": "AC_test",
    "auth_token": "tok_test",
    "from_number": "+4930000000",
    "public_base_url": "https://example.test",
}


def _tool(
    *,
    store: Any,
    place_call: Any,
    config: Any = _VALID_CONFIG,
) -> CallContactTool:
    return CallContactTool(
        store_resolver=lambda: store,
        place_call=place_call,
        call_config_resolver=lambda: config,
    )


# --------------------------------------------------------------------------- #
# Tests
# --------------------------------------------------------------------------- #
def test_risk_tier_is_ask() -> None:
    """Dialing a real person is consequential — ask-tier so the executor
    echo-confirms before the call goes out."""
    assert CallContactTool(store_resolver=lambda: None).risk_tier == "ask"


def test_name_is_call_contact() -> None:
    assert CallContactTool(store_resolver=lambda: None).name == "call-contact"


@pytest.mark.asyncio
async def test_call_resolves_phone_and_places_call() -> None:
    """Happy path: resolve the contact's phone (Contract 1), dial it via
    place_call (Contract 2) with the telephony config, return the call_sid."""
    store = _FakeStore([_FakeContact(name="Christoph", phones=["+4915112345678"])])
    pc = _RecordingPlaceCall()
    result = await _tool(store=store, place_call=pc).execute({"name": "Christoph"}, None)

    assert result.success is True
    assert len(pc.calls) == 1
    assert pc.calls[0]["to"] == "+4915112345678"
    assert pc.calls[0]["account_sid"] == "AC_test"
    assert pc.calls[0]["from_number"] == "+4930000000"
    assert "CA_test_sid_123" in result.output


@pytest.mark.asyncio
async def test_message_becomes_the_spoken_opening() -> None:
    """A user-provided message is what Jarvis speaks first to the callee."""
    store = _FakeStore([_FakeContact(name="Christoph", phones=["+4915112345678"])])
    pc = _RecordingPlaceCall()
    await _tool(store=store, place_call=pc).execute(
        {"name": "Christoph", "message": "Hi Christoph, are we still on for Friday?"},
        None,
    )
    assert pc.calls[0]["opening"] == "Hi Christoph, are we still on for Friday?"


@pytest.mark.asyncio
async def test_default_opening_is_non_empty_without_message() -> None:
    """Without a message the tool still gives the call a spoken opener so the
    callee is not greeted by silence."""
    store = _FakeStore([_FakeContact(name="Christoph", phones=["+4915112345678"])])
    pc = _RecordingPlaceCall()
    await _tool(store=store, place_call=pc).execute({"name": "Christoph"}, None)
    assert pc.calls[0]["opening"].strip() != ""


@pytest.mark.asyncio
async def test_missing_name_is_an_error() -> None:
    pc = _RecordingPlaceCall()
    result = await _tool(store=_FakeStore([]), place_call=pc).execute({}, None)
    assert result.success is False
    assert result.error
    assert pc.calls == []


@pytest.mark.asyncio
async def test_unknown_contact_does_not_dial() -> None:
    store = _FakeStore([_FakeContact(name="Christoph", phones=["+4915112345678"])])
    pc = _RecordingPlaceCall()
    result = await _tool(store=store, place_call=pc).execute({"name": "Mallory"}, None)
    assert result.success is False
    assert pc.calls == []


@pytest.mark.asyncio
async def test_contact_without_phone_does_not_dial() -> None:
    store = _FakeStore([_FakeContact(name="Christoph", emails=["c@example.com"], phones=[])])
    pc = _RecordingPlaceCall()
    result = await _tool(store=store, place_call=pc).execute({"name": "Christoph"}, None)
    assert result.success is False
    assert "phone" in (result.error or "").lower() or "nummer" in (result.error or "").lower()
    assert pc.calls == []


@pytest.mark.asyncio
async def test_store_unavailable_degrades_gracefully() -> None:
    pc = _RecordingPlaceCall()
    tool = CallContactTool(
        store_resolver=lambda: None,
        place_call=pc,
        call_config_resolver=lambda: _VALID_CONFIG,
    )
    result = await tool.execute({"name": "Christoph"}, None)
    assert result.success is False
    assert result.error
    assert pc.calls == []


@pytest.mark.asyncio
async def test_telephony_unconfigured_points_to_section() -> None:
    """No Twilio config (resolver returns None) -> clear English no-op pointing
    at the Telephony section; never a crash, never a dial."""
    store = _FakeStore([_FakeContact(name="Christoph", phones=["+4915112345678"])])
    pc = _RecordingPlaceCall()
    result = await _tool(store=store, place_call=pc, config=None).execute(
        {"name": "Christoph"}, None
    )
    assert result.success is False
    assert "telephony" in (result.error or "").lower()
    assert pc.calls == []


@pytest.mark.asyncio
async def test_place_call_provision_error_is_surfaced_gracefully() -> None:
    """When place_call raises (twilio missing/unconfigured per Contract 2) the
    tool surfaces a clean message, never an unhandled exception."""

    def _raises(**_: Any) -> str:
        raise RuntimeError("twilio is not installed; run pip install '.[telephony]'")

    store = _FakeStore([_FakeContact(name="Christoph", phones=["+4915112345678"])])
    result = await _tool(store=store, place_call=_raises).execute({"name": "Christoph"}, None)
    assert result.success is False
    assert result.error


@pytest.mark.asyncio
async def test_engine_absent_degrades_gracefully(monkeypatch) -> None:
    """When Chunk C is not merged the lazy ``_load_place_call`` import returns
    None — the tool must degrade to a clear English no-op (cloud-first)."""
    monkeypatch.setattr(call_contact_mod, "_load_place_call", lambda: None)
    store = _FakeStore([_FakeContact(name="Christoph", phones=["+4915112345678"])])
    # place_call=None forces the default lazy-load path (which we patched to None).
    tool = CallContactTool(
        store_resolver=lambda: store,
        place_call=None,
        call_config_resolver=lambda: _VALID_CONFIG,
    )
    result = await tool.execute({"name": "Christoph"}, None)
    assert result.success is False
    assert "telephony" in (result.error or "").lower()
