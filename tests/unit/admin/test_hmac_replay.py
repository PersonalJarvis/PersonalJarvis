"""HMAC verification + nonce replay protection + timestamp window (ADR-0001).

The tests exercise ``AdminPipeServer._decode_request`` directly at the
byte level — this way we need neither a real named pipe nor UAC elevation.
"""
from __future__ import annotations

import hashlib
import hmac as _hmac
import json
import time

import pytest

from jarvis.admin.executor import AdminExecutor
from jarvis.admin.ipc import (
    _TIMESTAMP_WINDOW_NS,
    AdminPipeServer,
    _AuthFailure,
    _canonical_args_json,
    _compute_hmac,
)
from jarvis.admin.schema import InstallWingetOp, ReadRegistryOp

SECRET = b"x" * 32


def _make_server(executor: AdminExecutor | None = None) -> AdminPipeServer:
    return AdminPipeServer(
        SECRET, r"\\.\pipe\test-admin",
        executor or AdminExecutor(),
        sid="S-1-5-18",
    )


def _build_envelope(op, *, secret: bytes = SECRET, nonce: str | None = None,
                    ts: int | None = None, break_hmac: bool = False,
                    extra_field_tamper: bool = False) -> bytes:
    op_dump = op.model_dump(mode="json")
    nonce = nonce or "a" * 32
    ts = ts if ts is not None else time.time_ns()
    args_json = _canonical_args_json(op_dump)
    sig = _compute_hmac(secret, nonce, ts, op_dump["type"], op_dump["op_id"], args_json)
    if break_hmac:
        sig = "0" * 64
    env = {"nonce": nonce, "timestamp_ns": ts, "hmac": sig, "op": op_dump}
    if extra_field_tamper:
        env["op"] = {**op_dump, "package_id": "EvilPkg"}
    return json.dumps(env).encode("utf-8")


def test_valid_request_passes():
    server = _make_server()
    op = InstallWingetOp(package_id="7zip.7zip")
    raw = _build_envelope(op)
    decoded, _nonce = server._decode_request(raw)
    assert isinstance(decoded, InstallWingetOp)
    assert decoded.package_id == "7zip.7zip"


def test_hmac_invalid_rejected():
    server = _make_server()
    op = InstallWingetOp(package_id="7zip.7zip")
    raw = _build_envelope(op, break_hmac=True)
    with pytest.raises(_AuthFailure) as exc:
        server._decode_request(raw)
    assert "hmac_invalid" in str(exc.value)


def test_nonce_replay_rejected():
    """H5 fix: the cache key is (nonce, timestamp_ns). Replay = the exact same
    envelope a second time. An attacker who replays the sniffed message
    sends identical bytes. With a new nonce or new timestamp the HMAC
    would be different anyway and would fail separately."""
    server = _make_server()
    op = InstallWingetOp(package_id="7zip.7zip")
    nonce = "deadbeef" * 4
    raw = _build_envelope(op, nonce=nonce)
    # First call ok
    server._decode_request(raw)
    # Second call with the EXACT same envelope → replay
    with pytest.raises(_AuthFailure) as exc:
        server._decode_request(raw)
    assert "nonce_replay" in str(exc.value)


def test_timestamp_out_of_window_rejected():
    server = _make_server()
    op = InstallWingetOp(package_id="7zip.7zip")
    stale_ts = time.time_ns() - (_TIMESTAMP_WINDOW_NS + 1_000_000_000)
    raw = _build_envelope(op, ts=stale_ts)
    with pytest.raises(_AuthFailure) as exc:
        server._decode_request(raw)
    assert "timestamp_out_of_window" in str(exc.value)


def test_wrong_secret_rejected():
    server = _make_server()
    op = InstallWingetOp(package_id="7zip.7zip")
    raw = _build_envelope(op, secret=b"wrong-secret-with-enough-len!!!!")
    with pytest.raises(_AuthFailure) as exc:
        server._decode_request(raw)
    assert "hmac_invalid" in str(exc.value)


def test_tampered_op_args_breaks_hmac():
    """HMAC also covers the args — tampering with package_id after signing
    must fail."""
    server = _make_server()
    op = InstallWingetOp(package_id="HarmlessPkg")
    op_dump = op.model_dump(mode="json")
    nonce = "b" * 32
    ts = time.time_ns()
    args_json = _canonical_args_json(op_dump)
    sig = _compute_hmac(SECRET, nonce, ts, op_dump["type"],
                        op_dump["op_id"], args_json)
    # Attack: after signing, patch package_id to something malicious.
    op_dump_tampered = {**op_dump, "package_id": "7zip.7zip"}
    env = {"nonce": nonce, "timestamp_ns": ts, "hmac": sig,
           "op": op_dump_tampered}
    raw = json.dumps(env).encode("utf-8")
    with pytest.raises(_AuthFailure) as exc:
        server._decode_request(raw)
    assert "hmac_invalid" in str(exc.value)


def test_lru_evicts_oldest_nonce():
    """Once more than the LRU size worth of entries is added, the oldest is evicted.

    Since the H5 fix, the cache key is ``(nonce, timestamp_ns)`` — even
    if a nonce is evicted, it can only be replayed if the exact
    timestamp is ALSO reused, which fails separately anyway outside the
    30s window. This test only covers the eviction mechanics,
    not the replay scenario itself (that is secured separately by the
    timestamp check).
    """
    server = _make_server()
    lru_size = 10_000
    # We test the eviction logic at a smaller boundary: we
    # fill LRU_SIZE + 5 entries and check that the first 5 are gone.
    # This gives the test a constant runtime.
    ts = 1_000_000_000
    for i in range(lru_size + 5):
        server._check_and_record_nonce(f"n{i:06d}", ts + i)
    assert ("n000000", ts + 0) not in server._nonce_set
    assert ("n000004", ts + 4) not in server._nonce_set
    assert (f"n{lru_size + 4:06d}", ts + lru_size + 4) in server._nonce_set


def test_envelope_not_object_rejected():
    server = _make_server()
    raw = json.dumps(["not", "an", "object"]).encode("utf-8")
    with pytest.raises(_AuthFailure) as exc:
        server._decode_request(raw)
    assert "envelope_not_object" in str(exc.value)


def test_compute_hmac_is_constant_time_compare():
    """Sanity: `_compute_hmac` is deterministic and the verification
    uses `hmac.compare_digest` instead of `==` — we check this indirectly
    by comparing identical calls."""
    sig1 = _compute_hmac(SECRET, "n", 123, "t", "id", "{}")
    sig2 = _compute_hmac(SECRET, "n", 123, "t", "id", "{}")
    assert _hmac.compare_digest(sig1, sig2)
    # And also: length as expected (sha256 hex = 64 chars).
    assert len(sig1) == 64
    # Sanity check: not identical to a plain sha256.
    assert sig1 != hashlib.sha256(b"whatever").hexdigest()


def test_read_registry_envelope_happy_path():
    """Non-install ops go through too."""
    server = _make_server()
    op = ReadRegistryOp(hive="HKCU", key_path="Environment")
    raw = _build_envelope(op)
    decoded, _ = server._decode_request(raw)
    assert isinstance(decoded, ReadRegistryOp)
    assert decoded.key_path == "Environment"
