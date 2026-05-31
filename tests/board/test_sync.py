"""SyncClient-Tests — laufen gegen ASGI-In-Memory-Backend ohne Netzwerk.

Statt einen echten httpx-Server zu starten, mounten wir die FastAPI-App
des Backends via ``httpx.ASGITransport``. Die Sig-Crypto + Replay-Logik
wird so 1:1 wie im Docker-Compose-Pfad geuebt — nur ohne TCP-Roundtrip.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

import httpx
import pytest

from board_backend.config import Settings
from board_backend.main import create_app
from jarvis.board.aggregator import BoardAggregator
from jarvis.board.evaluator import AchievementEvaluator
from jarvis.board.sync import SyncClient
from jarvis.core.events import ActionExecuted


class _MemKeyring:
    """In-Memory-Stub fuer ``keyring`` mit set_password/get_password."""

    def __init__(self, initial: dict | None = None) -> None:
        self._store: dict[tuple[str, str], str] = {}
        if initial:
            for (svc, key), val in initial.items():
                self._store[(svc, key)] = val

    def get_password(self, service: str, key: str) -> str | None:
        return self._store.get((service, key))

    def set_password(self, service: str, key: str, value: str) -> None:
        self._store[(service, key)] = value


def _ns(moment: datetime) -> int:
    return int(moment.timestamp() * 1e9)


def _make_jsonl(jsonl_dir: Path) -> None:
    base = datetime.now().astimezone()
    events = [{
        "ts_ns": _ns(base),
        "trace_id": "a" * 32,
        "event": "ActionExecuted",
        "layer": "orch",
        "payload": {"tool_name": "bash", "success": True, "duration_ms": 10},
    }, {
        "ts_ns": _ns(base + timedelta(minutes=1)),
        "trace_id": "b" * 32,
        "event": "TaskCompleted",
        "layer": "tasks",
        "payload": {"task_id": "t1", "duration_ms": 100},
    }]
    jsonl_dir.mkdir(parents=True, exist_ok=True)
    (jsonl_dir / "x.jsonl").write_text(
        "\n".join(json.dumps(e) for e in events), encoding="utf-8",
    )


@pytest.fixture
def backend(tmp_path: Path):
    settings = Settings(
        admin_token="test-admin",
        db_path=tmp_path / "backend.db",
        register_rate_limit_per_minute=100,
        replay_window_seconds=300,
    )
    app = create_app(settings=settings)
    return app


@pytest.fixture
def board(tmp_path: Path):
    jsonl = tmp_path / "flight_recorder"
    _make_jsonl(jsonl)
    db = tmp_path / "board" / "personal.db"
    agg = BoardAggregator(jsonl_dir=jsonl, db_path=db)
    agg.run()
    # Achievements vorab unlocken — durch eine Live-Action via Evaluator.
    ev = AchievementEvaluator(db)
    ev.attach()
    for tool in ("bash", "search_web", "write_file", "read_file", "grep_repo"):
        ev.evaluate_sync(ActionExecuted(
            trace_id=uuid4(), tool_name=tool, success=True, duration_ms=5,
        ))
    ev.close()
    return agg, db


@pytest.fixture
async def asgi_client(backend) -> httpx.AsyncClient:
    transport = httpx.ASGITransport(app=backend)
    async with httpx.AsyncClient(transport=transport, base_url="http://backend") as c:
        yield c


# ----------------------------------------------------------------------
# Tests
# ----------------------------------------------------------------------

@pytest.mark.asyncio
async def test_first_tick_registers_and_pushes(board, asgi_client) -> None:
    agg, db = board
    keyring = _MemKeyring(initial={
        ("jarvis-board", "admin_token"): "test-admin",
    })
    client = SyncClient(
        backend_url="http://backend",
        aggregator=agg,
        board_db_path=db,
        sync_interval_s=60,
        display_name="Test-Box",
        secrets=keyring,
        http_client=asgi_client,
    )
    # Privkey-Generierung ist Teil von start(); wir rufen das hier ohne start().
    client._privkey_hex, client._pubkey_hex = (
        # Generate via stub-keyring path
        __import__("board_backend.crypto", fromlist=["generate_keypair"]).generate_keypair()
    )
    keyring.set_password("jarvis-board", "sync_privkey_hex", client._privkey_hex)

    ok = await client.tick()
    assert ok is True
    assert client._registered

    # Zweiter Tick: Push only, kein Register-Call mehr.
    ok2 = await client.tick()
    assert ok2 is True


@pytest.mark.asyncio
async def test_payload_filters_extra_fields(board, asgi_client) -> None:
    """Auch wenn der lokale Aggregator-Export Schadensoutput liefert, filtert
    der SyncClient die Whitelist auf Daily-Keys."""
    agg, db = board
    keyring = _MemKeyring(initial={
        ("jarvis-board", "admin_token"): "test-admin",
    })
    client = SyncClient(
        backend_url="http://backend",
        aggregator=agg,
        board_db_path=db,
        secrets=keyring,
        http_client=asgi_client,
    )
    client._privkey_hex, client._pubkey_hex = (
        __import__("board_backend.crypto", fromlist=["generate_keypair"]).generate_keypair()
    )

    # Patch: aggregator liefert ein Schad-Feld zusaetzlich.
    orig = agg.export_all_for_federation
    def _bad():
        out = orig()
        if out["daily_stats"]:
            out["daily_stats"][0]["raw_transcript"] = "Mein Passwort ist hunter2"
        return out
    agg.export_all_for_federation = _bad  # type: ignore[method-assign]

    payload = client._build_payload()
    assert "raw_transcript" not in (payload["daily_stats"][0] if payload["daily_stats"] else {})


@pytest.mark.asyncio
async def test_unknown_admin_token_skips_register(board) -> None:
    """Ohne admin_token im Keyring: kein Register-Versuch, kein Crash."""
    agg, db = board
    keyring = _MemKeyring()  # leer

    # Custom transport that records calls
    calls: list[str] = []
    async def _handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        return httpx.Response(200)
    transport = httpx.MockTransport(_handler)

    async with httpx.AsyncClient(transport=transport, base_url="http://backend") as http:
        client = SyncClient(
            backend_url="http://backend",
            aggregator=agg,
            board_db_path=db,
            secrets=keyring,
            http_client=http,
        )
        client._privkey_hex, client._pubkey_hex = (
            __import__("board_backend.crypto", fromlist=["generate_keypair"]).generate_keypair()
        )
        result = await client.tick()
    assert result is False
    assert calls == []


@pytest.mark.asyncio
async def test_push_includes_unlocked_achievements(board, asgi_client) -> None:
    agg, db = board
    keyring = _MemKeyring(initial={
        ("jarvis-board", "admin_token"): "test-admin",
    })
    client = SyncClient(
        backend_url="http://backend",
        aggregator=agg,
        board_db_path=db,
        secrets=keyring,
        http_client=asgi_client,
    )
    client._privkey_hex, client._pubkey_hex = (
        __import__("board_backend.crypto", fromlist=["generate_keypair"]).generate_keypair()
    )

    payload = client._build_payload()
    ach_ids = {a["id"] for a in payload["achievements"]}
    assert "tool_dabbler" in ach_ids
    for a in payload["achievements"]:
        assert set(a.keys()).issubset({"id", "unlocked_at", "tier"})


@pytest.mark.asyncio
async def test_payload_does_not_carry_pii(board, asgi_client) -> None:
    agg, db = board
    keyring = _MemKeyring(initial={
        ("jarvis-board", "admin_token"): "test-admin",
    })
    client = SyncClient(
        backend_url="http://backend",
        aggregator=agg,
        board_db_path=db,
        secrets=keyring,
        http_client=asgi_client,
    )
    client._privkey_hex, client._pubkey_hex = (
        __import__("board_backend.crypto", fromlist=["generate_keypair"]).generate_keypair()
    )
    payload = client._build_payload()
    serialized = json.dumps(payload)
    for forbidden in ("transcript", "passwort", "credit-card", "args_preview"):
        assert forbidden.lower() not in serialized.lower()
