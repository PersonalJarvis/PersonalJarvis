"""Background tasks in the backend (Phase D).

- ``StoriesCleanup`` — nightly job that deletes ``activity_items`` with
  an expired ``expires_at``.
- ``FederationPuller`` — polls each friend URL at its configured
  interval (default 120 s, overridable per friend).

Both run as an ``asyncio.Task``, attached to the FastAPI app via the
``startup``/``shutdown`` lifespan.
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
# Stories cleanup
# ----------------------------------------------------------------------

class StoriesCleanup:
    """Deletes expired ``activity_items`` (typically stories after 24 h).

    Ticks every ``interval_s`` (default 1 h). Plan §D calls this
    "nightly" — that's approximate; a 1 h tick tolerates app restarts
    and gives a faster cleanup guarantee without a significant DB cost.
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
        # First run immediately, then at the interval.
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
        """Synchronous + idempotent. Returns the count of deleted items."""
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
# Federation puller
# ----------------------------------------------------------------------

class FederationPuller:
    """Polls each friend URL per ``Friend.pull_interval_s``.

    One ``asyncio.Task`` per friend — if a friend is offline, it doesn't
    block the others.

    Currently: only pulls the feed (``/api/v1/federation/feed``) and does
    NOT persist it — the output serves the UI, not the DB. Phase E could
    insert a cache layer in between.
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
        # Poll all active friends.
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
        """Anonymous pull — the feed itself filters visibility=public.

        For an authenticated friend pull we'd need the local privkey +
        pubkey, which the backend doesn't have (see the reactions note).
        Phase D MVP: public items only. Phase D full build-out: the local
        Jarvis supplies the backend a scoped auth header per friend.
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
