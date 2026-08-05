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
from jarvis.ultrawiki.connectors.local_folder import (
    LocalFolderConnector,
    LocalFolderRootError,
    normalize_root_path,
)
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
    def test_builtin_connectors_expose_seven_fresh_factories(self):
        registry = builtin_connectors()
        assert set(registry) == {
            "obsidian-vault",
            "local-folder",
            "jarvis-conversations",
            "normal-wiki",
            "export-import",
            "custom-source",
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
        assert "Example Contact" in note.body
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

    async def test_missing_root_fails_the_run_instead_of_importing_nothing(
        self, tmp_path: Path
    ):
        """Was: yields nothing + a log line. A log line is not a user surface.

        A vault path that is gone (external drive unplugged, folder renamed)
        used to sync "successfully" to zero items, which reads exactly like an
        empty vault. It now fails, so the reason reaches the card.
        """
        missing = tmp_path / "absent"
        ctx = _ctx({"root": str(missing)})
        with pytest.raises(LocalFolderRootError) as excinfo:
            await _collect(ObsidianVaultConnector().backfill(ctx))
        assert str(missing) in str(excinfo.value)

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


class TestLocalFolderRootPath:
    """A folder path a human typed or pasted, and what it must not do silently.

    The maintainer pasted ``C:\\Users\\Administrator>`` — the trailing ``>``
    belongs to the shell PROMPT, not to the path. The source registered, the
    import reported "done", and the card showed zero items with no reason
    anywhere. Both halves are pinned here: clean what is obviously shell
    decoration, and REFUSE loudly for whatever is left.
    """

    @pytest.mark.parametrize(
        ("raw", "expected"),
        [
            ("C:/Users/Someone>", "C:/Users/Someone"),
            ("  C:/Users/Someone  ", "C:/Users/Someone"),
            ('"C:/Users/Someone"', "C:/Users/Someone"),
            ("'C:/Users/Someone'", "C:/Users/Someone"),
            ("PS C:/Users/Someone>", "C:/Users/Someone"),
            ("C:/Users/Someone> ", "C:/Users/Someone"),
            ("/home/someone$", "/home/someone"),
            ("/home/someone #", "/home/someone"),
        ],
    )
    def test_shell_prompt_decoration_is_stripped(self, raw: str, expected: str):
        assert normalize_root_path(raw) == expected

    def test_a_real_path_survives_untouched(self, tmp_path: Path):
        assert normalize_root_path(str(tmp_path)) == str(tmp_path)

    def test_a_folder_ending_in_a_prompt_character_is_kept_when_it_exists(
        self, tmp_path: Path
    ):
        """Cleaning must never eat a real folder. ``>`` is legal on POSIX."""
        odd = tmp_path / "weird>"
        try:
            odd.mkdir()
        except OSError:
            pytest.skip("this filesystem rejects '>' in a name")
        assert normalize_root_path(str(odd)) == str(odd)

    async def test_a_root_that_does_not_exist_raises_instead_of_yielding_nothing(
        self, tmp_path: Path
    ):
        missing = tmp_path / "nope"
        with pytest.raises(LocalFolderRootError) as excinfo:
            await _collect(
                LocalFolderConnector().backfill(_ctx({"root": str(missing)}))
            )
        assert str(missing) in str(excinfo.value)

    async def test_a_pasted_prompt_path_is_cleaned_and_then_imported(
        self, tmp_path: Path
    ):
        """The maintainer's exact case: the folder IS there behind the ``>``."""
        (tmp_path / "note.md").write_text("# Hello", encoding="utf-8")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": f"{tmp_path}>"}))
        )
        assert [item.external_id for item in items] == ["note.md"]

    async def test_a_file_instead_of_a_folder_raises(self, tmp_path: Path):
        target = tmp_path / "notes.md"
        target.write_text("# Hello", encoding="utf-8")
        with pytest.raises(LocalFolderRootError):
            await _collect(
                LocalFolderConnector().backfill(_ctx({"root": str(target)}))
            )

    async def test_incremental_raises_for_a_missing_root_too(self, tmp_path: Path):
        missing = tmp_path / "gone"
        with pytest.raises(LocalFolderRootError):
            await _collect(
                LocalFolderConnector().incremental(_ctx({"root": str(missing)}))
            )


class TestLocalFolderNoiseDirectories:
    """A real home folder is mostly machine noise; a knowledge base is not.

    Walking a Desktop pulled in dependency trees and caches, which are text
    but carry no knowledge — and on a developer machine they outnumber the
    user's own files by orders of magnitude.
    """

    async def test_dependency_and_cache_directories_are_skipped(
        self, tmp_path: Path
    ):
        (tmp_path / "mine.md").write_text("keep me", encoding="utf-8")
        for noisy in ("node_modules", "__pycache__", "site-packages", "AppData"):
            folder = tmp_path / noisy
            folder.mkdir()
            (folder / "junk.md").write_text("noise", encoding="utf-8")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["mine.md"]

    async def test_an_obsidian_vault_still_skips_its_own_internals(self):
        """Widening the shared skip list must not drop the vault's own rules."""
        assert {".obsidian", ".trash"} <= ObsidianVaultConnector.SKIP_DIR_NAMES

    async def test_the_skip_list_is_not_windows_only(self):
        """Charter §3: the same walk runs on macOS and Linux."""
        assert {"node_modules", "__pycache__"} <= LocalFolderConnector.SKIP_DIR_NAMES
        assert {"AppData", "Library"} <= LocalFolderConnector.SKIP_DIR_NAMES


    async def test_nested_linked_git_worktrees_are_skipped(self, tmp_path: Path):
        """A Desktop source must not clone every task worktree into memory."""
        primary = tmp_path / "primary"
        primary.mkdir()
        (primary / ".git").mkdir()
        (primary / "keep.md").write_text("canonical", encoding="utf-8")
        linked = tmp_path / "task-worktree"
        linked.mkdir()
        (linked / ".git").write_text("gitdir: ../primary/.git/worktrees/task\n")
        (linked / "duplicate.md").write_text("duplicate", encoding="utf-8")

        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )

        assert [item.external_id for item in items] == ["primary/keep.md"]

    async def test_a_worktree_selected_as_the_root_is_imported(self, tmp_path: Path):
        """The nested filter must preserve an explicit source selection."""
        (tmp_path / ".git").write_text("gitdir: ../primary/.git/worktrees/task\n")
        (tmp_path / "note.md").write_text("unique work", encoding="utf-8")

        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )

        assert [item.external_id for item in items] == ["note.md"]


class TestLocalFolderExcludes:
    """A built-in noise list cannot know what THIS folder is cluttered with.

    A measured real Desktop was 84% one folder holding ~50 working copies of
    the same project - importable text by every rule, worthless as knowledge,
    and 300k files nobody would notice were wrong until the bill arrived.
    Naming them is the user's call, so the source carries its own list.
    """

    async def test_an_excluded_folder_is_not_walked(self, tmp_path: Path):
        (tmp_path / "keep.md").write_text("keep", encoding="utf-8")
        clutter = tmp_path / "ship-release-work"
        clutter.mkdir()
        (clutter / "copy.md").write_text("drop", encoding="utf-8")

        ctx = _ctx({"root": str(tmp_path), "exclude": ["ship-release-work"]})
        items = await _collect(LocalFolderConnector().backfill(ctx))

        assert [item.external_id for item in items] == ["keep.md"]

    async def test_excludes_apply_at_every_depth(self, tmp_path: Path):
        nested = tmp_path / "projects" / "alpha" / "build-output"
        nested.mkdir(parents=True)
        (nested / "generated.md").write_text("drop", encoding="utf-8")
        (tmp_path / "projects" / "notes.md").write_text("keep", encoding="utf-8")

        ctx = _ctx({"root": str(tmp_path), "exclude": ["build-output"]})
        items = await _collect(LocalFolderConnector().backfill(ctx))

        assert [item.external_id for item in items] == ["projects/notes.md"]

    async def test_excludes_add_to_the_built_in_noise_list(self, tmp_path: Path):
        """Naming one folder must not switch the sensible defaults off."""
        (tmp_path / "keep.md").write_text("keep", encoding="utf-8")
        for noisy in ("node_modules", "mine"):
            folder = tmp_path / noisy
            folder.mkdir()
            (folder / "junk.md").write_text("drop", encoding="utf-8")

        ctx = _ctx({"root": str(tmp_path), "exclude": ["mine"]})
        items = await _collect(LocalFolderConnector().backfill(ctx))

        assert [item.external_id for item in items] == ["keep.md"]

    async def test_a_blank_or_missing_exclude_list_changes_nothing(
        self, tmp_path: Path
    ):
        (tmp_path / "keep.md").write_text("keep", encoding="utf-8")
        ctx = _ctx({"root": str(tmp_path), "exclude": ["", "   "]})
        items = await _collect(LocalFolderConnector().backfill(ctx))
        assert [item.external_id for item in items] == ["keep.md"]

    async def test_exclusion_is_case_blind_on_every_platform(self, tmp_path: Path):
        """Windows and macOS folder names are case-insensitive; Linux is not.

        A list typed on one machine has to keep working on the next, so the
        match is case-blind everywhere rather than following the host.
        """
        clutter = tmp_path / "Ship-Release-Work"
        clutter.mkdir()
        (clutter / "copy.md").write_text("drop", encoding="utf-8")
        (tmp_path / "keep.md").write_text("keep", encoding="utf-8")

        ctx = _ctx({"root": str(tmp_path), "exclude": ["ship-release-work"]})
        items = await _collect(LocalFolderConnector().backfill(ctx))

        assert [item.external_id for item in items] == ["keep.md"]


class TestLocalFolderFileTypes:
    """"Import my whole Desktop" means documents and code, not just notes.

    The connector read ``.md`` and ``.txt`` only, so a folder of PDFs, Office
    documents or source code imported as zero items — indistinguishable, from
    the card, from a folder that does not exist.
    """

    async def test_source_code_and_config_files_are_read(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
        (tmp_path / "config.toml").write_text("[a]\nb = 1", encoding="utf-8")
        (tmp_path / "page.html").write_text("<h1>Title</h1>", encoding="utf-8")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == [
            "app.py",
            "config.toml",
            "page.html",
        ]

    async def test_machine_files_are_still_ignored(self, tmp_path: Path):
        """Installers and libraries are not memories and never become items."""
        (tmp_path / "setup.exe").write_bytes(b"MZ" + b"\x00" * 64)
        (tmp_path / "lib.dll").write_bytes(b"MZ" + b"\x00" * 64)
        (tmp_path / "cache.sqlite").write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)
        (tmp_path / "note.md").write_text("keep", encoding="utf-8")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["note.md"]

    async def test_photos_and_recordings_ARE_captured(self, tmp_path: Path):
        """The opposite of the rule above, and the reason it had to be split.

        A photo library used to import as nothing at all — the largest and
        most personal pile of files most people own, invisible. It is captured
        with what the file states about itself and marked for the enrichment
        stage; nothing here claims to know what the picture shows.
        """
        (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 32)
        (tmp_path / "voice.opus").write_bytes(b"OggS\x00\x02" + b"\x00" * 32)
        (tmp_path / "note.md").write_text("keep", encoding="utf-8")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["note.md", "photo.png", "voice.opus"]

        photo = next(item for item in items if item.external_id == "photo.png")
        assert photo.metadata["media_kind"] == "image"
        assert photo.metadata["enrich_pending"] is True
        assert photo.metadata["media_ref_kind"] == "file"
        # The body carries facts the FILE states, never a guess about content.
        assert "photo.png" in photo.body

        voice = next(item for item in items if item.external_id == "voice.opus")
        assert voice.metadata["media_kind"] == "audio"

    async def test_an_explicit_extensions_config_still_wins(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
        (tmp_path / "note.md").write_text("keep", encoding="utf-8")
        ctx = _ctx({"root": str(tmp_path), "extensions": ["md"]})
        items = await _collect(LocalFolderConnector().backfill(ctx))
        assert [item.external_id for item in items] == ["note.md"]

    async def test_an_obsidian_vault_stays_markdown_only(self, tmp_path: Path):
        (tmp_path / "app.py").write_text("print('hi')", encoding="utf-8")
        (tmp_path / "note.md").write_text("# Note", encoding="utf-8")
        items = await _collect(
            ObsidianVaultConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["note.md"]


def _write_docx(path: Path, paragraphs: list[str]) -> Path:
    """A minimal but real .docx (a zip of OOXML), written at test time."""
    import zipfile

    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<w:document xmlns:w="http://schemas.openxmlformats.org/'
        f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    )
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return path


class TestLocalFolderDocuments:
    """PDFs and Word files are what a real folder is FULL of.

    They are containers, not text: reading them as UTF-8 produced a body of
    replacement characters, so they were excluded — which made "import my
    Desktop" quietly mean "import the two text files on my Desktop".
    """

    async def test_a_word_document_is_imported_with_its_text(self, tmp_path: Path):
        _write_docx(tmp_path / "ledger.docx", ["The quarterly ledger reconciliation"])
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["ledger.docx"]
        assert "quarterly ledger" in items[0].body

    async def test_a_document_title_falls_back_to_the_file_name(self, tmp_path: Path):
        _write_docx(tmp_path / "ledger.docx", ["Body text"])
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert items[0].title == "ledger"

    async def test_a_document_holding_no_readable_text_is_kept_but_marked(
        self, tmp_path: Path
    ):
        """An EMPTY body would rank and lie. A body of file facts does neither.

        This replaces "skip it": a scanned invoice with no OCR layer is exactly
        the document a person searches for by name and date, and dropping it
        made it unfindable forever — there was no record for a later run, with
        OCR available or the file repaired, to reclaim. The item therefore
        carries what the file itself states plus an explicit
        ``content_missing`` flag and the honest reason, and claims nothing
        about content it has not read.
        """
        import zipfile

        path = tmp_path / "scan.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
        (tmp_path / "real.md").write_text("# Real", encoding="utf-8")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["real.md", "scan.docx"]

        scan = next(item for item in items if item.external_id == "scan.docx")
        assert scan.metadata["content_missing"] is True
        assert scan.metadata["content_missing_reason"]
        assert scan.body.strip(), "an empty body is the thing this must never produce"
        assert "scan.docx" in scan.body

    async def test_the_plain_text_size_cap_does_not_apply_to_documents(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """A 3 MB PDF holding two pages of prose must not be dropped as "big"."""
        monkeypatch.setattr(LocalFolderConnector, "MAX_FILE_BYTES", 64)
        _write_docx(tmp_path / "ledger.docx", ["The quarterly ledger " * 40])
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert [item.external_id for item in items] == ["ledger.docx"]

    async def test_a_broken_document_never_ends_the_walk(self, tmp_path: Path):
        """The walk continuing is the point; what the broken file becomes is not.

        Content decides now, so a text file misnamed ``.docx`` is read as the
        text it is rather than discarded — the walk reaching ``b-good.md`` is
        what this test exists to protect.
        """
        (tmp_path / "a-broken.docx").write_bytes(b"not a zip")
        (tmp_path / "b-good.md").write_text("# Good", encoding="utf-8")
        items = await _collect(
            LocalFolderConnector().backfill(_ctx({"root": str(tmp_path)}))
        )
        assert "b-good.md" in [item.external_id for item in items]


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
            # A REQUIRED-key check, not an exact-set one: candidates are
            # progressively enriched (catalog identity, brand, status), and
            # pinning the exact shape turns every additive improvement into a
            # test failure that says nothing about correctness.
            assert {
                "id",
                "kind",
                "label",
                "detail",
                # The machine-readable twin of the "pull adapter pending" note.
                # Callers (the health checklist) must not have to string-match
                # an English sentence to learn whether this integration can
                # contribute anything at all.
                "has_pull_adapter",
            } <= set(entry)
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
