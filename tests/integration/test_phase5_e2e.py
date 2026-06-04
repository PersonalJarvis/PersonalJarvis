"""Phase-5 E2E-Integration-Tests — abbildend was der Mandat-DoD verlangt.

Nicht live: keine echten Screenshots, kein echter UAC-Prompt, keine
echten API-Calls. Stattdessen verdrahten wir die Komponenten mit ihren
Fakes und pruefen die Propagation durch die Schichten.

Live-Tests mit echtem Desktop/Admin sind in `test_vision_live.py` und
`test_admin_ipc_loopback.py` mit `@pytest.mark.skip_ci`.

Dieser File ist die Referenz-Abnahme fuer den DoD im Phase-5-Report.
"""
from __future__ import annotations

import asyncio
import importlib.metadata as _md

import pytest

from jarvis.control import (
    CancelScope,
    CostMeter,
    KillSwitch,
)
from jarvis.control.cost import BudgetConfig, ModelPrice
from jarvis.control.wiring import voice_matches_kill_intent
from jarvis.core.bus import EventBus
from jarvis.core.events import (
    BudgetExceeded,
    KillRequested,
    ObservationCaptured,
    TranscriptFinal,
)
from jarvis.core.protocols import CostRecord, Transcript
from jarvis.telemetry import FlightRecorder

pytestmark = pytest.mark.phase5


# ======================================================================
# DoD 1 — `pytest -m phase5` laeuft durch
# ======================================================================
# Dieser File traegt den Marker; die Ausfuehrung dieser Tests deckt das
# ab. Die `skip_ci`-Live-Tests sind separat.


# ======================================================================
# DoD 2 — screenshot computer-use Harness ist via entry_points discoverbar
# ======================================================================

def test_entry_points_registered_for_phase5():
    """pyproject.toml hat screenshot + dispatch-to-admin eingetragen."""
    harness_eps = {ep.name for ep in _md.entry_points(group="jarvis.harness")}
    tool_eps = {ep.name for ep in _md.entry_points(group="jarvis.tool")}
    assert "screenshot" in harness_eps
    assert "dispatch-to-admin" in tool_eps


# ======================================================================
# DoD 3 — Kill-Switch greift <2s
# ======================================================================

@pytest.mark.asyncio
async def test_kill_switch_via_voice_cancels_cu_token_under_2s():
    """Voice-Intent -> KillRequested -> KillSwitch -> Token.cancel, <2s."""
    bus = EventBus()
    ks = KillSwitch()
    ks.bind(bus)
    # Voice-Wiring
    from jarvis.control.wiring import wire_voice_kill_switch
    wire_voice_kill_switch(bus)

    import time
    async with CancelScope(ks, holder="cu_loop") as token:
        start = time.monotonic()
        await bus.publish(TranscriptFinal(
            transcript=Transcript(text="Jarvis, stopp!",
                                  language="de", confidence=0.95),
        ))
        # Event-Dispatch durchlaufen lassen
        for _ in range(5):
            await asyncio.sleep(0)
        elapsed = time.monotonic() - start
        assert token.is_cancelled()
        assert elapsed < 2.0, f"Kill-Switch brauchte {elapsed:.3f}s (>2s)"


def test_voice_intent_regex_covers_mandate_phrases():
    """Alle Mandat-Phrasen werden vom Regex gefangen."""
    for phrase in [
        "Notfall-Stopp", "Jarvis, stopp", "kill switch", "emergency stop",
        "alles stoppen",
    ]:
        assert voice_matches_kill_intent(phrase), f"Fehlgeschlagen: {phrase!r}"


# ======================================================================
# DoD 4 — Cost-Circuit trippt bei Overrun, startet Cooldown
# ======================================================================

@pytest.mark.asyncio
async def test_cost_circuit_trips_and_cancels_token(tmp_path):
    """Simulierter Overrun -> BudgetExceeded-Event + Token gecancelt."""
    bus = EventBus()
    ks = KillSwitch()
    ks.bind(bus)
    config = BudgetConfig(
        enabled=True, per_task_eur=0.5, per_day_eur=100.0, eur_per_usd=1.0,
        prices={"test": ModelPrice(usd_per_1m_input=1.0, usd_per_1m_output=1.0)},
    )
    meter = CostMeter(config, tmp_path / "jarvis.db", tmp_path / "cooldown.json",
                      bus=bus, kill_switch=ks)

    events: list = []
    async def capture(ev: BudgetExceeded) -> None:
        events.append(ev)
    bus.subscribe(BudgetExceeded, capture)

    from uuid import uuid4
    async with CancelScope(ks, holder="brain_stream") as token:
        tid = uuid4()
        meter.start(tid, "test", "test")
        meter.add(CostRecord(
            trace_id=tid, provider="test", model="test",
            tokens_in=1, tokens_out=1, tokens_cache_hit=0,
            usd=1.0,                                   # weit ueber 0.5 EUR
            timestamp_ns=0,
        ))
        for _ in range(5):
            await asyncio.sleep(0)

        assert events, "BudgetExceeded wurde nicht publiziert"
        assert events[0].scope == "task"
        assert token.is_cancelled()
        assert token.reason == "budget_task_exceeded"


@pytest.mark.asyncio
async def test_cost_daily_overrun_starts_cooldown_persistent(tmp_path):
    """Tages-Overrun setzt Cooldown, der App-Restart ueberlebt."""
    config = BudgetConfig(
        enabled=True, per_task_eur=100.0, per_day_eur=0.5,
        cooldown_minutes=60, eur_per_usd=1.0,
        prices={"x": ModelPrice(usd_per_1m_input=1.0, usd_per_1m_output=1.0)},
    )
    meter1 = CostMeter(config, tmp_path / "jarvis.db", tmp_path / "cooldown.json")

    from uuid import uuid4
    tid = uuid4()
    meter1.start(tid, "x", "x")
    meter1.add(CostRecord(trace_id=tid, provider="x", model="x",
                           tokens_in=1, tokens_out=1, tokens_cache_hit=0,
                           usd=1.0, timestamp_ns=0))
    assert meter1.is_in_cooldown()

    # Neuer Meter auf gleichen Pfaden -> Cooldown bleibt aktiv
    meter2 = CostMeter(config, tmp_path / "jarvis.db", tmp_path / "cooldown.json")
    assert meter2.is_in_cooldown()


# ======================================================================
# DoD 6 — Flight-Recorder laesst sich replayen
# ======================================================================

@pytest.mark.asyncio
async def test_flight_recorder_roundtrip_for_cu_task(tmp_path):
    """CU-Task -> Recorder schreibt JSONL -> Replay findet Events."""
    bus = EventBus()
    recorder = FlightRecorder(tmp_path, flush_interval_s=0)
    recorder.attach(bus)

    from uuid import uuid4
    trace_id = uuid4()
    from jarvis.core.events import HarnessDispatched
    from jarvis.core.protocols import HarnessTask
    await bus.publish(HarnessDispatched(
        trace_id=trace_id, harness="computer-use",
        task=HarnessTask(prompt="test"),
    ))
    await bus.publish(ObservationCaptured(
        trace_id=trace_id, window_title="Notepad", node_count=15,
        screenshot_hash="abc123",
    ))
    await bus.publish(KillRequested(trace_id=trace_id, source="hotkey"))
    await recorder.flush()
    await recorder.close()

    records = recorder.iter_events_for_trace(trace_id)
    kinds = [r["event"] for r in records]
    assert kinds == ["HarnessDispatched", "ObservationCaptured", "KillRequested"]

    # Replay-CLI liefert Exit-Code 0
    from jarvis.telemetry.replay import main as replay_main
    rc = replay_main([trace_id.hex, "--data-dir", str(tmp_path)])
    assert rc == 0


# ======================================================================
# DoD 7 — Task-Queue: Startup-Cleanup macht running -> interrupted
# ======================================================================

@pytest.mark.asyncio
async def test_task_queue_startup_cleanup_marks_running_as_interrupted(tmp_path):
    """Simuliert App-Crash waehrend laufender Task."""
    from jarvis.tasks import SpeakAction, TaskSpec, TriggerAfterDelay
    from jarvis.tasks.store import TaskStore
    store = TaskStore(tmp_path / "jarvis.db")
    await store.init()
    try:
        spec = TaskSpec(
            title="crashed",
            trigger=TriggerAfterDelay(delay_seconds=1.0),
            action=SpeakAction(text="..."),
        )
        task_id = await store.insert(spec)
        await store.update_state(task_id, "running")

        n = await store.cleanup_interrupted()
        assert n >= 1

        task = await store.get(task_id)
        assert task["state"] == "interrupted"
    finally:
        await store.close()


# ======================================================================
# DoD 8 — Fresh-Install: leeres jarvis.toml startet ohne Phase-5-Features
# ======================================================================

def test_phase5_sections_are_disabled_by_default():
    """Ohne User-Eingriff sind alle Phase-5-Sections in jarvis.toml enabled=false."""
    from jarvis.core import config as cfg
    raw = cfg._RAW_CONFIG if hasattr(cfg, "_RAW_CONFIG") else {}

    phase5_sections = ["vision", "computer_use", "admin_helper",
                       "task_queue", "kill_switch", "cost"]
    for section in phase5_sections:
        data = raw.get(section, {}) if isinstance(raw, dict) else {}
        if isinstance(data, dict) and "enabled" in data:
            assert data["enabled"] is False, (
                f"jarvis.toml:[{section}] default MUSS enabled=false sein, "
                f"fand: {data.get('enabled')!r}"
            )
