"""Performance-Audit für v1.0.

Drei Targets aus dem Release-Prompt:

1. **Aggregator-Run mit 365 Tage Daten** — Soll < 30 s.
2. **Federation-Pull mit 10 Friends** — Bandbreite pro Pull < 100 KB.
3. **Frontend `/board` Initial-Load** — Soll < 500 ms.

Items 1 + 2 misst dieses Skript direkt. Item 3 erfordert eine Browser-
Session und wird separat in PERFORMANCE_AUDIT.md dokumentiert (mit
``mcp__playwright`` oder per Hand).

Aufruf: ``python tools/board_perf.py``
"""
from __future__ import annotations

import asyncio
import json
import statistics
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from random import Random
from tempfile import TemporaryDirectory

import httpx

from board_backend.config import Settings
from board_backend.crypto import canonical_json, generate_keypair, sign
from board_backend.main import create_app
from jarvis.board.aggregator import BoardAggregator


# ----------------------------------------------------------------------
# (1) Aggregator: 365 Tage Synthetic-JSONL → run()-Dauer
# ----------------------------------------------------------------------

def _emit_year_of_events(jsonl_dir: Path, *, days: int = 365, events_per_day: int = 50) -> int:
    """Schreibt ``days * events_per_day`` Events ueber ``days`` Tage,
    aufgeteilt auf Tagesschritte. Returns total events count.
    """
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    rng = Random(7)
    now = datetime.now(timezone.utc)
    total = 0
    for day_off in range(days):
        day = now - timedelta(days=day_off)
        path = jsonl_dir / f"{day.strftime('%Y-%m-%d')}.jsonl"
        with path.open("w", encoding="utf-8") as fh:
            for k in range(events_per_day):
                kind = rng.choices(
                    ("ActionExecuted", "TaskCompleted", "SubJarvisCompleted",
                     "TranscriptFinal"),
                    weights=(0.55, 0.15, 0.10, 0.20),
                )[0]
                ts = day - timedelta(seconds=k * 17)
                ts_ns = int(ts.timestamp() * 1e9)
                trace_id = rng.getrandbits(128).to_bytes(16, "big").hex()[:32]
                if kind == "ActionExecuted":
                    payload = {
                        "tool_name": rng.choice(["bash", "search_web", "write_file",
                                                  "grep_repo", "read_file", "git_log"]),
                        "success": rng.random() > 0.10,
                        "duration_ms": rng.randint(20, 1500),
                    }
                elif kind == "TaskCompleted":
                    payload = {"task_id": f"t{day_off}-{k}", "duration_ms": rng.randint(100, 9000)}
                elif kind == "SubJarvisCompleted":
                    payload = {"success": rng.random() > 0.20,
                               "duration_s": rng.uniform(60, 3600), "summary": "redacted"}
                else:
                    payload = {"transcript": {"text": "redacted"}}
                line = json.dumps({
                    "ts_ns": ts_ns, "trace_id": trace_id,
                    "event": kind, "layer": "perf-fixture",
                    "payload": payload,
                })
                fh.write(line + "\n")
                total += 1
    return total


def benchmark_aggregator() -> dict:
    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        jsonl = tmp / "flight_recorder"
        db = tmp / "personal.db"

        emit_t0 = time.perf_counter()
        n_events = _emit_year_of_events(jsonl, days=365, events_per_day=50)
        emit_dt = time.perf_counter() - emit_t0

        agg = BoardAggregator(jsonl_dir=jsonl, db_path=db)
        run_t0 = time.perf_counter()
        agg.run()
        run_dt = time.perf_counter() - run_t0

        # Re-run idempotent? Nicer-to-have number.
        run2_t0 = time.perf_counter()
        agg.run()
        run2_dt = time.perf_counter() - run2_t0

        rows = agg.db.execute("SELECT COUNT(*) FROM daily_stats").fetchone()[0]
        size_mb = db.stat().st_size / (1024 * 1024)
        agg.close()

    return {
        "events_total": n_events,
        "fixture_emit_s": round(emit_dt, 2),
        "aggregate_run_s": round(run_dt, 3),
        "aggregate_rerun_s": round(run2_dt, 3),
        "daily_stats_rows": rows,
        "db_size_mb": round(size_mb, 3),
        "target_lt_30s": run_dt < 30.0,
    }


# ----------------------------------------------------------------------
# (2) Federation-Pull-Bandbreite mit 10 Friends
# ----------------------------------------------------------------------

async def benchmark_federation_pull() -> dict:
    """Misst Body-Größe + Latenz für /federation/feed mit 10 Friends.

    Wir spawnen ein Backend mit Owner + 10 Friend-Rows + ~50
    Activity-Items. Dann pullt der erste Friend den Feed. Bandbreite =
    Response-Body-Größe.
    """
    from board_backend.models import ActivityItem, Friend

    with TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        s = Settings(admin_token="x", db_path=tmp / "p.db",
                     register_rate_limit_per_minute=1000, replay_window_seconds=300)
        app = create_app(settings=s)
        app.state.disable_background = True

        owner_priv, owner_pub = generate_keypair()
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                                     base_url="http://x", timeout=10) as c:
            await c.post("/api/v1/identity/register",
                         json={"pubkey": owner_pub, "display_name": "Owner"},
                         headers={"X-Admin-Token": "x"})

            # 10 Friends + 50 Activity-Items.
            from datetime import datetime, timezone
            with app.state.session_factory() as session:
                friends_priv = []
                for i in range(10):
                    fp_priv, fp_pub = generate_keypair()
                    friends_priv.append((fp_priv, fp_pub))
                    session.add(Friend(
                        owner_pubkey=owner_pub, friend_pubkey=fp_pub,
                        friend_url=f"http://f{i}", friend_display_name=f"F{i}",
                        paired_at=datetime.now(timezone.utc),
                    ))
                rng = Random(11)
                for i in range(50):
                    session.add(ActivityItem(
                        id=f"item{i:028d}",
                        author_pubkey=owner_pub,
                        kind="achievement_unlocked",
                        payload=json.dumps({"achievement_id": f"a{i}"}),
                        created_at=datetime.now(timezone.utc) - timedelta(hours=i),
                        visibility="friends",
                    ))
                session.commit()

            # Friend 0 pullt.
            f0_priv, f0_pub = friends_priv[0]
            payload = {"ts_ms": int(time.time() * 1000)}
            body = canonical_json(payload)
            sig = sign(payload, privkey_hex=f0_priv)
            samples = []
            for _ in range(5):
                t0 = time.perf_counter()
                r = await c.request(
                    "GET", "/api/v1/federation/feed", content=body,
                    params={"sort": "interesting"},
                    headers={"X-Pubkey": f0_pub, "X-Jarvis-Sig": sig,
                             "Content-Type": "application/json"},
                )
                dt = time.perf_counter() - t0
                samples.append(dt)
            assert r.status_code == 200
            body_size = len(r.content)

    return {
        "friends_count": 10,
        "items_in_feed": 50,
        "median_latency_ms": round(statistics.median(samples) * 1000, 2),
        "p95_latency_ms": round(sorted(samples)[-1] * 1000, 2),
        "response_body_bytes": body_size,
        "response_body_kb": round(body_size / 1024, 2),
        "target_lt_100kb": body_size < 100 * 1024,
    }


# ----------------------------------------------------------------------
# Top-Level
# ----------------------------------------------------------------------

def main() -> None:
    print("=" * 72)
    print("AGGREGATOR (1y, 50 events/day = ~18k events)")
    print("=" * 72)
    res_agg = benchmark_aggregator()
    print(json.dumps(res_agg, indent=2))
    print()

    print("=" * 72)
    print("FEDERATION-PULL (10 friends, 50 items)")
    print("=" * 72)
    res_fed = asyncio.run(benchmark_federation_pull())
    print(json.dumps(res_fed, indent=2))


if __name__ == "__main__":
    main()
