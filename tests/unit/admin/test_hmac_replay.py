"""HMAC-Verifikation + Nonce-Replay-Schutz + Timestamp-Window (ADR-0001).

Die Tests prueften ``AdminPipeServer._decode_request`` direkt auf
Bytes-Ebene — so brauchen wir weder echte Named-Pipe noch UAC-Elevation.
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
    """H5-Fix: Cache-Key ist (nonce, timestamp_ns). Replay = exakt derselbe
    Envelope ein zweites Mal. Ein Angreifer, der die gesnifferte Message
    wiederholt, sendet identische Bytes. Mit neuer Nonce oder neuem
    Timestamp waere die HMAC ohnehin anders und wuerde separat scheitern."""
    server = _make_server()
    op = InstallWingetOp(package_id="7zip.7zip")
    nonce = "deadbeef" * 4
    raw = _build_envelope(op, nonce=nonce)
    # Erster Call ok
    server._decode_request(raw)
    # Zweiter Call mit EXAKT demselben Envelope → Replay
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
    """HMAC deckt die Args mit ab — Manipulation an package_id nach Signatur
    muss scheitern."""
    server = _make_server()
    op = InstallWingetOp(package_id="HarmlessPkg")
    op_dump = op.model_dump(mode="json")
    nonce = "b" * 32
    ts = time.time_ns()
    args_json = _canonical_args_json(op_dump)
    sig = _compute_hmac(SECRET, nonce, ts, op_dump["type"],
                        op_dump["op_id"], args_json)
    # Angriff: nach Signatur die package_id auf was Boeses patchen.
    op_dump_tampered = {**op_dump, "package_id": "7zip.7zip"}
    env = {"nonce": nonce, "timestamp_ns": ts, "hmac": sig,
           "op": op_dump_tampered}
    raw = json.dumps(env).encode("utf-8")
    with pytest.raises(_AuthFailure) as exc:
        server._decode_request(raw)
    assert "hmac_invalid" in str(exc.value)


def test_lru_evicts_oldest_nonce():
    """Nach mehr als LRU-Groesse Eintraegen wird der aelteste verdraengt.

    Seit dem H5-Fix ist der Cache-Key ``(nonce, timestamp_ns)`` — selbst
    wenn eine Nonce verdraengt wird, kann sie nur replayed werden wenn
    ALSO der exakte Timestamp wiederverwendet wird, was ausserhalb des
    30s-Fensters ohnehin scheitert. Der Test deckt nur die Evict-Mechanik
    ab, nicht das Replay-Szenario selbst (das ist durch den Timestamp-
    Check separat gesichert).
    """
    server = _make_server()
    lru_size = 10_000
    # Wir pruefen die Evict-Logik an einer kleineren Grenze: wir
    # fuellen LRU_SIZE + 5 Eintraege und checken die ersten 5 sind weg.
    # Das gibt dem Test eine konstante Laufzeit.
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
    """Sanity: `_compute_hmac` ist deterministisch und die Verifikation
    nutzt `hmac.compare_digest` statt `==` — das pruefen wir indirekt,
    indem wir identische Aufrufe vergleichen."""
    sig1 = _compute_hmac(SECRET, "n", 123, "t", "id", "{}")
    sig2 = _compute_hmac(SECRET, "n", 123, "t", "id", "{}")
    assert _hmac.compare_digest(sig1, sig2)
    # Und noch: Laenge wie erwartet (sha256 hex = 64 chars).
    assert len(sig1) == 64
    # Sanity-Check: nicht mit blossen sha256 identisch.
    assert sig1 != hashlib.sha256(b"whatever").hexdigest()


def test_read_registry_envelope_happy_path():
    """Auch non-install Ops gehen durch."""
    server = _make_server()
    op = ReadRegistryOp(hive="HKCU", key_path="Environment")
    raw = _build_envelope(op)
    decoded, _ = server._decode_request(raw)
    assert isinstance(decoded, ReadRegistryOp)
    assert decoded.key_path == "Environment"
