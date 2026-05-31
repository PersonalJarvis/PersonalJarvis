"""TelephonyManager registry + ring buffer + five-layer status guard tests."""

from __future__ import annotations

import time

import pytest
from pydantic import ValidationError

from jarvis.telephony.status import CallRecord, TelephonyManager


def test_call_record_rejects_unknown_status():
    with pytest.raises(ValidationError):
        CallRecord(call_sid="CA1", status="bananas")


def test_call_record_to_api_uses_from_to_keys():
    rec = CallRecord(
        call_sid="CA1",
        from_number="+4930",
        to_number="+4940",
        status="completed",
        turns=3,
        duration_s=12.345,
    )
    api = rec.to_api()
    assert api["from"] == "+4930"
    assert api["to"] == "+4940"
    assert api["duration_s"] == 12.34 or api["duration_s"] == 12.35
    assert api["status"] == "completed"
    assert api["turns"] == 3


def test_pending_secret_handshake():
    mgr = TelephonyManager()
    mgr.register_pending("CA1", "sekret", from_number="+49", to_number="+1")
    # wrong secret -> None
    assert mgr.consume_pending("CA1", "nope") is None
    # right secret -> record returned and removed
    pending = mgr.consume_pending("CA1", "sekret")
    assert pending is not None
    assert pending.from_number == "+49"
    assert mgr.consume_pending("CA1", "sekret") is None  # one-shot


def test_pending_eviction_of_stale_entries():
    mgr = TelephonyManager(pending_ttl_s=0.0)
    mgr.register_pending("CA_old", "x")
    time.sleep(0.01)
    mgr.register_pending("CA_new", "y")  # triggers eviction of stale ones
    assert mgr.peek_pending("CA_old") is None
    assert mgr.peek_pending("CA_new") is not None


def test_active_call_count():
    mgr = TelephonyManager()
    assert mgr.active_calls == 0
    sentinel = object()
    mgr.register_active("CA1", sentinel)  # type: ignore[arg-type]
    assert mgr.active_calls == 1
    assert mgr.active_session("CA1") is sentinel
    mgr.unregister_active("CA1")
    assert mgr.active_calls == 0


def test_recent_calls_ring_buffer_dedups_by_sid():
    mgr = TelephonyManager(recent_capacity=5)
    mgr.record_call(CallRecord(call_sid="CA1", status="in_progress"))
    mgr.record_call(CallRecord(call_sid="CA1", status="completed", turns=2))
    calls = mgr.recent_calls()
    assert len(calls) == 1
    assert calls[0]["status"] == "completed"
    assert calls[0]["turns"] == 2


def test_recent_calls_capacity_and_order():
    mgr = TelephonyManager(recent_capacity=3)
    for i in range(5):
        mgr.record_call(CallRecord(call_sid=f"CA{i}", status="completed"))
    calls = mgr.recent_calls()
    assert len(calls) == 3  # capacity enforced
    # most recent first
    assert calls[0]["call_sid"] == "CA4"
    assert calls[-1]["call_sid"] == "CA2"


def test_reachability_cache():
    mgr = TelephonyManager()
    assert mgr.reachable is None
    mgr.set_reachable(True)
    assert mgr.reachable is True
    assert mgr.reachable_error is None
    mgr.set_reachable(False, "auth failed")
    assert mgr.reachable is False
    assert mgr.reachable_error == "auth failed"
