"""Background-Tasks im Backend (Phase D).

- ``StoriesCleanup`` — nightly job, loescht ``activity_items`` mit
  abgelaufener ``expires_at``.
- ``FederationPuller`` — pollt jede Friend-URL ihrem konfigurierten
  Intervall nach (Default 120 s, per-Friend ueberschreibbar).

Beide laufen als ``asyncio.Task``, attached an die FastAPI-App via
``startup``/``shutdown``-Lifespan.
"""
from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta, timezone
from typing import Any

import httpx
from sqlalchemy.orm import Session

from .models import ActivityItem, Friend

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------
# Stories-Cleanup
# ----------------------------------------------------------------------

class StoriesCleanup:
    """Loescht abgelaufene ``activity_items`` (typ. Stories nach 24 h).

    Tickt alle ``interval_s`` (Default 1 h). Plan §D nennt „nightly" — das
    ist ungefaehr; ein 1h-Tick toleriert App-Restarts und gibt schnellere
    Cleanup-Garantie ohne signifikanten DB-Cost.
    """

    def __init__(self, *, session_factory, interval_s: float = 3600.0) -> None:
        self._sf = session_factory
        self._interval_s = interval_s
        self._task: asyncio.Task[None] | None = None

    async def start(self) -> None:
        if self._task is None or self._task.done():
            self._task = asyncio.create_task(self._loop(), name="stories-cleanup")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await self._task
        except (asyncio.CancelledError, Exception):
            pass
        self._task = None

    async def _loop(self) -> None:
        # Erster Run sofort, danach Intervall.
        while True:
            try:
                self.run_once()
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("stories-cleanup tick failed")
            try:
                await asyncio.sleep(self._interval_s)
            except asyncio.CancelledError:
                raise

    def run_once(self) -> int:
        """Synchron + idempotent. Returns count gelöschter Items."""
        now = datetime.now(timezone.utc)
        with self._sf() as session:
            expired = session.query(ActivityItem).filter(
                ActivityItem.expires_at.isnot(None),
                ActivityItem.expires_at < now,
            ).all()
            for it in expired:
                session.delete(it)
            session.commit()
            return len(expired)


# ----------------------------------------------------------------------
# Federation-Puller
# ----------------------------------------------------------------------

class FederationPuller:
    """Polled jede Friend-URL gemaess ``Friend.pull_interval_s``.

    Pro Friend ein eigener ``asyncio.Task`` — wenn ein Friend offline ist,
    blockiert er die anderen nicht.

    Aktuell: pullt nur den Feed (``/api/v1/federation/feed``) und persistiert
    NICHT — der Output dient dem UI, nicht der DB. Phase E koennte einen
    Cache-Layer dazwischen schalten.
    """

    def __init__(self, *, session_factory, http_client: httpx.AsyncClient | None = None) -> None:
        self._sf = session_factory
        self._http = http_client
        self._owns_http = http_client is None
        self._tasks: dict[str, asyncio.Task[None]] = {}
        self._last_results: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        if self._http is None:
            self._http = httpx.AsyncClient(timeout=10.0)
        # Alle aktiven Friends pollen.
        with self._sf() as session:
            friends = session.query(Friend).all()
            for f in friends:
                self._spawn_for(f)

    async def stop(self) -> None:
        for t in list(self._tasks.values()):
            t.cancel()
        for t in list(self._tasks.values()):
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        self._tasks.clear()
        if self._owns_http and self._http is not None:
            await self._http.aclose()

    def _spawn_for(self, friend: Friend) -> None:
        key = f"{friend.owner_pubkey}|{friend.friend_pubkey}"
        if key in self._tasks and not self._tasks[key].done():
            return
        self._tasks[key] = asyncio.create_task(
            self._friend_loop(friend.friend_pubkey, friend.friend_url, friend.pull_interval_s),
            name=f"fed-pull-{friend.friend_pubkey[:8]}",
        )

    async def _friend_loop(self, friend_pubkey: str, friend_url: str, interval_s: int) -> None:
        while True:
            try:
                items = await self._pull_once(friend_url)
                self._last_results[friend_pubkey] = {
                    "items_count": len(items),
                    "fetched_at": datetime.now(timezone.utc).isoformat(),
                }
                self._update_last_pull(friend_pubkey)
            except asyncio.CancelledError:
                raise
            except Exception:  # noqa: BLE001
                log.exception("federation pull from %s failed", friend_url)
            try:
                await asyncio.sleep(max(60, interval_s))
            except asyncio.CancelledError:
                raise

    async def _pull_once(self, friend_url: str) -> list[Any]:
        """Anonymer Pull — der Feed selbst filtert visibility=public.

        Fuer einen authenticated friend-Pull braeuchten wir den lokalen
        Privkey + Pubkey, die das Backend nicht hat (siehe Reactions-Note).
        Phase D-MVP: nur public items. Phase D-Vollausbau: lokales Jarvis
        liefert dem Backend einen scoped Auth-Header pro Friend.
        """
        assert self._http is not None
        resp = await self._http.get(f"{friend_url.rstrip('/')}/api/v1/federation/feed",
                                    params={"sort": "interesting"})
        if resp.status_code != 200:
            return []
        return resp.json().get("items", [])

    def _update_last_pull(self, friend_pubkey: str) -> None:
        with self._sf() as session:
            row = session.query(Friend).filter(
                Friend.friend_pubkey == friend_pubkey
            ).first()
            if row is not None:
                row.last_pull_at = datetime.now(timezone.utc)
                session.commit()

    @property
    def last_results(self) -> dict[str, dict[str, Any]]:
        return dict(self._last_results)
