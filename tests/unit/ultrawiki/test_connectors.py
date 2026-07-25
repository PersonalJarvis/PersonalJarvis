"""Unit tests for the UltraWiki local-first connectors.

Covers the registry, the file-walk connectors (obsidian-vault,
local-folder, normal-wiki), the jarvis-conversations chunker against a
handmade SQLite store using the REAL ``messages`` DDL, and the
plugin-bridge gateway. Everything runs offline with no credentials.
"""

from __future__ import annotations

import logging
import os
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest

from jarvis.ultrawiki.connectors import (
    builtin_connectors,
    discover_connectors,
    discovery_failures,
    jarvis_conversations,
)
from jarvis.ultrawiki.connectors import plugin_bridge as plugin_bridge_module
from jarvis.ultrawiki.connectors.jarvis_conversations import (
    JarvisConversationsConnector,
)
from jarvis.ultrawiki.connectors.local_folder import LocalFolderConnector
from jarvis.ultrawiki.connectors.normal_wiki import NormalWikiConnector
from jarvis.ultrawiki.connectors.obsidian_vault import ObsidianVaultConnector
from jarvis.ultrawiki.connectors.plugin_bridge import (
    PluginBridgeConnector,
    list_candidates,
    register_pull_adapter,
    unregister_pull_adapter,
)
from jarvis.ultrawiki.types import ConnectorContext, RawItem

TESTS_ROOT = Path(__file__).resolve().parents[2]
FIXTURES = TESTS_ROOT / "fixtures" / "ultrawiki"
REPO_ROOT = TESTS_ROOT.parent

_ISO_UTC_RE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")
_NS = 10**9


async def _collect(agen) -> list[RawItem]:
    return [item async for item in agen]


def _ctx(config: dict) -> ConnectorContext:
    return ConnectorContext(source_id="test-source", config=config)


# ---------------------------------------------------------------------------
# Registry
# ---------------------------------------------------------------------------


class TestRegistry:
    def test_builtin_connectors_expose_five_fresh_factories(self):
        registry = builtin_connectors()
        assert set(registry) == {
            "obsidian-vault",
            "local-folder",
            "jarvis-conversations",
            "normal-wiki",
            "plugin-bridge",
        }
        for name, factory in registry.items():
            first, second = factory(), factory()
            assert first is not second, f"{name} must be instantiated fresh per use"
            assert first.id == name

    def test_discover_includes_builtins_and_records_failures(self):
        discovered = discover_connectors()
        assert set(builtin_connectors()) <= set(discovered)
        assert isinstance(discovery_failures(), dict)


# ---------------------------------------------------------------------------
# Obsidian vault
# ---------------------------------------------------------------------------


class TestObsidianVault:
    async def test_backfill_yields_visible_notes_with_stable_ids(self):
        root = FIXTURES / "obsidian_vault"
        items = await _collect(
            ObsidianVaultConnector().backfill(_ctx({"root": str(root)}))
        )
        assert [item.external_id for item in items] == ["daily/idea.md", "note.md"]
        note = next(item for item in items if item.external_id == "note.md")
        assert note.title == "Meeting Notes"
        assert "Viktoria" in note.body
        assert note.permalink == (root / "note.md").resolve().as_uri()
        for item in items:
            assert _ISO_UTC_RE.fullmatch(item.timestamp_utc)
            assert isinstance(item.metadata["mtime_ns"], int)

    async def test_hidden_and_trash_dirs_are_skipped(self):
        root = FIXTURES / "obsidian_vault"
        items = await _collect(
            ObsidianVaultConnector().backfill(_ctx({"root": str(root)}))
        )
        assert all(".obsidian" not in item.external_id for item in items)
        assert all(".trash" not in item.external_id for item in items)

    async def test_cursor_roundtrip_incremental_reyields_only_touched_file(
        self, tmp_path: Path
    ):
        (tmp_path / "a.md").write_text("# Alpha\nBody A.\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("# Beta\nBody B.\n", encoding="utf-8")
        base_ns = 1_700_000_000 * _NS
        os.utime(tmp_path / "a.md", ns=(base_ns, base_ns))
        os.utime(tmp_path / "b.md", ns=(base_ns + _NS, base_ns + _NS))
        connector = ObsidianVaultConnector()
        ctx = _ctx({"root": str(tmp_path)})

        items = await _collect(connector.backfill(ctx))
        assert [item.external_id for item in items] == ["a.md", "b.md"]
        cursor = str(max(item.metadata["mtime_ns"] for item in items))

        assert await _collect(connector.incremental(ctx, cursor)) == []

        touched_ns = base_ns + 5 * _NS
        os.utime(tmp_path / "a.md", ns=(touched_ns, touched_ns))
        changed = await _collect(connector.incremental(ctx, cursor))
        assert [item.external_id for item in changed] == ["a.md"]

    async def test_checkpoint_resumes_after_last_seen_id(self, tmp_path: Path):
        (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
        (tmp_path / "b.md").write_text("# B\n", encoding="utf-8")
        ctx = _ctx({"root": str(tmp_path)})
        resumed = await _collect(
            ObsidianVaultConnector().backfill(ctx, checkpoint="a.md")
        )
        assert [item.external_id for item in resumed] == ["b.md"]

    async def test_missing_root_yields_nothing_and_logs(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        ctx = _ctx({"root": str(tmp_path / "absent")})
        with caplog.at_level(logging.WARNING):
            assert await _collect(ObsidianVaultConnector().backfill(ctx)) == []
        assert "does not exist" in caplog.text

    async def test_unparsable_cursor_reyields_everything(
        self, tmp_path: Path, caplog: pytest.LogCaptureFixture
    ):
        (tmp_path / "a.md").write_text("# A\n", encoding="utf-8")
        ctx = _ctx({"root": str(tmp_path)})
        with caplog.at_level(logging.WARNING):
            items = await _collect(
                ObsidianVaultConnector().incremental(ctx, "not-a-number")
            )
        assert [item.external_id for item in items] == ["a.md"]
        assert "unusable incremental cursor" in caplog.text


# ---------------------------------------------------------------------------
# Local folder
# ---------------------------------------------------------------------------


class TestLocalFolder:
    async def test_default_extensions_yield_md_and_txt_only(self):
        root = FIXTURES / "local_folder"
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(root)}))
        )
        assert [item.external_id for item in items] == ["notes.txt", "readme.md"]

    async def test_custom_extensions_config(self):
        root = FIXTURES / "local_folder"
        ctx = _ctx({"root": str(root), "extensions": ["bin"]})
        items = await _collect(LocalFolderConnector().backfill(ctx))
        assert [item.external_id for item in items] == ["image.bin"]

    async def test_oversized_file_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        monkeypatch.setattr(LocalFolderConnector, "MAX_FILE_BYTES", 16)
        (tmp_path / "small.txt").write_text("tiny", encoding="utf-8")
        (tmp_path / "big.txt").write_text("x" * 64, encoding="utf-8")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["small.txt"]

    async def test_non_utf8_bytes_are_replaced_not_raised(self, tmp_path: Path):
        (tmp_path / "weird.txt").write_bytes(b"caf\xe9 latin-1")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert len(items) == 1
        assert "caf" in items[0].body

    async def test_missing_root_config_yields_nothing_and_logs(
        self, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.WARNING):
            assert await _collect(LocalFolderConnector().backfill(_ctx({}))) == []
        assert "no 'root' configured" in caplog.text


# ---------------------------------------------------------------------------
# Jarvis conversations
# ---------------------------------------------------------------------------


def _messages_ddl() -> str:
    """The REAL messages DDL, extracted from jarvis/memory/schema.sql."""
    schema = (REPO_ROOT / "jarvis" / "memory" / "schema.sql").read_text(
        encoding="utf-8"
    )
    match = re.search(
        r"CREATE TABLE IF NOT EXISTS messages \(.*?\);", schema, re.DOTALL
    )
    assert match is not None, "messages DDL not found in jarvis/memory/schema.sql"
    return match.group(0)


def _ns_at(day: str, hour: int, minute: int = 0) -> int:
    moment = datetime.strptime(day, "%Y-%m-%d").replace(
        hour=hour, minute=minute, tzinfo=UTC
    )
    return int(moment.timestamp()) * _NS


@pytest.fixture
def conversations_db(tmp_path: Path) -> Path:
    """A tiny handmade store with the real messages schema."""
    db_path = tmp_path / "jarvis.db"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute(_messages_ddl())
        conn.executemany(
            "INSERT INTO messages (trace_id, thread_id, timestamp_ns, role, text) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                ("tr-1", "t1", _ns_at("2026-01-05", 9), "user", "hello"),
                ("tr-1", "t1", _ns_at("2026-01-05", 9, 1), "assistant", "hi there"),
                ("tr-1", "t1", _ns_at("2026-01-06", 8), "user", "next day"),
                ("tr-2", None, _ns_at("2026-01-05", 12), "user", "standalone"),
            ],
        )
        conn.commit()
    finally:
        conn.close()
    return db_path


class TestJarvisConversations:
    async def test_backfill_chunks_per_conversation_and_day(
        self, conversations_db: Path
    ):
        connector = JarvisConversationsConnector()
        items = await _collect(
            connector.backfill(_ctx({"db_path": str(conversations_db)}))
        )
        assert [item.external_id for item in items] == [
            "t1:2026-01-05",
            "t1:2026-01-06",
            "tr-2:2026-01-05",
        ]
        first = items[0]
        assert first.body == "user: hello\nassistant: hi there"
        assert first.thread_key == "t1"
        assert first.permalink == "jarvis://chats/t1"
        assert first.timestamp_utc == "2026-01-05T09:01:00Z"
        assert first.metadata["message_count"] == 2
        # The trace_id fallback names the conversation when thread_id is NULL.
        assert items[2].permalink == "jarvis://chats/tr-2"

    async def test_incremental_rechunks_only_touched_days(
        self, conversations_db: Path
    ):
        connector = JarvisConversationsConnector()
        ctx = _ctx({"db_path": str(conversations_db)})
        items = await _collect(connector.backfill(ctx))
        cursor = str(max(item.metadata["max_rowid"] for item in items))

        assert await _collect(connector.incremental(ctx, cursor)) == []

        conn = sqlite3.connect(conversations_db)
        try:
            conn.execute(
                "INSERT INTO messages (trace_id, thread_id, timestamp_ns, role, text) "
                "VALUES (?, ?, ?, ?, ?)",
                ("tr-1", "t1", _ns_at("2026-01-05", 10), "user", "later addition"),
            )
            conn.commit()
        finally:
            conn.close()

        changed = await _collect(connector.incremental(ctx, cursor))
        assert [item.external_id for item in changed] == ["t1:2026-01-05"]
        # The whole day is re-chunked, in chronological order.
        assert changed[0].body == (
            "user: hello\nassistant: hi there\nuser: later addition"
        )
        assert changed[0].metadata["max_rowid"] > int(cursor)

    async def test_backfill_checkpoint_resumes_after_chunk(
        self, conversations_db: Path
    ):
        connector = JarvisConversationsConnector()
        ctx = _ctx({"db_path": str(conversations_db)})
        resumed = await _collect(connector.backfill(ctx, checkpoint="t1:2026-01-05"))
        assert [item.external_id for item in resumed] == [
            "t1:2026-01-06",
            "tr-2:2026-01-05",
        ]

    async def test_missing_db_yields_nothing(self, tmp_path: Path):
        connector = JarvisConversationsConnector()
        ctx = _ctx({"db_path": str(tmp_path / "absent.db")})
        assert await _collect(connector.backfill(ctx)) == []
        assert await _collect(connector.incremental(ctx, None)) == []
        assert not (tmp_path / "absent.db").exists(), "read-only probe must not create the db"

    async def test_cursor_never_advances_past_the_scanned_high_water_mark(
        self, conversations_db: Path
    ):
        """A row written DURING the incremental run must not be skipped.

        The touched-day scan runs first; re-reading a day afterwards can pick
        up rows inserted since — including rows of OTHER conversations that the
        scan never saw. Reporting one of those rowids would advance the cursor
        past them and lose them forever.
        """
        connector = JarvisConversationsConnector()
        ctx = _ctx({"db_path": str(conversations_db)})
        items = await _collect(connector.backfill(ctx))
        cursor = max(item.metadata["max_rowid"] for item in items)

        conn = sqlite3.connect(conversations_db)
        try:
            # Row A touches t1's day and IS covered by the scan below.
            conn.execute(
                "INSERT INTO messages (trace_id, thread_id, timestamp_ns, role, text)"
                " VALUES (?, ?, ?, ?, ?)",
                ("tr-1", "t1", _ns_at("2026-01-05", 10), "user", "seen by the scan"),
            )
            conn.commit()
            scanned_rowid = conn.execute(
                "SELECT MAX(id) FROM messages"
            ).fetchone()[0]
        finally:
            conn.close()

        # The interleave: a LATER row of a DIFFERENT conversation lands on the
        # same day, after the touched-day scan but before the day is re-read.
        original_rows_for_days = jarvis_conversations._rows_for_days

        def racing_rows_for_days(conn_, pairs):
            racing = sqlite3.connect(conversations_db)
            try:
                racing.execute(
                    "INSERT INTO messages (trace_id, thread_id, timestamp_ns, role,"
                    " text) VALUES (?, ?, ?, ?, ?)",
                    ("tr-9", "t9", _ns_at("2026-01-05", 11), "user", "never scanned"),
                )
                racing.commit()
            finally:
                racing.close()
            jarvis_conversations._rows_for_days = original_rows_for_days
            return original_rows_for_days(conn_, pairs)

        jarvis_conversations._rows_for_days = racing_rows_for_days
        try:
            changed = await _collect(connector.incremental(ctx, str(cursor)))
        finally:
            jarvis_conversations._rows_for_days = original_rows_for_days

        assert [item.external_id for item in changed] == ["t1:2026-01-05"]
        reported = changed[0].metadata["max_rowid"]
        assert reported == scanned_rowid, "cursor must stop at the scanned mark"

        # Because the cursor stopped there, the unscanned conversation is
        # picked up by the NEXT run instead of being lost.
        following = await _collect(connector.incremental(ctx, str(reported)))
        assert "t9:2026-01-05" in {item.external_id for item in following}


# ---------------------------------------------------------------------------
# Normal wiki
# ---------------------------------------------------------------------------


class TestNormalWiki:
    async def test_backfill_yields_kind_slug_ids_and_titles(self):
        root = FIXTURES / "wiki_vault"
        items = await _collect(
            NormalWikiConnector().backfill(_ctx({"vault_root": str(root)}))
        )
        by_id = {item.external_id: item for item in items}
        assert set(by_id) == {"concepts/hybrid-retrieval", "entities/ada-lovelace"}
        assert by_id["entities/ada-lovelace"].title == "Ada Lovelace"
        assert by_id["concepts/hybrid-retrieval"].title == "Hybrid Retrieval"
        assert (
            by_id["entities/ada-lovelace"].permalink
            == (root / "entities" / "ada-lovelace.md").resolve().as_uri()
        )

    async def test_ignores_pages_outside_the_four_kind_dirs(self, tmp_path: Path):
        (tmp_path / "entities").mkdir()
        (tmp_path / "attachments").mkdir()
        (tmp_path / "entities" / "x.md").write_text("# X\n", encoding="utf-8")
        (tmp_path / "attachments" / "y.md").write_text("# Y\n", encoding="utf-8")
        (tmp_path / "log.md").write_text("# Log\n", encoding="utf-8")
        items = await _collect(
            NormalWikiConnector().backfill(_ctx({"vault_root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["entities/x"]

    async def test_incremental_uses_mtime_cursor(self, tmp_path: Path):
        (tmp_path / "concepts").mkdir()
        page = tmp_path / "concepts" / "alpha.md"
        page.write_text("# Alpha\n", encoding="utf-8")
        base_ns = 1_700_000_000 * _NS
        os.utime(page, ns=(base_ns, base_ns))
        connector = NormalWikiConnector()
        ctx = _ctx({"vault_root": str(tmp_path)})
        items = await _collect(connector.backfill(ctx))
        cursor = str(items[0].metadata["mtime_ns"])
        assert await _collect(connector.incremental(ctx, cursor)) == []
        os.utime(page, ns=(base_ns + _NS, base_ns + _NS))
        changed = await _collect(connector.incremental(ctx, cursor))
        assert [item.external_id for item in changed] == ["concepts/alpha"]

    async def test_walk_order_matches_the_checkpoint_order(self, tmp_path: Path):
        """Resume must not skip a page whose id sorts differently than its path.

        The checkpoint stores an ``external_id`` (no ``.md``). Ordering the walk
        by the file PATH puts 'entities/foo-bar.md' before 'entities/foo.md'
        ('-' < '.'), while the ids order 'entities/foo' first — so resuming
        after 'entities/foo-bar' skipped 'entities/foo' entirely.
        """
        (tmp_path / "entities").mkdir()
        for name in ("foo.md", "foo-bar.md", "foo-zeta.md"):
            (tmp_path / "entities" / name).write_text(f"# {name}\n", encoding="utf-8")
        connector = NormalWikiConnector()
        ctx = _ctx({"vault_root": str(tmp_path)})

        walked = [item.external_id for item in await _collect(connector.backfill(ctx))]
        assert walked == sorted(walked), "the walk must follow external_id order"
        assert walked == ["entities/foo", "entities/foo-bar", "entities/foo-zeta"]

        resumed = await _collect(connector.backfill(ctx, checkpoint="entities/foo"))
        assert [item.external_id for item in resumed] == [
            "entities/foo-bar",
            "entities/foo-zeta",
        ]


# ---------------------------------------------------------------------------
# Plugin bridge
# ---------------------------------------------------------------------------


class TestPluginBridge:
    def test_list_candidates_returns_a_list_on_this_host(self):
        result = list_candidates()
        assert isinstance(result, list)
        for entry in result:
            assert set(entry) == {
                "id",
                "kind",
                "label",
                "detail",
                # The machine-readable twin of the "pull adapter pending" note.
                # Callers (the health checklist) must not have to string-match
                # an English sentence to learn whether this integration can
                # contribute anything at all.
                "has_pull_adapter",
            }
            assert entry["kind"] in {"plugin", "mcp"}
            assert isinstance(entry["has_pull_adapter"], bool)
            assert all(
                isinstance(entry[key], str)
                for key in ("id", "kind", "label", "detail")
            )

    def test_list_candidates_survives_broken_registries(
        self, monkeypatch: pytest.MonkeyPatch
    ):
        import jarvis.marketplace.catalog_data as catalog_data
        import jarvis.mcp.state as mcp_state

        def _boom(*_args, **_kwargs):
            raise RuntimeError("registry down")

        monkeypatch.setattr(catalog_data, "load_catalog", _boom)
        monkeypatch.setattr(mcp_state, "load_config", _boom)
        assert plugin_bridge_module.list_candidates() == []

    async def test_backfill_without_adapter_logs_pending_and_yields_nothing(
        self, caplog: pytest.LogCaptureFixture
    ):
        connector = PluginBridgeConnector()
        ctx = _ctx({"integration_id": "plugin:does-not-exist"})
        with caplog.at_level(logging.INFO):
            assert await _collect(connector.backfill(ctx)) == []
        assert "pull adapter pending" in caplog.text
        assert "plugin:does-not-exist" in caplog.text

    async def test_backfill_without_integration_id_warns(
        self, caplog: pytest.LogCaptureFixture
    ):
        with caplog.at_level(logging.WARNING):
            assert await _collect(PluginBridgeConnector().backfill(_ctx({}))) == []
        assert "no 'integration_id' configured" in caplog.text

    async def test_registered_pull_adapter_streams_items(self):
        async def fake_adapter(ctx, checkpoint):
            assert checkpoint is None
            yield RawItem(
                external_id="record-1",
                body="hello from the integration",
                permalink="https://example.invalid/records/1",
                timestamp_utc="2026-01-01T00:00:00Z",
            )

        register_pull_adapter("plugin:fake", fake_adapter)
        try:
            assert plugin_bridge_module.has_pull_adapter("plugin:fake")
            items = await _collect(
                PluginBridgeConnector().backfill(_ctx({"integration_id": "plugin:fake"}))
            )
        finally:
            unregister_pull_adapter("plugin:fake")
        assert [item.external_id for item in items] == ["record-1"]
        assert not plugin_bridge_module.has_pull_adapter("plugin:fake")

    async def test_incremental_is_an_empty_stream(self):
        items = await _collect(PluginBridgeConnector().incremental(_ctx({}), None))
        assert items == []
