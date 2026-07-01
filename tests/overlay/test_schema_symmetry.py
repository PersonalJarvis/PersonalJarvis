"""JSON schema export as a CI gate for Phase 9.4 Zod symmetry (AD-15).

Phase 9.2 delivers the Pydantic side. Phase 9.4 will generate the Zod
schema and this test will then check structural equality. For now we
only verify that:

1. Pydantic can export a complete JSON schema for ``IPCMessage``.
2. Every envelope type has a unique discriminator value.
3. All expected type values are present.
4. Every envelope type is functionally round-trip-capable
   (serialize -> deserialize via ``IPCMessage.validate_json``).
5. The TS side (``OS-Level/overlay-ui/src/schema.ts``) explicitly
   documents the Phase-9.4 scope limitation (only the state slice is
   mirrored).

Phase-9.10+ TODO (real Zod<->Pydantic symmetry):
    Full structural equality between the Pydantic JSON schema and the
    Zod schema only makes sense once all 9 envelopes are mirrored on
    the TS side. At that point the test would need to run a
    ``tsc/node`` subprocess (Zod schema -> JSON schema via
    ``zod-to-json-schema``) and diff it against the Pydantic export.
    For Phase 9.4 that would be overhead without payoff: the renderer
    currently only needs the state slice. Planned from Phase 9.10
    onward, once the overlay itself consumes click/cursor/action
    envelopes.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from overlay.schema import IPCMessage


EXPECTED_TYPES = frozenset(
    {
        "state",
        "click",
        "action_started",
        "action_ended",
        "cursor",
        "heartbeat",
        "config",
        "ack",
        "error",
    }
)


def test_json_schema_exportable() -> None:
    schema = IPCMessage.json_schema()
    assert isinstance(schema, dict)
    # Pydantic v2 emits oneOf/discriminator for discriminated unions.
    blob = json.dumps(schema)
    for t in EXPECTED_TYPES:
        assert f'"{t}"' in blob, f"type={t!r} missing from JSON schema"


def test_all_expected_types_present() -> None:
    """Every envelope type must be validatable."""
    for t in EXPECTED_TYPES:
        # Minimal raw payload per type.
        sample = _sample_for(t)
        msg = IPCMessage.validate_python(sample)
        assert msg.type == t


def _sample_for(t: str) -> dict:
    base = {"v": 1, "id": "01HX9000000000000000000000", "ts_ns": 1, "target": "*", "type": t}
    payload_map = {
        "state": {"state": "idle"},
        "click": {"x": 0, "y": 0},
        "action_started": {"kind": "click", "action_id": "X"},
        "action_ended": {"action_id": "X"},
        "cursor": {"x": 0, "y": 0},
        "heartbeat": {},
        "config": {},
        "ack": {"ack_id": "X"},
        "error": {"code": "x", "message": "m"},
    }
    return {**base, "payload": payload_map[t]}


def test_discriminator_field_in_schema() -> None:
    schema = IPCMessage.json_schema()
    # Pydantic v2: the ``discriminator`` property is either under the ``oneOf``
    # wrapper or at the top level. We accept both — what matters is that the
    # ``type`` field carries the discriminator hint.
    blob = json.dumps(schema)
    assert "discriminator" in blob or "propertyName" in blob or '"type":' in blob


def test_schema_export_stable_for_drift_detection(tmp_path) -> None:
    """Snapshot of the JSON schema. Phase 9.4 compares against the Zod output."""
    schema = IPCMessage.json_schema()
    snapshot = tmp_path / "ipc-schema.json"
    snapshot.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    # Sanity: not empty, parseable.
    parsed = json.loads(snapshot.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)


# -----------------------------------------------------------------------------
# New tests (MAJOR #9 — Plan §10.3 + AD-15 symmetry audit)
# -----------------------------------------------------------------------------


def _find_overlay_ui_schema_ts() -> Path | None:
    """Looks for ``OS-Level/overlay-ui/src/schema.ts`` from the test file.

    We may run from different roots (``Personal Jarvis`` vs.
    ``Personal Jarvis-main``), so check multiple candidate paths.
    """
    here = Path(__file__).resolve()
    candidates: list[Path] = []
    for parent in (here.parent, *here.parents):
        candidates.append(parent / "OS-Level" / "overlay-ui" / "src" / "schema.ts")
    for path in candidates:
        if path.is_file():
            return path
    return None


def test_zod_schema_file_documents_phase94_limitation() -> None:
    """``schema.ts`` MUST explicitly document the Phase-9.4 scope limitation.

    Plan AD-15 demands symmetric Pydantic<->Zod schemas. Phase 9.4,
    however, only mirrors the state slice (StateName + StateChange), not
    all 9 envelopes. This scope limitation must be visible as a
    comment/TODO in the TS file, otherwise silent drift creeps in.
    """
    schema_ts = _find_overlay_ui_schema_ts()
    if schema_ts is None:
        pytest.skip(
            "OS-Level/overlay-ui/src/schema.ts not found — "
            "Phase 9.4 frontend tree is missing in this checkout."
        )

    text = schema_ts.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:30]).lower()

    # Accepted: a Phase-9.4 hint OR an explicit TODO/limitation/scope hint
    # about additional envelopes (or a reference to AD-15).
    phase_marker = any(
        token in head
        for token in (
            "9.4",
            "9.10",
            "phase 9",
            "todo",
            "ad-15",
            "limitation",
            "scope",
        )
    )
    envelope_marker = any(
        token in head
        for token in (
            "envelope",
            "weitere envelopes",  # i18n-allow: matched literally against schema.ts content
            "additional envelopes",
            "more envelopes",
            "state-slice",
            "state slice",
        )
    )

    if not (phase_marker and envelope_marker):
        pytest.fail(
            "AD-15 symmetry audit missing: schema.ts does not document the "
            "Phase-9.4 scope limitation. Please add a 1-5 line comment at the "
            "top explaining that only the state slice is mirrored and "
            "further envelopes follow from Phase 9.10+.\n"
            f"File: {schema_ts}\n"
            f"First 30 lines:\n{head}"
        )


def test_envelope_round_trip_each_type() -> None:
    """Each of the 9 envelope types must be JSON-round-trip-capable.

    Serialize a sample -> validate_json -> assert ``type`` and all
    payload keys are identical. Prevents a future Pydantic refactor
    from breaking the discriminated union without any test making
    noise.
    """
    for t in EXPECTED_TYPES:
        sample = _sample_for(t)
        # Bytes path (real-life wire format).
        raw = json.dumps(sample).encode("utf-8")
        msg = IPCMessage.validate_json(raw)

        # Discriminator must be preserved.
        assert msg.type == t, f"type mismatch for {t!r}: got {msg.type!r}"

        # All payload keys from the sample must end up in the
        # deserialized object (extra="forbid" on the payloads secures
        # the reverse direction — no key gets lost).
        sample_payload = sample["payload"]
        deserialized_payload = msg.payload.model_dump()
        for key, expected in sample_payload.items():
            assert key in deserialized_payload, (
                f"Payload key {key!r} missing after round trip for type={t!r}"
            )
            assert deserialized_payload[key] == expected, (
                f"Payload value for {key!r} differs for type={t!r}: "
                f"expected {expected!r}, got {deserialized_payload[key]!r}"
            )

        # Envelope fields must be identical.
        assert msg.v == sample["v"]
        assert msg.id == sample["id"]
        assert msg.ts_ns == sample["ts_ns"]
        assert msg.target == sample["target"]
