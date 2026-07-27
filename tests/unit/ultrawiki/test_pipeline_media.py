"""The media enrichment lane, end to end against a real store, fully offline.

Two things are pinned here that nothing else could catch:

* **Connector metadata survives the store.** It did not, for the store's whole
  life, and nothing noticed — connectors attached it to every RawItem and it
  was dropped on the way in. Nothing depended on it until a photo had to carry
  the reference back to its own bytes.
* **The lane defers.** Describing a picture costs a model call and a photo
  library is tens of thousands of them, so it must never delay the keyword
  indexing of the text that arrived alongside.
"""

from __future__ import annotations

import dataclasses
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from jarvis.ultrawiki.pipeline import PipelineWorker
from jarvis.ultrawiki.store import UltraStore
from jarvis.ultrawiki.types import ConsentState, ItemState, RawItem

#: A stand-in photo. Sized like a real one on purpose: the enrichment lane
#: skips pictures too small to hold readable content, so a 40-byte fixture
#: would exercise the skip path instead of the description path.
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 80_000

#: These workers use an INJECTED distiller, so the production credential probe
#: must never run — a test whose outcome depends on the host's keys is the
#: AP-23 trap.
DISTILL_READY = lambda: (True, "")  # noqa: E731 — a one-line test seam


async def distill_never(cfg: Any, *, title: str, body: str, source_kind: str) -> Any:
    raise AssertionError("distill_fn must not be called in this test")


def media_cfg(mode: str = "frugal") -> SimpleNamespace:
    return SimpleNamespace(
        ultrawiki=SimpleNamespace(
            enabled=True,
            db_backend="sqlite",
            embedding_provider="",
            embedding_model="",
            distill_provider="",
            distill_model="",
            rerank_provider="",
            ollama_endpoint="",
            media_enrich=mode,
        ),
        memory=SimpleNamespace(data_dir="unused"),
        brain=SimpleNamespace(primary=""),
    )


def worker_for(store: Any, cfg: Any) -> PipelineWorker:
    return PipelineWorker(
        store,
        cfg,
        embedding_backend_factory=lambda: None,
        distill_fn=distill_never,
        distill_ready_fn=DISTILL_READY,
    )


def text_item(index: int) -> RawItem:
    return RawItem(
        external_id=f"ext-{index:04d}",
        body=f"body text number {index}",
        permalink=f"app://item/{index}",
        timestamp_utc="2026-01-01T00:00:00Z",
        title=f"Item {index}",
    )


def photo_item(index: int, path: Path, *, pending: bool = True) -> RawItem:
    """An item shaped exactly as the folder walk produces one for a photo."""
    return RawItem(
        external_id=f"photo-{index:04d}.png",
        body=(
            f"File: photo-{index:04d}.png\nFolder: 2019\n"
            "Taken: 2019-08-14T17:03:22Z"
        ),
        permalink=path.as_uri(),
        timestamp_utc="2019-08-14T17:03:22Z",
        title=f"photo-{index:04d}",
        metadata={
            "media_kind": "image",
            "enrich_pending": pending,
            "captured_at": "2019-08-14T17:03:22Z",
            "media_ref_kind": "file",
            "media_ref_path": str(path),
        },
    )


@pytest.fixture
async def store(tmp_path: Path):
    instance = UltraStore(tmp_path / "ultrawiki.db")
    await instance.upsert_source("src1", connector="local-folder", label="Test source")
    await instance.set_consent("src1", ConsentState.APPROVED)
    yield instance
    await instance.close()


async def only_item(store: Any, external_id: str = "") -> dict[str, Any]:
    rows, _total = await store.list_items(source_id="src1", limit=50)
    if external_id:
        rows = [row for row in rows if str(row.get("title", "")).startswith("photo")]
    item = await store.get_item(int(rows[0]["id"]))
    assert item is not None
    return item


def patch_describe(monkeypatch: pytest.MonkeyPatch, fn: Any) -> None:
    import jarvis.ultrawiki.media_enrich as enrich_mod

    monkeypatch.setattr(enrich_mod, "describe_image", fn)


# ---------------------------------------------------------------------------
# Metadata reaching the store at all
# ---------------------------------------------------------------------------


class TestMetadataSurvivesTheStore:
    async def test_connector_metadata_is_persisted_and_read_back(self, store, tmp_path):
        await store.upsert_items("src1", [photo_item(1, tmp_path / "a.png")])
        item = await only_item(store)
        assert item["metadata"]["media_kind"] == "image"
        assert item["metadata"]["enrich_pending"] is True
        assert item["metadata"]["media_ref_path"] == str(tmp_path / "a.png")

    async def test_an_item_without_metadata_reads_as_an_empty_mapping(self, store):
        await store.upsert_items("src1", [text_item(1)])
        item = await only_item(store)
        assert item["metadata"] == {}

    async def test_unserialisable_metadata_costs_that_item_its_metadata_only(
        self, store, tmp_path
    ):
        """One odd value must never cost the whole batch its transaction."""
        odd = RawItem(
            external_id="odd",
            body="body",
            permalink="app://odd",
            timestamp_utc="2026-01-01T00:00:00Z",
            title="Odd",
            metadata={"handle": object()},
        )
        counts = await store.upsert_items("src1", [odd, text_item(2)])
        assert counts.new == 2


# ---------------------------------------------------------------------------
# The backlog query
# ---------------------------------------------------------------------------


class TestMediaBacklog:
    async def test_only_items_actually_waiting_are_claimed(self, store, tmp_path):
        await store.upsert_items(
            "src1",
            [
                photo_item(1, tmp_path / "a.png"),
                photo_item(2, tmp_path / "b.png", pending=False),
                text_item(3),
            ],
        )
        pending = await store.pending_media_items(limit=10)
        assert [row["external_id"] for row in pending] == ["photo-0001.png"]

    async def test_a_store_with_no_media_returns_nothing(self, store):
        await store.upsert_items("src1", [text_item(1)])
        assert await store.pending_media_items(limit=10) == []


# ---------------------------------------------------------------------------
# The pass itself
# ---------------------------------------------------------------------------


class TestMediaPass:
    async def test_a_described_photo_keeps_its_file_facts_and_gains_the_text(
        self, store, tmp_path, monkeypatch
    ):
        """The description is APPENDED: the facts underneath are what make the
        photo findable by time and place, and prose does not supersede them."""
        photo = tmp_path / "a.png"
        photo.write_bytes(PNG)
        await store.upsert_items("src1", [photo_item(1, photo)])

        async def _describe(data, *, filename, cfg, **kwargs):
            from jarvis.ultrawiki.media_enrich import EnrichResult

            assert data.startswith(b"\x89PNG"), "the real bytes must reach the model"
            assert filename == "a.png"
            return EnrichResult(
                text="Two people on a beach.", ok=True, provider="seeing"
            )

        patch_describe(monkeypatch, _describe)
        assert await worker_for(store, media_cfg())._media_pass() == 1

        item = await only_item(store)
        assert "Two people on a beach." in item["body_raw"]
        assert "Taken: 2019-08-14T17:03:22Z" in item["body_raw"]
        assert item["metadata"]["enrich_pending"] is False
        assert item["metadata"]["enriched_by"] == "seeing"
        # A changed body sends the item back through the normal ladder, so the
        # description is embedded and distilled like any other text.
        assert item["state"] == ItemState.CAPTURED.value
        assert await store.pending_media_items(limit=10) == []

    async def test_a_failure_leaves_the_item_findable_and_still_queued(
        self, store, tmp_path, monkeypatch
    ):
        photo = tmp_path / "a.png"
        photo.write_bytes(PNG)
        await store.upsert_items("src1", [photo_item(1, photo)])

        async def _describe(data, *, filename, cfg, **kwargs):
            from jarvis.ultrawiki.media_enrich import EnrichResult

            return EnrichResult(reason="no provider can read images")

        patch_describe(monkeypatch, _describe)
        await worker_for(store, media_cfg())._media_pass()

        item = await only_item(store)
        assert "Taken: 2019-08-14T17:03:22Z" in item["body_raw"]
        assert item["metadata"]["enrich_error"] == "no provider can read images"
        # Still queued: connecting a capable provider later must drain it.
        assert item["metadata"]["enrich_pending"] is True

    async def test_a_deleted_original_stops_being_retried(self, store, tmp_path):
        """A file removed between import and description is ordinary; retrying
        it forever would be a loop with no end."""
        await store.upsert_items("src1", [photo_item(1, tmp_path / "gone.png")])
        await worker_for(store, media_cfg())._media_pass()

        item = await only_item(store)
        assert item["metadata"]["enrich_pending"] is False
        assert "no longer where it was imported from" in item["metadata"]["enrich_error"]

    async def test_off_means_nothing_is_touched(self, store, tmp_path):
        photo = tmp_path / "a.png"
        photo.write_bytes(PNG)
        await store.upsert_items("src1", [photo_item(1, photo)])
        assert await worker_for(store, media_cfg("off"))._media_pass() == 0
        assert len(await store.pending_media_items(limit=10)) == 1

    async def test_a_stalled_lane_stops_rescanning_the_corpus(
        self, store, tmp_path, monkeypatch
    ):
        """An item that stays queued must not be re-fetched every 100 ms.

        The backlog query is a full scan of the item table — the pending flag
        lives inside metadata_json, so no index can serve it — and the lane
        above deliberately leaves a failed item queued for a provider that may
        arrive later. Those two together spun a tight circle that re-read a
        236 k-row corpus ten times a second for a whole day (2026-07-27). The
        item stays queued, as it must; what stops is the asking.
        """
        photo = tmp_path / "a.png"
        photo.write_bytes(PNG)
        await store.upsert_items("src1", [photo_item(1, photo)])

        async def _describe(data, *, filename, cfg, **kwargs):
            from jarvis.ultrawiki.media_enrich import EnrichResult

            return EnrichResult(reason="no provider can read images")

        patch_describe(monkeypatch, _describe)
        worker = worker_for(store, media_cfg())

        scans = 0
        probe = store.pending_media_items

        async def counting(**kwargs):
            nonlocal scans
            scans += 1
            return await probe(**kwargs)

        monkeypatch.setattr(store, "pending_media_items", counting)

        # Two passes to SEE a standstill — the second is what proves the head
        # did not move — and from then on the lane stops asking entirely.
        assert await worker._media_pass() == 1
        assert await worker._media_pass() == 1
        assert scans == 2
        assert await worker._media_pass() == 0
        assert scans == 2, "a rested lane must not scan the corpus again"
        # And the item is still queued — resting is not giving up.
        item = await only_item(store)
        assert item["metadata"]["enrich_pending"] is True

    async def test_a_draining_lane_is_never_slowed_down(
        self, store, tmp_path, monkeypatch
    ):
        """The rest above must not cost throughput while work is getting done.

        A lane that describes something sees a different item next pass, and
        that difference is exactly what keeps it running at full speed.
        """
        for n in (1, 2):
            photo = tmp_path / f"{n}.png"
            photo.write_bytes(PNG)
            await store.upsert_items("src1", [photo_item(n, photo)])

        async def _describe(data, *, filename, cfg, **kwargs):
            from jarvis.ultrawiki.media_enrich import EnrichResult

            return EnrichResult(text=f"A picture ({filename}).", ok=True, provider="seeing")

        patch_describe(monkeypatch, _describe)
        worker = worker_for(store, media_cfg())

        assert await worker._media_pass() == 1
        # No cooldown was armed, so the very next pass runs and drains the second.
        assert await worker._media_pass() == 1
        assert await store.pending_media_items(limit=10) == []

    async def test_the_lane_defers_to_every_other_stage(self, store, tmp_path):
        """Frugal means: only in the gaps. A photo must never delay the keyword
        indexing of the text that arrived with it."""
        photo = tmp_path / "a.png"
        photo.write_bytes(PNG)
        await store.upsert_items("src1", [text_item(1), photo_item(1, photo)])

        calls: list[int] = []
        worker = worker_for(store, media_cfg())
        original = worker._media_pass

        async def _counting() -> int:
            calls.append(1)
            return await original()

        worker._media_pass = _counting  # type: ignore[method-assign]

        assert await worker.run_once() > 0, "the keyword stage had work"
        assert calls == [], "the media lane must not run while a stage has work"
        await worker.run_once()
        assert calls == [1], "it gets its turn once the ladder is idle"

    async def test_an_enricher_that_raises_never_stops_the_loop(
        self, store, tmp_path, monkeypatch
    ):
        photo = tmp_path / "a.png"
        photo.write_bytes(PNG)
        await store.upsert_items("src1", [photo_item(1, photo)])

        async def _boom(data, *, filename, cfg, **kwargs):
            raise RuntimeError("provider exploded")

        patch_describe(monkeypatch, _boom)
        assert await worker_for(store, media_cfg())._media_pass() == 1

        item = await only_item(store)
        assert "RuntimeError" in item["metadata"]["enrich_error"]

    async def test_a_store_without_the_backlog_query_is_simply_skipped(self, tmp_path):
        """An older store object must degrade, not raise."""

        class _OldStore:
            async def upsert_items(self, *args, **kwargs):  # pragma: no cover
                raise AssertionError("nothing should be written")

        assert await worker_for(_OldStore(), media_cfg())._media_pass() == 0

    async def test_a_failure_note_does_not_reprocess_the_item(
        self, store, tmp_path, monkeypatch
    ):
        """The trap this caught: recording a failure through the ordinary
        upsert writes NOTHING, because unchanged content is left untouched by
        design — so the note vanished and the file was retried forever."""
        photo = tmp_path / "a.png"
        photo.write_bytes(PNG)
        await store.upsert_items("src1", [photo_item(1, photo)])
        before = await only_item(store)

        async def _describe(data, *, filename, cfg, **kwargs):
            from jarvis.ultrawiki.media_enrich import EnrichResult

            return EnrichResult(reason="the provider is out of credit")

        patch_describe(monkeypatch, _describe)
        await worker_for(store, media_cfg())._media_pass()

        after = await only_item(store)
        assert after["metadata"]["enrich_error"] == "the provider is out of credit"
        # The item did NOT go back through the ladder: nothing about its
        # content changed, so re-embedding it would be pure waste.
        assert after["content_hash"] == before["content_hash"]
        assert after["state"] == before["state"]


class TestNotEveryPictureIsWorthAModelCall:
    """The cheap gate in front of the expensive stage.

    A photo library is tens of thousands of model calls, and most of the
    picture files on a real machine are not photos at all: icons, sprites and
    cache thumbnails. Measured on one real Desktop, the audio side had 218,419
    one-second wake-word clips under a program data folder — the image side has
    the same shape. Skipping them must be PERMANENT (they will never become
    worth reading) and must state its reason.
    """

    async def test_a_tiny_icon_is_skipped_without_calling_a_model(
        self, store, tmp_path, monkeypatch
    ):
        icon = tmp_path / "icon.png"
        icon.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 200)
        await store.upsert_items("src1", [photo_item(1, icon)])

        async def _never(data, *, filename, cfg, **kwargs):
            raise AssertionError("a 200-byte icon must never reach a model")

        patch_describe(monkeypatch, _never)
        assert await worker_for(store, media_cfg())._media_pass() == 1

        item = await only_item(store)
        assert item["metadata"]["enrich_pending"] is False, "must not be retried"
        assert "small" in item["metadata"]["enrich_error"].lower()

    async def test_a_picture_in_a_program_folder_is_skipped(
        self, store, tmp_path, monkeypatch
    ):
        """Judged on the path INSIDE the chosen folder, not the absolute one."""
        folder = tmp_path / "data" / "wake_debug"
        folder.mkdir(parents=True)
        frame = folder / "frame.png"
        frame.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 60_000)
        item = photo_item(1, frame)
        item = dataclasses.replace(item, external_id="data/wake_debug/frame.png")
        await store.upsert_items("src1", [item])

        async def _never(data, *, filename, cfg, **kwargs):
            raise AssertionError("a program-folder file must never reach a model")

        patch_describe(monkeypatch, _never)
        await worker_for(store, media_cfg())._media_pass()

        item = await only_item(store)
        assert item["metadata"]["enrich_pending"] is False
        assert "program folder" in item["metadata"]["enrich_error"].lower()

    async def test_a_real_photo_still_reaches_the_model(
        self, store, tmp_path, monkeypatch
    ):
        """The gate must not become the reason nothing is ever described."""
        photo = tmp_path / "holiday.png"
        photo.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 400_000)
        await store.upsert_items("src1", [photo_item(1, photo)])
        seen: list[str] = []

        async def _describe(data, *, filename, cfg, **kwargs):
            from jarvis.ultrawiki.media_enrich import EnrichResult

            seen.append(filename)
            return EnrichResult(text="A beach at sunset.", ok=True, provider="seeing")

        patch_describe(monkeypatch, _describe)
        await worker_for(store, media_cfg())._media_pass()

        assert seen == ["holiday.png"]
        item = await only_item(store)
        assert "A beach at sunset." in item["body_raw"]
