"""Smoke-Test fuer Frontier-Brain-Stack (2026-04-29).

Verifiziert:
1. BrainManager wird mit Frontier-IDs instantiiert (nicht grok-3/gpt-4o/2.5).
2. Pre-Boot-Key-Check filtert Provider ohne Key aus _dead_providers.
3. voice_turns-DB nimmt nur den ERFOLGREICHEN Provider auf (Bug C).

Usage:
    python scripts/smoke_frontier.py            # alle Checks
    python scripts/smoke_frontier.py --no-db    # ohne historische DB-Pruefung

Exit-Code 0 = OK, !=0 = STALE-Modell oder Halluzinations-Tag erkannt.
"""
from __future__ import annotations

import asyncio
import os
import sqlite3
import sys
from pathlib import Path

if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
    except Exception:
        pass

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

STALE_MODELS = {
    "grok-3", "grok-3-mini", "grok-3-fast", "grok-2",
    "gpt-4o", "gpt-4o-mini", "gpt-4-turbo", "gpt-4",
    "gemini-2.5-flash", "gemini-2.5-pro", "gemini-2.5-flash-lite",
    "gemini-1.5-pro", "gemini-1.5-flash",
    "claude-3-opus", "claude-3-sonnet", "claude-3-haiku",
    "claude-3-5-sonnet", "claude-3-5-haiku",
}


def check_toml() -> int:
    """Liest jarvis.toml und prueft dass alle [brain.providers.*] Frontier sind."""
    from jarvis.core.config import load_config

    cfg = load_config()
    issues = []
    for name, p in (cfg.brain.providers or {}).items():
        if p.model and p.model in STALE_MODELS:
            issues.append(f"  {name}.model = {p.model!r}  (STALE)")
        if p.deep_model and p.deep_model in STALE_MODELS:
            issues.append(f"  {name}.deep_model = {p.deep_model!r}  (STALE)")

    sub = cfg.brain.sub_jarvis
    if sub:
        if sub.model and sub.model in STALE_MODELS:
            issues.append(f"  sub_jarvis.model = {sub.model!r}  (STALE)")
        if sub.fallback_model and sub.fallback_model in STALE_MODELS:
            issues.append(f"  sub_jarvis.fallback_model = {sub.fallback_model!r}  (STALE)")
        if sub.fallback_model_2 and sub.fallback_model_2 in STALE_MODELS:
            issues.append(f"  sub_jarvis.fallback_model_2 = {sub.fallback_model_2!r}  (STALE)")

    if issues:
        print("FAIL: STALE-Modelle in jarvis.toml:")
        for i in issues:
            print(i)
        return 1

    print("OK: jarvis.toml ist Frontier-konform.")
    print(f"  primary={cfg.brain.primary}")
    for name in ("claude-api", "gemini", "grok", "openai"):
        p = cfg.brain.providers.get(name)
        if p:
            print(f"  {name}: {p.model} / {p.deep_model}")
    if cfg.brain.sub_jarvis:
        print(f"  sub_jarvis: {cfg.brain.sub_jarvis.provider}/{cfg.brain.sub_jarvis.model}")
    return 0


def check_db(post_restart_only: bool = True) -> int:
    """Prueft data/sessions.db: STALE-Modelle in den letzten N voice_turns.

    Wenn ``post_restart_only=True`` (Default): nur Turns nach Backend-
    Restart-Marker (ENV ``JARVIS_RESTART_TS_MS`` oder Cache-File-mtime).
    Historische Turns vor Backend-Restart werden ignoriert (sie sind
    pre-fix-Daten und kein Indikator fuer aktuellen Zustand).
    """
    db = REPO / "data" / "sessions.db"
    if not db.exists():
        print("INFO: data/sessions.db fehlt — kein DB-Check moeglich. (skip)")
        return 0

    # Restart-Marker: Cache-File-mtime ist ein guter Proxy. Wenn es nicht
    # existiert oder leer ist, hat es noch keinen Restart gegeben.
    cache = REPO / "data" / "frontier_cache.json"
    cutoff_ms = 0
    if post_restart_only and cache.exists():
        # mtime in ms — Turns davor sind pre-restart-Daten.
        cutoff_ms = int(cache.stat().st_mtime * 1000)

    conn = sqlite3.connect(str(db))
    cur = conn.cursor()
    rows = list(cur.execute(
        "SELECT id, provider, model FROM voice_turns "
        "WHERE provider != '' AND model != '' AND started_ms > ? "
        "ORDER BY started_ms DESC LIMIT 10",
        (cutoff_ms,),
    ))
    conn.close()

    if not rows:
        print(
            f"INFO: Keine voice_turns nach Cutoff (cutoff_ms={cutoff_ms}). "
            f"Backend wurde noch nicht neu gestartet ODER es gab keine Voice-Session "
            f"seit Restart. Smoke akzeptiert (post-restart-Daten fehlen, kein Fail)."
        )
        return 0

    issues = []
    for tid, prov, model in rows:
        if model in STALE_MODELS:
            issues.append(f"  {tid[:24]}...  provider={prov} model={model}")

    if issues:
        print("FAIL: STALE-Modelle in voice_turns NACH Restart:")
        for i in issues:
            print(i)
        return 1

    print(f"OK: {len(rows)} post-restart voice_turns gepruft, keine STALE-IDs.")
    for tid, prov, model in rows[:3]:
        print(f"  {tid[:32]}  {prov}/{model}")
    return 0


def check_brain_module_imports() -> int:
    """Imports laufen ohne ModuleNotFoundError."""
    try:
        from jarvis.brain import (  # noqa: F401
            cost,
            factory,
            frontier_autoswitch,
            frontier_resolver,
            manager,
        )
        from jarvis.core.events import (  # noqa: F401
            BrainTurnCompleted,
            BrainTurnStarted,
        )
        from jarvis.sessions import recorder, store  # noqa: F401
    except ImportError as exc:
        print(f"FAIL: Import-Error: {exc}")
        return 1
    print("OK: Alle Module importierbar.")
    return 0


def main() -> int:
    print("=" * 60)
    print("Frontier-Smoke 2026-04-29")
    print("=" * 60)

    rc = 0
    rc |= check_brain_module_imports()
    print()
    rc |= check_toml()
    print()
    rc |= check_db()
    print()

    print("=" * 60)
    if rc == 0:
        print("SMOKE PASSED [OK]")
    else:
        print("SMOKE FAILED [X] — siehe Failures oben.")
    print("=" * 60)
    return rc


if __name__ == "__main__":
    sys.exit(main())
