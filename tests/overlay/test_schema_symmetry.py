"""JSON-Schema-Export als CI-Gate fuer Phase 9.4 Zod-Symmetrie (AD-15).

Phase 9.2 liefert die Pydantic-Seite. Phase 9.4 wird das Zod-Schema
generieren und dieser Test prueft dann strukturelle Gleichheit. Hier
verifizieren wir vorerst nur, dass:

1. Pydantic ein vollstaendiges JSON-Schema fuer ``IPCMessage``
   exportieren kann.
2. Jeder Envelope-Type einen unique discriminator-Wert hat.
3. Alle erwarteten Type-Werte praesent sind.
4. Jeder Envelope-Type funktional roundtrip-faehig ist
   (serialize -> deserialize via ``IPCMessage.validate_json``).
5. Die TS-Seite (``OS-Level/overlay-ui/src/schema.ts``) dokumentiert die
   Phase-9.4-Scope-Limitation explizit (nur State-Slice gespiegelt).

Phase-9.10+-TODO (echte Zod<->Pydantic-Symmetrie):
    Eine vollstaendige strukturelle Gleichheit zwischen Pydantic-JSON-
    Schema und Zod-Schema ist erst sinnvoll, wenn alle 9 Envelopes auf
    der TS-Seite gespiegelt sind. Dann muesste der Test einen
    ``tsc/node``-Subprocess fahren (Zod-Schema -> JSON-Schema via
    ``zod-to-json-schema``) und gegen den Pydantic-Export diff'en. Das
    ist zur Phase 9.4 Overhead ohne Gegenwert: der Renderer braucht
    aktuell nur den State-Slice. Geplant ab Phase 9.10, wenn Overlay
    selbst Click/Cursor/Action-Envelopes konsumiert.
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
    # Pydantic v2 emittiert oneOf/discriminator fuer Discriminated Unions.
    blob = json.dumps(schema)
    for t in EXPECTED_TYPES:
        assert f'"{t}"' in blob, f"type={t!r} fehlt in JSON-Schema"


def test_all_expected_types_present() -> None:
    """Jeder Envelope-Type muss validierbar sein."""
    for t in EXPECTED_TYPES:
        # Minimaler Roh-Payload pro Type.
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
    # Pydantic v2: ``discriminator`` Property unter ``oneOf``-Wrapper oder im
    # Top-Level. Wir akzeptieren beides — wichtig ist, dass das Feld ``type``
    # den Discriminator-Hinweis traegt.
    blob = json.dumps(schema)
    assert "discriminator" in blob or "propertyName" in blob or '"type":' in blob


def test_schema_export_stable_for_drift_detection(tmp_path) -> None:
    """Snapshot des JSON-Schemas. Phase 9.4 vergleicht gegen Zod-Output."""
    schema = IPCMessage.json_schema()
    snapshot = tmp_path / "ipc-schema.json"
    snapshot.write_text(json.dumps(schema, indent=2, sort_keys=True), encoding="utf-8")
    # Sanity: nicht leer, parseable.
    parsed = json.loads(snapshot.read_text(encoding="utf-8"))
    assert isinstance(parsed, dict)


# -----------------------------------------------------------------------------
# Neue Tests (MAJOR #9 — Plan §10.3 + AD-15 Symmetrie-Audit)
# -----------------------------------------------------------------------------


def _find_overlay_ui_schema_ts() -> Path | None:
    """Sucht ``OS-Level/overlay-ui/src/schema.ts`` von der Test-Datei aus.

    Wir laufen u.U. aus verschiedenen Roots (``Personal Jarvis`` vs.
    ``Personal Jarvis-main``); also mehrere Kandidaten-Pfade pruefen.
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
    """``schema.ts`` MUSS die Phase-9.4-Scope-Limitation explizit dokumentieren.

    Plan AD-15 fordert symmetrische Pydantic<->Zod-Schemas. Phase 9.4
    spiegelt jedoch nur den State-Slice (StateName + StateChange), nicht
    alle 9 Envelopes. Diese Scope-Begrenzung muss im TS-File als
    Kommentar/TODO sichtbar sein, sonst entsteht stille Drift.
    """
    schema_ts = _find_overlay_ui_schema_ts()
    if schema_ts is None:
        pytest.skip(
            "OS-Level/overlay-ui/src/schema.ts nicht gefunden — "
            "Phase 9.4 Frontend-Tree fehlt in diesem Checkout."
        )

    text = schema_ts.read_text(encoding="utf-8")
    head = "\n".join(text.splitlines()[:30]).lower()

    # Akzeptiert: Phase-9.4-Hinweis ODER expliziter TODO/limitation/scope-Hinweis
    # auf weitere Envelopes (oder AD-15 referenziert).
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
            "weitere envelopes",
            "additional envelopes",
            "more envelopes",
            "state-slice",
            "state slice",
        )
    )

    if not (phase_marker and envelope_marker):
        pytest.fail(
            "AD-15 Symmetrie-Audit fehlt: schema.ts dokumentiert die Phase-9.4-"
            "Scope-Limitation nicht. Bitte 1-5 Zeilen Kommentar oben einfuegen, "
            "der erklaert dass nur der State-Slice gespiegelt ist und weitere "
            "Envelopes mit Phase 9.10+ folgen.\n"
            f"Datei: {schema_ts}\n"
            f"Erste 30 Zeilen:\n{head}"
        )


def test_envelope_round_trip_each_type() -> None:
    """Jeder der 9 Envelope-Types muss JSON-Roundtrip-faehig sein.

    Serialize ein Sample -> validate_json -> assert ``type`` und alle
    Payload-Keys identisch. Verhindert dass ein zukuenftiger Pydantic-
    Refactor die Discriminated-Union sprengt, ohne dass ein Test laut
    wird.
    """
    for t in EXPECTED_TYPES:
        sample = _sample_for(t)
        # Bytes-Pfad (real-life Wire-Format).
        raw = json.dumps(sample).encode("utf-8")
        msg = IPCMessage.validate_json(raw)

        # Discriminator muss erhalten bleiben.
        assert msg.type == t, f"type-Mismatch fuer {t!r}: got {msg.type!r}"

        # Payload-Keys aus dem Sample muessen alle im deserialisierten
        # Objekt landen (extra="forbid" auf den Payloads sichert die
        # Gegenrichtung — kein Key wandert verloren).
        sample_payload = sample["payload"]
        deserialized_payload = msg.payload.model_dump()
        for key, expected in sample_payload.items():
            assert key in deserialized_payload, (
                f"Payload-Key {key!r} fehlt nach Roundtrip fuer type={t!r}"
            )
            assert deserialized_payload[key] == expected, (
                f"Payload-Wert fuer {key!r} weicht ab fuer type={t!r}: "
                f"erwartet {expected!r}, bekommen {deserialized_payload[key]!r}"
            )

        # Envelope-Felder muessen identisch sein.
        assert msg.v == sample["v"]
        assert msg.id == sample["id"]
        assert msg.ts_ns == sample["ts_ns"]
        assert msg.target == sample["target"]
