"""Board-Demo-Bootstrap (v1.0).

Spawnt zwei in-Process ASGI-Backends (Alice + Bob), erzeugt fünf
synthetische Identitäten, paired sie kreuzweise und befüllt jedes
Backend mit ca. 30 Tagen Random-Activity (Achievements + Stories +
Reactions).

Zweck:
- **Demo**: Screenshots fürs README ohne echte Friends.
- **Performance-Audit**: liefert die Datenbasis für ``board_perf.py``.
- **Tests**: ein einziger Befehl bringt einen vollständig befüllten
  Federation-Stand auf — manuelle Pair-Roundtrips entfallen.

Aufruf::

    python tools/board_demo.py --out /tmp/board_demo
    # spawn 2 Backends als Subprozesse, schreibt URLs in stdout
    python tools/board_demo.py --backend1 :18001 --backend2 :18002

Sicherheits-Hinweis: das Skript ist *Dev-Only*. Es erzeugt Keypairs
ohne sie ausser-Process zu speichern — keine Produktiv-Identities.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import random
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

# Re-use Backend-internals.
from board_backend.config import Settings
from board_backend.crypto import canonical_json, generate_keypair, sign
from board_backend.main import create_app


# ----------------------------------------------------------------------
# Synthetic content pool
# ----------------------------------------------------------------------

ACHIEVEMENT_IDS = [
    "first_mcp", "tool_dabbler", "tool_journeyman", "tool_master",
    "triple_combo", "sub_jarvis_summoner", "centennial",
    "ten_x_engineer", "kilo_club", "one_year_with_jarvis",
]

STORY_TEXTS = [
    "Heute den Tool-Master geknackt — 30 verschiedene CLIs in 14 Tagen genutzt.",
    "Sub-Jarvis hat einen Refactor durchgezogen, während ich Mittag gegessen habe.",
    "Voice-First-Try-Rate über 95 % seit drei Tagen. Kein Zufall.",
    "Erste eigene MCP-Bridge integriert. Lief auf Anhieb.",
    "Triple-Combo: bash → grep → write_file. In einer Session, ein Take.",
    "Die Aggregator-Pipeline lief 30 Tage durch ohne einen einzigen Crash.",
    "Habe den Privkey aus dem Credential Manager exportiert. Backup im Tresor.",
    "Mein Board zeigt jetzt eine echte Streak. Wieder mal mit Jarvis durchgezogen.",
]

DISPLAY_NAMES = ["Ada", "Bjorn", "Camille", "Diego", "Eli"]


# ----------------------------------------------------------------------
# Identity + Backend Wrapper
# ----------------------------------------------------------------------

@dataclass
class DemoIdentity:
    name: str
    privkey: str
    pubkey: str
    backend_id: str        # "alice" | "bob"


@dataclass
class DemoBackend:
    name: str
    settings: Settings
    app: Any = field(default=None)
    client: httpx.AsyncClient = field(default=None)


async def _start_backend(name: str, db_path: Path, admin_token: str) -> DemoBackend:
    settings = Settings(
        admin_token=admin_token,
        db_path=db_path,
        register_rate_limit_per_minute=1000,
        replay_window_seconds=300,
    )
    app = create_app(settings=settings)
    app.state.disable_background = True       # kein FederationPuller-noise
    transport = httpx.ASGITransport(app=app)
    client = httpx.AsyncClient(transport=transport, base_url=f"http://{name}", timeout=10)
    return DemoBackend(name=name, settings=settings, app=app, client=client)


async def _register(b: DemoBackend, ident: DemoIdentity) -> None:
    r = await b.client.post(
        "/api/v1/identity/register",
        json={"pubkey": ident.pubkey, "display_name": ident.name},
        headers={"X-Admin-Token": b.settings.admin_token},
    )
    r.raise_for_status()


async def _signed(method: str, b: DemoBackend, path: str, *, ident: DemoIdentity,
                  payload: dict, params: dict | None = None) -> httpx.Response:
    body = canonical_json(payload)
    sig = sign(payload, privkey_hex=ident.privkey)
    return await b.client.request(
        method, path, content=body, params=params or {},
        headers={"X-Pubkey": ident.pubkey, "X-Jarvis-Sig": sig,
                 "Content-Type": "application/json"},
    )


# ----------------------------------------------------------------------
# Pair + Friend Setup
# ----------------------------------------------------------------------

async def _pair(host: DemoBackend, host_ident: DemoIdentity,
                friend: DemoIdentity, friend_url: str) -> None:
    init = await host.client.post(
        "/api/v1/pair/initiate", json={},
        headers={"X-Admin-Token": host.settings.admin_token},
    )
    init.raise_for_status()
    token = init.json()["token"]
    accept = await host.client.post("/api/v1/pair/accept", json={
        "token": token,
        "friend_pubkey": friend.pubkey,
        "friend_url": friend_url,
        "friend_display_name": friend.name,
    })
    accept.raise_for_status()


# ----------------------------------------------------------------------
# Random Activity-Generator
# ----------------------------------------------------------------------

def _now_ms() -> int:
    return int(time.time() * 1000)


async def _seed_activity(
    b: DemoBackend, owner: DemoIdentity, *, days: int, rng: random.Random,
) -> None:
    """Erzeugt ~3 Items pro Tag, gemischt aus Achievements + Stories.

    Backdating via direktem DB-Write — wir gehen *durch* die ORM-Schicht,
    aber überschreiben ``created_at`` und ``expires_at``, damit der Feed
    eine glaubhafte Zeitachse hat.
    """
    from board_backend.models import ActivityItem
    factory = b.app.state.session_factory
    base = datetime.now(timezone.utc).replace(hour=12, minute=0, second=0, microsecond=0)
    items_per_day = (2, 4)
    visibilities = ("friends", "friends", "friends", "public", "private")

    with factory() as session:
        for day in range(days):
            ts = base - timedelta(days=day, minutes=rng.randint(0, 600))
            count = rng.randint(*items_per_day)
            for _ in range(count):
                kind = rng.choices(("achievement_unlocked", "story"), weights=(0.6, 0.4))[0]
                vis = rng.choice(visibilities)
                if kind == "achievement_unlocked":
                    payload = {"achievement_id": rng.choice(ACHIEVEMENT_IDS)}
                    expires_at = None
                else:
                    payload = {"text": rng.choice(STORY_TEXTS)}
                    # Stories die schon "live" waren bekommen expires_at, alte
                    # Stories sind faktisch gelöscht (skip).
                    if day < 1:
                        expires_at = ts + timedelta(hours=24)
                    else:
                        continue
                session.add(ActivityItem(
                    id=_random_id(rng),
                    author_pubkey=owner.pubkey,
                    kind=kind,
                    payload=json.dumps(payload, sort_keys=True),
                    created_at=ts,
                    visibility=vis,
                    expires_at=expires_at,
                ))
        session.commit()


def _random_id(rng: random.Random) -> str:
    return "".join(rng.choices("0123456789abcdef", k=32))


async def _seed_reactions(
    backend: DemoBackend, author: DemoIdentity, reactors: list[DemoIdentity],
    rng: random.Random,
) -> None:
    """Verteilt zufällige Reactions auf die Items des author."""
    from board_backend.models import ActivityItem, Reaction
    factory = backend.app.state.session_factory
    REACTIONS = ("rocket", "brain", "fire")
    with factory() as session:
        items = session.query(ActivityItem).filter(
            ActivityItem.author_pubkey == author.pubkey,
            ActivityItem.visibility != "private",
        ).all()
        for item in items:
            for r in reactors:
                if rng.random() < 0.35:
                    try:
                        session.add(Reaction(
                            item_id=item.id, reactor_pubkey=r.pubkey,
                            reaction=rng.choice(REACTIONS),
                        ))
                        session.flush()
                    except Exception:  # noqa: BLE001
                        session.rollback()
        session.commit()


# ----------------------------------------------------------------------
# Top-Level
# ----------------------------------------------------------------------

async def bootstrap(
    out_dir: Path, *, days: int = 30, seed: int | None = None,
) -> dict[str, Any]:
    """Spawnt zwei Backends, paired, befüllt — und gibt einen Status-
    Report zurück, den der Caller (board_perf, README-Recipe) konsumieren
    kann.
    """
    rng = random.Random(seed if seed is not None else 42)
    out_dir.mkdir(parents=True, exist_ok=True)

    alice = await _start_backend("alice", out_dir / "alice.db", admin_token="alice-admin")
    bob = await _start_backend("bob", out_dir / "bob.db", admin_token="bob-admin")

    # Owner-Identitäten (sind die einzigen registrierten Identities pro Backend).
    a_priv, a_pub = generate_keypair()
    b_priv, b_pub = generate_keypair()
    alice_id = DemoIdentity("Alice", a_priv, a_pub, "alice")
    bob_id = DemoIdentity("Bob", b_priv, b_pub, "bob")

    await _register(alice, alice_id)
    await _register(bob, bob_id)

    # Drei zusätzliche pseudo-Friends pro Backend (kein eigenes Backend,
    # nur Sig-fähige Identitäten für Reactions).
    extras: list[DemoIdentity] = []
    for name in DISPLAY_NAMES:
        priv, pub = generate_keypair()
        extras.append(DemoIdentity(name, priv, pub, "ext"))

    # Bidirektionale Pair-Beziehungen Alice ↔ Bob (Standard-Demo).
    await _pair(alice, alice_id, bob_id, friend_url="http://bob")
    await _pair(bob,   bob_id,   alice_id, friend_url="http://alice")

    # Pseudo-Friends als Friend-Rows direkt in beide DBs schreiben.
    from board_backend.models import Friend
    for backend, owner in ((alice, alice_id), (bob, bob_id)):
        with backend.app.state.session_factory() as session:
            for ext in extras:
                if session.get(Friend, (owner.pubkey, ext.pubkey)) is None:
                    session.add(Friend(
                        owner_pubkey=owner.pubkey, friend_pubkey=ext.pubkey,
                        friend_url=f"http://demo-{ext.name.lower()}",
                        friend_display_name=ext.name,
                        paired_at=datetime.now(timezone.utc),
                        pull_interval_s=120,
                    ))
            session.commit()

    # Activity + Reactions pro Backend.
    await _seed_activity(alice, alice_id, days=days, rng=rng)
    await _seed_activity(bob, bob_id, days=days, rng=rng)
    await _seed_reactions(alice, alice_id, [bob_id, *extras], rng)
    await _seed_reactions(bob, bob_id, [alice_id, *extras], rng)

    # Live-Probe für Status-Report.
    me_payload = {"ts_ms": _now_ms()}
    r = await _signed("GET", alice, "/api/v1/me", ident=alice_id, payload=me_payload)
    alice_me = r.json()
    r = await _signed("GET", alice, "/api/v1/federation/feed", ident=alice_id, payload=me_payload)
    alice_feed_count = len(r.json()["items"])

    summary = {
        "alice_db": str(alice.settings.db_path),
        "bob_db": str(bob.settings.db_path),
        "alice_pubkey": alice_id.pubkey,
        "bob_pubkey": bob_id.pubkey,
        "alice_friends": 1 + len(extras),
        "alice_me": alice_me,
        "alice_feed_items": alice_feed_count,
        "days_seeded": days,
    }

    await alice.client.aclose()
    await bob.client.aclose()
    return summary


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap a Jarvis-Board demo state.")
    p.add_argument("--out", type=Path, default=Path("data/board_demo"),
                   help="Verzeichnis für DB-Dateien (Default: data/board_demo).")
    p.add_argument("--days", type=int, default=30,
                   help="Anzahl der Tage Random-Activity (Default: 30).")
    p.add_argument("--seed", type=int, default=None,
                   help="RNG-Seed für deterministische Demo (Default: 42).")
    return p.parse_args()


def main() -> None:
    args = _parse_args()
    summary = asyncio.run(bootstrap(args.out, days=args.days, seed=args.seed))
    print(json.dumps(summary, indent=2, default=str))


if __name__ == "__main__":
    main()
