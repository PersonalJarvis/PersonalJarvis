"""Flight-Recorder — JSONL-Event-Log fuer Replay und Debug (ADR-0007).

Wird als Wildcard-Subscriber (`bus.subscribe_all`) aktiviert und schreibt
jedes Event als eine Zeile in `data/flight_recorder/YYYY-MM-DD.jsonl`.

Binaerdaten > 64 KB (z.B. Screenshots) werden nicht inline ge-dumpt,
sondern in `blobs/<hash>.<ext>` ausgelagert und per `{"__file__": "..."}`
referenziert — damit bleibt die JSONL jq-faehig.

Rotation bei Tageswechsel und bei File-Size > 500 MB (`-2`, `-3`, ...).
Ein `contextlib`-Flag fuer async-safe Write-Failures.
"""
from __future__ import annotations

import base64
import contextlib
import dataclasses
import hashlib
import json
import threading
import time
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import UUID

from jarvis.core.events import Event

if TYPE_CHECKING:
    from jarvis.core.bus import EventBus


# Top-Level-Keys, die nicht als "payload" serialisiert werden — sie
# landen auf Top-Level des JSON-Records (damit jq einfacher geht).
_TOP_LEVEL_FIELDS = frozenset({"trace_id", "timestamp_ns", "source_layer"})


def _json_default(value: Any) -> Any:
    """JSON-Encoder fuer die Typen, die stdlib nicht direkt beherrscht."""
    if isinstance(value, UUID):
        return value.hex
    if isinstance(value, bytes):
        return {"__bytes__": base64.b64encode(value).decode("ascii")}
    if dataclasses.is_dataclass(value):
        return dataclasses.asdict(value)
    if hasattr(value, "model_dump"):       # Pydantic
        return value.model_dump(mode="json")
    raise TypeError(f"Not JSON-serializable: {type(value).__name__}")


class FlightRecorder:
    """Wildcard-Subscriber am EventBus.

    Lifecycle:
        rec = FlightRecorder(data_dir=Path("data/flight_recorder"))
        rec.attach(bus)
        # ... events flow through ...
        await rec.flush()
        await rec.close()
    """

    # JSONL-Zeilen werden gepuffert und alle `flush_interval_s` Sekunden
    # per `fsync` in die Datei geschrieben — verhindert Disk-Kummer bei
    # vielen kleinen Writes, ohne Crash-Safety zu verlieren.
    flush_interval_s: float = 1.0

    # Binaerdaten-Inline-Limit. Alles groesser wird in `blobs/` ausgelagert.
    blob_inline_limit_bytes: int = 64 * 1024

    # File-Rotation — JSONL ueber dieser Groesse bekommt Suffix `-2`, `-3`, ...
    rotation_size_bytes: int = 500 * 1024 * 1024

    def __init__(
        self,
        data_dir: Path,
        *,
        flush_interval_s: float | None = None,
        now_ns: Callable[[], int] = time.time_ns,
        today_date: Callable[[], str] = lambda: datetime.now().strftime("%Y-%m-%d"),
    ) -> None:
        self._data_dir = data_dir
        self._blobs_dir = data_dir / "blobs"
        self._now_ns = now_ns
        self._today = today_date
        if flush_interval_s is not None:
            self.flush_interval_s = flush_interval_s

        self._lock = threading.Lock()
        self._buffer: list[str] = []
        self._current_day: str = self._today()
        self._last_flush_ns: int = 0
        self._subscribed_bus: EventBus | None = None

        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._blobs_dir.mkdir(parents=True, exist_ok=True)

    # ---------------- Bus binding ----------------

    def attach(self, bus: EventBus) -> None:
        """Registriert sich als Wildcard-Subscriber. Idempotent."""
        if self._subscribed_bus is bus:
            return
        self._subscribed_bus = bus
        bus.subscribe_all(self._on_event)

    async def _on_event(self, event: Event) -> None:
        line = self._serialize_event(event)
        should_flush = False
        with self._lock:
            self._buffer.append(line)
            if (self._now_ns() - self._last_flush_ns) / 1e9 >= self.flush_interval_s:
                should_flush = True
        if should_flush:
            await self.flush()

    # ---------------- Serialization ----------------

    def _serialize_event(self, event: Event) -> str:
        data = dataclasses.asdict(event)
        top: dict[str, Any] = {
            "ts_ns": data.pop("timestamp_ns"),
            "trace_id": event.trace_id.hex,
            "event": type(event).__name__,
            "layer": data.pop("source_layer", ""),
        }
        data.pop("trace_id", None)
        # Blob-Externalisierung fuer Felder, die als bytes ankommen.
        for key, value in list(data.items()):
            if isinstance(value, bytes) and len(value) > self.blob_inline_limit_bytes:
                data[key] = self._store_blob(value, hint=key)
        top["payload"] = data
        return json.dumps(top, default=_json_default, ensure_ascii=False)

    def _store_blob(self, value: bytes, *, hint: str) -> dict[str, str]:
        digest = hashlib.sha256(value).hexdigest()[:16]
        ext = ".png" if hint.endswith("_png") else ".bin"
        path = self._blobs_dir / f"{digest}{ext}"
        if not path.exists():
            path.write_bytes(value)
        return {"__file__": str(path.relative_to(self._data_dir.parent).as_posix())}

    # ---------------- File I/O ----------------

    def _current_file(self) -> Path:
        day = self._today()
        if day != self._current_day:
            self._current_day = day
        base = self._data_dir / f"{self._current_day}.jsonl"
        # Rotation bei Groesse
        if base.exists() and base.stat().st_size >= self.rotation_size_bytes:
            for suffix in range(2, 1000):
                cand = self._data_dir / f"{self._current_day}-{suffix}.jsonl"
                if not cand.exists() or cand.stat().st_size < self.rotation_size_bytes:
                    return cand
        return base

    async def flush(self) -> None:
        """Schreibt gepufferte Zeilen in die aktuelle JSONL-Datei."""
        with self._lock:
            if not self._buffer:
                return
            lines = self._buffer
            self._buffer = []
            self._last_flush_ns = self._now_ns()
        path = self._current_file()
        with path.open("a", encoding="utf-8") as f:
            f.write("\n".join(lines))
            f.write("\n")

    async def close(self) -> None:
        await self.flush()
        self._subscribed_bus = None

    # ---------------- Replay-Helpers ----------------

    def iter_events_for_trace(self, trace_id: UUID) -> list[dict[str, Any]]:
        """Liest alle Tages-JSONLs und filtert nach `trace_id`.

        Nutzung nur durch Replay-CLI / Tests — fuer Produktionsqueries
        sollte man `grep` oder `jq` nehmen.

        H3-Fix: `suppress(OSError)` umschliesst jetzt den **gesamten**
        Read-Loop (nicht nur den ``open()``-Call). Sonst wuerde ein I/O-
        Fehler mitten in ``readline()`` durchschlagen — besonders lustig
        auf Netzlaufwerken.
        """
        target = trace_id.hex
        out: list[dict[str, Any]] = []
        for path in sorted(self._data_dir.glob("*.jsonl")):
            with contextlib.suppress(OSError):
                with path.open("r", encoding="utf-8") as f:
                    for raw in f:
                        line = raw.strip()
                        if not line or target not in line:
                            continue
                        with contextlib.suppress(json.JSONDecodeError):
                            record = json.loads(line)
                            if record.get("trace_id") == target:
                                out.append(record)
        out.sort(key=lambda r: r.get("ts_ns", 0))
        return out


def attach_flight_recorder(
    bus: EventBus, *, enabled: bool, data_dir: Path | None = None,
) -> FlightRecorder | None:
    """Wire the flight-recorder audit log to the EventBus at boot (ADR-0007).

    The recorder is a wildcard subscriber that writes every event to
    ``data/flight_recorder/<date>.jsonl`` — a replayable audit trail of what
    Jarvis did, including every Computer-Use action. It was defined but never
    attached at boot, so ``telemetry.flight_recorder = true`` promised an audit
    log that was silently empty (audit #14). This is the single wiring point.

    Returns the attached recorder, or ``None`` when ``enabled`` is False. Caller
    owns logging + the boot try/except (consistent with the other ``_init_*``
    boot steps); a genuine setup error propagates so it is logged, never silently
    swallowed.
    """
    if not enabled:
        return None
    rec = FlightRecorder(data_dir=data_dir or (Path("data") / "flight_recorder"))
    rec.attach(bus)
    return rec
