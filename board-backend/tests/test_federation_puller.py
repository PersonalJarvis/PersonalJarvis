"""Two isolated backends agree on anonymous public-feed synchronization."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from board_backend.background import FederationPuller
from board_backend.config import Settings
from board_backend.main import create_app
from board_backend.models import ActivityItem, Friend, Identity


def backend(path, owner):
    app = create_app(settings=Settings(admin_token="fixture-admin", db_path=path))  # noqa: S106
    app.state.disable_background = True
    with app.state.session_factory() as session:
        session.add(Identity(pubkey=owner, display_name="Fixture"))
        session.commit()
    return app


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():  # noqa: ASYNC110 - bounded polling of scheduler state under test
            await asyncio.sleep(0.01)


@pytest.mark.asyncio
async def test_public_pull_sync_and_live_friend_rescheduling(tmp_path):
    local = backend(tmp_path / "local.db", "a" * 64)
    remote = backend(tmp_path / "remote.db", "b" * 64)
    sf = local.state.session_factory
    with remote.state.session_factory() as session:
        for visibility in ("public", "friends", "private"):
            session.add(
                ActivityItem(
                    id=visibility, author_pubkey="b" * 64, kind="milestone", visibility=visibility
                )
            )
        session.add(
            ActivityItem(
                id="expired",
                author_pubkey="b" * 64,
                kind="story",
                visibility="public",
                expires_at=datetime.now(UTC) - timedelta(hours=1),
            )
        )
        session.commit()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=remote), base_url="http://remote"
    ) as client:
        puller = FederationPuller(session_factory=sf, http_client=client)
        await puller.start()
        try:
            # The signed endpoint keeps its existing authentication contract.
            assert (await client.get("/api/v1/federation/feed")).status_code == 422
            assert [item["id"] for item in await puller._pull_once("http://remote")] == ["public"]
            with sf() as session:
                session.add(
                    Friend(
                        owner_pubkey="a" * 64, friend_pubkey="b" * 64, friend_url="http://remote"
                    )
                )
                session.commit()
            puller.friends_changed()
            key = "a" * 64 + "|" + "b" * 64
            await until(lambda: puller.last_results.get(key, {}).get("ok"))
            first_task = puller._tasks[key]
            with sf() as session:
                friend = session.get(Friend, ("a" * 64, "b" * 64))
                assert friend.last_pull_at is not None
                friend.pull_interval_s = 300
                session.commit()
            puller.friends_changed()
            await until(lambda: puller._tasks.get(key) is not first_task)
            assert first_task.done()
            with sf() as session:
                session.delete(session.get(Friend, ("a" * 64, "b" * 64)))
                session.commit()
            puller.friends_changed()
            await until(lambda: not puller._tasks)
        finally:
            await puller.stop()
        assert puller._scheduler is None


@pytest.mark.asyncio
async def test_rejected_pull_does_not_record_success(tmp_path):
    local = backend(tmp_path / "local.db", "a" * 64)
    sf = local.state.session_factory
    with sf() as session:
        session.add(
            Friend(owner_pubkey="a" * 64, friend_pubkey="b" * 64, friend_url="http://remote")
        )
        session.commit()
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda request: httpx.Response(401))
    ) as client:
        puller = FederationPuller(session_factory=sf, http_client=client)
        await puller.start()
        try:
            key = "a" * 64 + "|" + "b" * 64
            await until(lambda: key in puller.last_results)
            assert puller.last_results[key] == {"ok": False, "error": "pull_failed"}
            with sf() as session:
                assert session.get(Friend, ("a" * 64, "b" * 64)).last_pull_at is None
        finally:
            await puller.stop()
