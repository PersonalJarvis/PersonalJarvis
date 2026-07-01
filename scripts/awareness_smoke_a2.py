"""Live smoke test for Phase A2 — L2 Story Tracker (bus replay).

The Plan §6 smoke test is really "work manually for 30min" — unrealistic
for a smoke test. Instead, this script replays a realistic
bus sequence (FrameUpdated + IdleEntered) against a fully wired
StoryTracker with a real Verdichter (Haiku brain call, if an API key is present)
or a FakeVerdichter (no key found).

What the script verifies:
- StoryTracker accumulates salient frames in the builder
- app switch + min-duration triggers a Verdichter call
- episode lands in tmp SQLite + the FTS index is populated
- state.last_episode_summary is set
- EpisodeRecorded event is published

Usage::

    python scripts/awareness_smoke_a2.py
    python scripts/awareness_smoke_a2.py --use-real-haiku    # brain call instead of fake
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import os
import sys
import tempfile
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jarvis.awareness.config import AwarenessConfig, AwarenessStoryConfig
from jarvis.awareness.manager import AwarenessManager
from jarvis.awareness.state import FrameSnapshot
from jarvis.awareness.story import StoryTracker
from jarvis.core.bus import EventBus
from jarvis.core.events import EpisodeRecorded, FrameUpdated, IdleEntered
from jarvis.memory.recall import RecallStore


@dataclass
class FakeVerdichter:
    summary: str = (  # i18n-allow: simulated Verdichter output — the real Verdichter's summary is German by design (VERDICHTER_SYSTEM_PROMPT)
        "Der Nutzer war 8min in Code.exe mit pipeline.py aktiv, "  # i18n-allow: simulated Verdichter output, German by design
        "wechselte kurz zu Chrome.exe (GitHub-PR-Tab) und kehrte zurueck. "  # i18n-allow: simulated Verdichter output, German by design
        "Aktueller Fokus: pipeline.py."
    )
    calls: list[dict[str, Any]] = field(default_factory=list)

    async def call(
        self,
        *,
        frames: list[dict[str, Any]],
        events: list[dict[str, Any]],
        primary_app: str,
    ) -> tuple[str, dict[str, Any]]:
        self.calls.append({
            "n_frames": len(frames),
            "n_events": len(events),
            "primary_app": primary_app,
        })
        return self.summary, {
            "tokens_in": 380, "tokens_out": 95,
            "duration_ms": 420, "error_reason": None,
        }


def _make_frame(
    *, title: str, process: str, ts_ns: int, capture_allowed: bool = True,
) -> FrameSnapshot:
    return FrameSnapshot(
        timestamp_ns=ts_ns,
        active_window_title=title,
        active_process_name=process,
        active_pid=1000,
        is_capture_allowed=capture_allowed,
    )


async def run_smoke(use_real_haiku: bool) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        stream=sys.stdout,
    )
    log = logging.getLogger("smoke-a2")

    # Tmp DB
    tmpdir = Path(tempfile.mkdtemp(prefix="jarvis-a2-smoke-"))
    db_path = tmpdir / "jarvis.db"
    log.info("Tmp DB: %s", db_path)

    bus = EventBus()
    recall = RecallStore(db_path)
    await recall.open()

    manager = AwarenessManager(AwarenessConfig.default())

    # Verdichter: Real-Haiku oder Fake
    if use_real_haiku:
        try:
            from jarvis.awareness.verdichter import Verdichter
            from jarvis.brain.provider_registry import BrainProviderRegistry

            v_cfg = manager.config.verdichter
            log.info(
                "Real-Haiku-Modus: provider=%s model=%s",
                v_cfg.provider, v_cfg.model,
            )
            registry = BrainProviderRegistry()
            brain = registry.instantiate(v_cfg.provider, model=v_cfg.model)
            verdichter: Any = Verdichter(brain=brain, config=v_cfg)
        except Exception as exc:    # noqa: BLE001
            log.warning("Real-Haiku-Init failed (%s) — fallback to FakeVerdichter", exc)
            verdichter = FakeVerdichter()
    else:
        verdichter = FakeVerdichter()
        log.info("FakeVerdichter aktiv (deterministische Summary)")

    cfg = AwarenessStoryConfig(episode_min_duration_s=1)
    tracker = StoryTracker(
        manager=manager, bus=bus, recall=recall,
        verdichter=verdichter, config=cfg,
    )

    # Bus subscriber for stats
    received: list[EpisodeRecorded] = []

    async def collect(ev: EpisodeRecorded) -> None:
        received.append(ev)
    bus.subscribe(EpisodeRecorded, collect)

    await tracker.start()

    log.info("=== Replay: 6 frames over ~2.5s, then IdleEntered ===")
    base_ts = time.time_ns() - 2_500_000_000

    sequence: list[tuple[str, str, bool]] = [
        ("pipeline.py - Visual Studio Code", "Code.exe", True),
        ("manager.py - Visual Studio Code", "Code.exe", True),
        ("Banking - Sparkasse - Chrome", "chrome.exe", False),    # blocked
        ("pipeline.py - Visual Studio Code", "Code.exe", True),
        ("factory.py - Visual Studio Code", "Code.exe", True),
        ("pipeline.py - Visual Studio Code", "Code.exe", True),
    ]

    for i, (title, process, allowed) in enumerate(sequence):
        f = _make_frame(
            title=title, process=process,
            ts_ns=base_ts + i * 400_000_000,
            capture_allowed=allowed,
        )
        manager.state.current_frame = f
        ev = FrameUpdated(
            window_title=title, process_name=process,
            pid=1000, is_capture_allowed=allowed,
        )
        log.info(
            "FRAME #%d  process=%s  title=%r  allowed=%s",
            i + 1, process, title, allowed,
        )
        await tracker._on_frame_updated(ev)
        await asyncio.sleep(0.05)

    # Idle = Episode-Boundary
    log.info("=== IdleEntered → flush ===")
    await asyncio.sleep(1.2)    # > min_duration
    await tracker._on_idle_entered(IdleEntered(idle_since_ns=time.time_ns()))
    await asyncio.sleep(0.1)    # bus dispatch settle

    # Verifikationen
    log.info("=== Verifikationen ===")
    log.info("Verdichter calls: %d", len(verdichter.calls))
    if verdichter.calls:
        c = verdichter.calls[0]
        log.info(
            "  → Frames=%d  Events=%d  primary_app=%s",
            c.get("n_frames", c.get("frames_n", 0)),
            c.get("n_events", c.get("events_n", 0)),
            c["primary_app"],
        )

    episodes = await recall.recent_episodes(limit=10)
    log.info("Persisted episodes: %d", len(episodes))
    for ep in episodes:
        preview = ep["summary"][:80] + ("..." if len(ep["summary"]) > 80 else "")
        log.info(
            "  → id=%d  trigger=%s  primary_app=%s  frames=%d  tokens=%d/%d  summary=%r",
            ep["id"], ep["trigger_kind"], ep["primary_app"],
            ep["frame_count"], ep["tokens_in"], ep["tokens_out"], preview,
        )

    fts_results = await recall.search_episodes(query="pipeline", limit=5)
    log.info("FTS-Search 'pipeline': %d hit(s)", len(fts_results))

    log.info("State after flush:")
    log.info("  → last_episode_summary: %r", manager.state.last_episode_summary)
    log.info("  → last_episode_id: %s", manager.state.last_episode_id)

    log.info("EpisodeRecorded events received: %d", len(received))

    snap = manager.state.snapshot_for_prompt(max_chars=600)
    log.info("snapshot_for_prompt() (%d chars):", len(snap))
    for line in snap.splitlines():
        log.info("  | %s", line)

    await tracker.stop()
    await recall.close()
    log.info("=== DONE ===")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description="Awareness A2 bus-replay smoke")
    parser.add_argument(
        "--use-real-haiku", action="store_true",
        help="Echter Brain-Call statt FakeVerdichter (braucht API-Key)",
    )
    args = parser.parse_args()
    if os.environ.get("PYTHONIOENCODING", "").lower() != "utf-8":
        os.environ["PYTHONIOENCODING"] = "utf-8"
    return asyncio.run(run_smoke(args.use_real_haiku))


if __name__ == "__main__":
    sys.exit(main())
