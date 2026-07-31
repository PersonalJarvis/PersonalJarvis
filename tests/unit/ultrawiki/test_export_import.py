"""The export-import connector: detection, per-format parsing, archives, limits.

The connector's promise is "drop whatever you have and it goes in", so the
tests are shaped around the ways that promise breaks:

* a format detected by its EXTENSION when the extension lies (a WhatsApp chat
  and a vCard dump are both ``.txt``),
* an archive that must be read without ever touching the disk, and that must
  not be usable to fill this machine's memory,
* an interrupted import that has to resume without losing items,
* and the honest half — an encrypted PDF, an unreadable file and a binary blob
  are COUNTED and reported, never silently dropped and never fatal.

Everything runs offline against tiny committed fixtures; the archive, the PDFs
and the binary file are generated into ``tmp_path`` so no opaque bytes live in
the history.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from jarvis.ultrawiki.connectors import builtin_connectors
from jarvis.ultrawiki.connectors.export_import import (
    EXPORT_FORMATS,
    ExportImportConnector,
    chat_name_from_filename,
    detect_format,
    scan_export,
    uploads_dir,
)
from jarvis.ultrawiki.types import (
    AuthKind,
    ConnectorContext,
    IncrementalMode,
    RawItem,
    UWConnector,
)

FIXTURES = Path(__file__).resolve().parents[3] / "tests" / "fixtures" / "ultrawiki" / "export"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _items(path: Path | str, checkpoint: str | None = None) -> list[RawItem]:
    connector = ExportImportConnector()
    ctx = ConnectorContext(source_id="test-export", config={"path": str(path)})
    return [item async for item in connector.backfill(ctx, checkpoint)]


def _by_format(items: list[RawItem], fmt: str) -> list[RawItem]:
    return [item for item in items if item.metadata.get("format") == fmt]


def _one(items: list[RawItem], external_id_suffix: str) -> RawItem:
    matches = [i for i in items if i.external_id.endswith(external_id_suffix)]
    assert len(matches) == 1, f"{external_id_suffix} -> {[i.external_id for i in matches]}"
    return matches[0]


def _make_pdf(path: Path, pages: list[str]) -> Path:
    """A real, tiny PDF written at test time (no binary fixture in git)."""
    pypdf = pytest.importorskip("pypdf")
    writer = pypdf.PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=200, height=200)
        writer.pages[-1]  # touch, keeps the intent explicit
        page.merge_page(_text_page(pypdf, text))
    with path.open("wb") as handle:
        writer.write(handle)
    return path


def _text_page(pypdf, text: str):
    """A one-line text page built through pypdf's own generic object model."""
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    page = writer.add_blank_page(width=200, height=200)
    content = DecodedStreamObject()
    content.set_data(
        f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1", errors="replace")
    )
    page[NameObject("/Contents")] = writer._add_object(content)  # noqa: SLF001
    font = DictionaryObject()
    font.update(
        {
            NameObject("/Type"): NameObject("/Font"),
            NameObject("/Subtype"): NameObject("/Type1"),
            NameObject("/BaseFont"): NameObject("/Helvetica"),
        }
    )
    resources = DictionaryObject()
    fonts = DictionaryObject()
    fonts[NameObject("/F1")] = writer._add_object(font)  # noqa: SLF001
    resources[NameObject("/Font")] = fonts
    page[NameObject("/Resources")] = resources
    return page


# ---------------------------------------------------------------------------
# Contract + registration
# ---------------------------------------------------------------------------


class TestConnectorContract:
    def test_it_satisfies_the_connector_protocol(self):
        connector = ExportImportConnector()
        assert isinstance(connector, UWConnector)
        assert connector.id == "export-import"
        assert connector.auth is AuthKind.EXPORT_FILE

    def test_a_dropped_export_has_no_cursor_and_no_deletes(self):
        """A snapshot cannot be polled and cannot report a removal."""
        caps = ExportImportConnector.capabilities
        assert caps.backfill is True
        assert caps.incremental is IncrementalMode.NONE
        assert caps.deletes is False

    def test_it_is_registered_as_a_builtin(self):
        registry = builtin_connectors()
        assert "export-import" in registry
        assert isinstance(registry["export-import"](), ExportImportConnector)

    async def test_incremental_yields_nothing(self):
        connector = ExportImportConnector()
        ctx = ConnectorContext(source_id="s", config={"path": str(FIXTURES)})
        assert [item async for item in connector.incremental(ctx)] == []

    async def test_a_missing_path_yields_nothing_instead_of_raising(self, tmp_path):
        assert await _items(tmp_path / "not-here") == []

    async def test_no_path_configured_yields_nothing(self):
        connector = ExportImportConnector()
        ctx = ConnectorContext(source_id="s", config={})
        assert [item async for item in connector.backfill(ctx)] == []


# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


class TestDetection:
    def test_content_magic_beats_a_lying_extension(self):
        """An export's extension is routinely wrong; the bytes are not."""
        assert detect_format("notes.txt", b"BEGIN:VCARD\r\nFN:Ada\r\n") == "vcard"
        assert detect_format("notes.txt", b"BEGIN:VCALENDAR\r\n") == "ics"
        assert detect_format("data.txt", b"%PDF-1.7\n%...") == "pdf"
        assert detect_format("blob", b"PK\x03\x04rest") == "zip"

    def test_a_takeout_mail_file_without_an_extension_is_still_an_mbox(self):
        head = b"From ada@example.com Mon Jan 01 09:00:00 2024\nSubject: hi\n\nbody"
        assert detect_format("Inbox", head) == "mbox"

    def test_both_whatsapp_line_families_are_recognised_as_chats(self):
        bracket = b"[01.02.24, 09:12:03] Ada: hi\n[01.02.24, 09:13:00] Bruno: yo\n"
        dash = b"15/03/2024, 08:30 - Bruno: hi\n15/03/2024, 08:31 - Ada: yo\n"
        assert detect_format("chat.txt", bracket) == "whatsapp"
        assert detect_format("chat.txt", dash) == "whatsapp"

    def test_a_plain_text_file_stays_plain_text(self):
        assert detect_format("notes.txt", b"just some notes\n") == "text"
        assert detect_format("readme.md", b"# Title\n") == "markdown"

    def test_a_binary_blob_is_not_a_format(self):
        assert detect_format("image.bin", bytes([0, 1, 2, 3, 0, 255])) == ""

    def test_the_bom_a_windows_exporter_leaves_does_not_hide_the_magic(self):
        assert detect_format("cal.txt", b"\xef\xbb\xbfBEGIN:VCALENDAR\r\n") == "ics"

    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("WhatsApp Chat with Ada.txt", "Ada"),
            # i18n-allow: the German filename token WhatsApp itself writes
            ("WhatsApp Chat mit Bruno Marek.txt", "Bruno Marek"),
            ("Chat de WhatsApp con Clara.txt", "Clara"),
            ("some-random-export.txt", "some-random-export"),
        ],
    )
    def test_the_chat_name_comes_out_of_the_filename(self, filename, expected):
        assert chat_name_from_filename(filename) == expected


# ---------------------------------------------------------------------------
# Per-format parsing
# ---------------------------------------------------------------------------


class TestMail:
    async def test_an_mbox_yields_one_item_per_message(self):
        items = _by_format(await _items(FIXTURES / "mail" / "inbox.mbox"), "mbox")
        assert len(items) == 2

    async def test_a_mail_carries_its_message_id_headers_and_date(self):
        items = await _items(FIXTURES / "mail" / "inbox.mbox")
        first = _one(items, "#mail-001@example.com")
        assert first.title == "Quarterly ledger"
        assert first.timestamp_utc == "2024-01-01T09:00:00Z"
        assert first.author_raw == "Ada Lovelace <ada@example.com>"
        assert "To: Bruno Marek <bruno@example.com>" in first.body
        assert "The quarterly ledger reconciliation is finished." in first.body
        assert first.metadata["message_id"] == "mail-001@example.com"

    async def test_a_reply_shares_the_thread_key_of_its_subject(self):
        items = await _items(FIXTURES / "mail" / "inbox.mbox")
        keys = {item.thread_key for item in items}
        assert keys == {"quarterly ledger"}

    async def test_a_single_eml_is_one_item_keyed_by_its_file(self):
        items = await _items(FIXTURES / "mail" / "single.eml")
        assert len(items) == 1
        assert items[0].external_id == "single.eml"
        assert items[0].thread_key == "quarterly ledger"

    async def test_a_mail_without_a_message_id_still_gets_a_stable_id(self, tmp_path):
        (tmp_path / "no-id.mbox").write_text(
            "From a@b.example Mon Jan 01 09:00:00 2024\n"
            "From: A <a@b.example>\n"
            "Subject: No identifier\n"
            "\n"
            "body text\n",
            encoding="utf-8",
        )
        first = (await _items(tmp_path))[0]
        second = (await _items(tmp_path))[0]
        assert first.external_id == second.external_id
        assert first.external_id.startswith("no-id.mbox#")


class TestCalendarAndContacts:
    async def test_an_ics_event_carries_uid_times_and_attendees(self):
        items = await _items(FIXTURES / "calendar" / "schedule.ics")
        assert len(items) == 1
        event = items[0]
        assert event.external_id == "schedule.ics#event-001@example.com"
        assert event.title == "Telescope maintenance review"
        assert event.timestamp_utc == "2024-01-10T14:00:00Z"
        assert "Location: Observatory, room 2" in event.body
        assert "bruno@example.com" in event.body
        assert "Walk through the maintenance schedule" in event.body
        assert event.metadata["uid"] == "event-001@example.com"

    async def test_vcards_become_one_item_each_with_unfolded_values(self):
        items = await _items(FIXTURES / "contacts" / "people.vcf")
        assert len(items) == 2
        ada = _one(items, "#contact-001")
        assert ada.title == "Ada Lovelace"
        assert "Email: ada@example.com" in ada.body
        bruno = next(i for i in items if i.title == "Bruno Marek")
        # The folded NOTE (continuation line) must arrive as one value.
        assert "budget for the observatory" in bruno.body

    async def test_a_vcard_photo_blob_never_reaches_the_body(self, tmp_path):
        (tmp_path / "one.vcf").write_text(
            "BEGIN:VCARD\nVERSION:3.0\nFN:Ada\n"
            "PHOTO;ENCODING=b;TYPE=JPEG:" + ("A" * 500) + "\nEND:VCARD\n",
            encoding="utf-8",
        )
        item = (await _items(tmp_path))[0]
        assert "AAAA" not in item.body
        # The format's own bookkeeping is dropped with it: VERSION says
        # nothing about the person the card describes.
        assert item.body.strip() == "Fn: Ada"


class TestWhatsApp:
    async def test_the_bracket_family_groups_messages_by_day(self):
        items = await _items(FIXTURES / "chats" / "WhatsApp Chat with Ada.txt")
        assert [i.external_id for i in items] == [
            "WhatsApp Chat with Ada.txt#2024-02-01",
            "WhatsApp Chat with Ada.txt#2024-02-02",
        ]
        assert items[0].thread_key == "Ada"
        assert items[0].timestamp_utc == "2024-02-01T09:12:00Z"
        assert items[0].metadata["messages"] == 2
        assert "09:12 Bruno: Yes, filed under maintenance." in items[0].body

    async def test_the_dash_family_parses_the_same_way(self):
        # i18n-allow: the fixture's German filename token is the input under test
        items = await _items(FIXTURES / "chats" / "WhatsApp Chat mit Bruno.txt")
        assert [i.external_id for i in items] == [
            "WhatsApp Chat mit Bruno.txt#2024-03-15",
            "WhatsApp Chat mit Bruno.txt#2024-03-16",
        ]
        assert items[0].thread_key == "Bruno"

    async def test_a_day_above_twelve_pins_the_date_order_for_the_file(self, tmp_path):
        """15/03 can only be day-first, and that decides the ambiguous lines."""
        (tmp_path / "WhatsApp Chat with X.txt").write_text(
            "15/03/2024, 08:30 - X: unambiguous, day first\n"
            "04/03/2024, 09:00 - X: ambiguous, must follow\n",
            encoding="utf-8",
        )
        days = sorted(i.metadata["day"] for i in await _items(tmp_path))
        assert days == ["2024-03-04", "2024-03-15"]

    async def test_a_month_above_twelve_pins_the_american_order(self, tmp_path):
        (tmp_path / "WhatsApp Chat with X.txt").write_text(
            "03/15/2024, 08:30 - X: unambiguous, month first\n"
            "03/04/2024, 09:00 - X: ambiguous, must follow\n",
            encoding="utf-8",
        )
        days = sorted(i.metadata["day"] for i in await _items(tmp_path))
        assert days == ["2024-03-04", "2024-03-15"]

    async def test_am_pm_and_invisible_marks_do_not_break_the_match(self, tmp_path):
        (tmp_path / "WhatsApp Chat with X.txt").write_text(
            "‎[01.02.24, 1:05:00 PM] X: afternoon\n"
            "‎[01.02.24, 1:06:00 AM] X: night\n",
            encoding="utf-8",
        )
        item = (await _items(tmp_path))[0]
        assert "13:05 X: afternoon" in item.body
        assert "01:06 X: night" in item.body

    async def test_a_wrapped_message_stays_with_its_own_line(self, tmp_path):
        (tmp_path / "WhatsApp Chat with X.txt").write_text(
            "[01.02.24, 09:00:00] X: first line\ncontinued here\n"
            "[01.02.24, 09:01:00] X: second\n",
            encoding="utf-8",
        )
        item = (await _items(tmp_path))[0]
        assert "09:00 X: first line\ncontinued here" in item.body
        assert item.metadata["messages"] == 2


class TestTabularAndDocuments:
    async def test_a_csv_repeats_its_header_in_every_chunk(self, tmp_path):
        rows = "\n".join(f"row{n},value{n}" for n in range(1, 251))
        (tmp_path / "big.csv").write_text(f"name,value\n{rows}\n", encoding="utf-8")
        items = await _items(tmp_path)
        assert [i.external_id for i in items] == [
            "big.csv#rows-00000001-00000100",
            "big.csv#rows-00000101-00000200",
            "big.csv#rows-00000201-00000250",
        ]
        for item in items:
            assert item.body.startswith("Columns: name,value")
        assert "row101,value101" in items[1].body
        assert items[2].metadata == {
            **items[2].metadata,
            "row_start": 201,
            "row_end": 250,
        }

    async def test_a_small_csv_is_one_chunk(self):
        items = await _items(FIXTURES / "tables" / "ledger.csv")
        assert len(items) == 1
        assert "Columns: name,email,city" in items[0].body
        assert "Clara Nunes,clara@example.com,Lisbon" in items[0].body

    async def test_a_tsv_is_split_on_tabs(self, tmp_path):
        (tmp_path / "t.tsv").write_text("a\tb\n1\t2\n", encoding="utf-8")
        item = (await _items(tmp_path))[0]
        assert item.body.startswith("Columns: a\tb")

    async def test_jsonl_chunks_by_line_range(self):
        items = await _items(FIXTURES / "tables" / "events.jsonl")
        assert len(items) == 1
        assert items[0].external_id == "events.jsonl#lines-00000001-00000003"
        assert '"budget_moved"' in items[0].body

    async def test_a_json_document_is_rendered_readably(self):
        item = (await _items(FIXTURES / "tables" / "settings.json"))[0]
        assert item.external_id == "settings.json"
        assert '"next_service": "2024-04-01"' in item.body

    async def test_broken_json_is_imported_as_text_rather_than_lost(self, tmp_path):
        (tmp_path / "half.json").write_text('{"a": 1, "b":', encoding="utf-8")
        item = (await _items(tmp_path))[0]
        assert item.body == '{"a": 1, "b":'

    async def test_markdown_takes_its_title_from_the_first_heading(self):
        item = (await _items(FIXTURES / "notes" / "readme.md"))[0]
        assert item.title == "Telescope maintenance"
        assert "observatory dome" in item.body

    async def test_html_is_stripped_to_text_with_its_title(self):
        item = (await _items(FIXTURES / "web" / "page.html"))[0]
        assert item.title == "Observatory log"
        assert "The dome was serviced on the tenth of January." in item.body
        assert "console.log" not in item.body
        assert "color: red" not in item.body


class TestPdf:
    async def test_a_pdf_is_extracted_page_by_page(self, tmp_path):
        _make_pdf(tmp_path / "report.pdf", ["Ledger page one", "Ledger page two"])
        items = await _items(tmp_path)
        assert len(items) == 1
        assert items[0].external_id == "report.pdf"
        assert items[0].metadata["pages"] == 2
        assert "Ledger page one" in items[0].body
        assert "Ledger page two" in items[0].body

    async def test_an_encrypted_pdf_is_skipped_with_a_reason_not_a_crash(
        self, tmp_path, caplog
    ):
        pypdf = pytest.importorskip("pypdf")
        _make_pdf(tmp_path / "open.pdf", ["Readable page"])
        reader = pypdf.PdfReader(str(tmp_path / "open.pdf"))
        writer = pypdf.PdfWriter()
        for page in reader.pages:
            writer.add_page(page)
        writer.encrypt("a-passphrase-nobody-here-has")
        with (tmp_path / "locked.pdf").open("wb") as handle:
            writer.write(handle)
        (tmp_path / "open.pdf").unlink()

        with caplog.at_level("INFO"):
            items = await _items(tmp_path)
        assert items == []
        assert "password protected" in caplog.text

    async def test_a_pdf_with_no_extractable_text_is_reported_not_stored(
        self, tmp_path, caplog
    ):
        pypdf = pytest.importorskip("pypdf")
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with (tmp_path / "scan.pdf").open("wb") as handle:
            writer.write(handle)
        with caplog.at_level("INFO"):
            assert await _items(tmp_path) == []
        assert "no extractable text" in caplog.text


# ---------------------------------------------------------------------------
# Archives
# ---------------------------------------------------------------------------


def _zip_fixture(target: Path, names: list[str]) -> Path:
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            archive.write(FIXTURES / name, arcname=name)
    return target


class TestArchives:
    async def test_zip_entries_are_parsed_without_extracting_to_disk(self, tmp_path):
        _zip_fixture(
            tmp_path / "takeout.zip",
            ["mail/inbox.mbox", "calendar/schedule.ics", "notes/readme.md"],
        )
        items = await _items(tmp_path)
        assert len(items) == 4  # 2 mails + 1 event + 1 note
        assert {i.metadata["format"] for i in items} == {"mbox", "ics", "markdown"}
        # Nothing was written next to the archive.
        assert sorted(p.name for p in tmp_path.iterdir()) == ["takeout.zip"]

    async def test_an_entry_id_and_permalink_name_the_archive_and_the_entry(
        self, tmp_path
    ):
        _zip_fixture(tmp_path / "takeout.zip", ["calendar/schedule.ics"])
        item = (await _items(tmp_path))[0]
        assert item.external_id == (
            "takeout.zip!calendar/schedule.ics#event-001@example.com"
        )
        assert item.permalink.endswith("takeout.zip#calendar/schedule.ics")
        assert item.permalink.startswith("file:///")

    async def test_a_nested_archive_is_followed(self, tmp_path):
        inner = _zip_fixture(tmp_path / "inner.zip", ["notes/readme.md"])
        outer = tmp_path / "outer.zip"
        with zipfile.ZipFile(outer, "w") as archive:
            archive.write(inner, arcname="bundles/inner.zip")
        inner.unlink()

        item = (await _items(tmp_path))[0]
        assert item.external_id == "outer.zip!bundles/inner.zip!notes/readme.md"
        assert item.title == "Telescope maintenance"

    async def test_nesting_stops_at_the_depth_limit(self, tmp_path, caplog):
        """A four-deep chain is read three deep and says so."""
        payload = tmp_path / "build"
        payload.mkdir()
        current = payload / "level4.zip"
        with zipfile.ZipFile(current, "w") as archive:
            archive.write(FIXTURES / "notes" / "readme.md", arcname="readme.md")
        for level in (3, 2, 1):
            nxt = payload / f"level{level}.zip"
            with zipfile.ZipFile(nxt, "w") as archive:
                archive.write(current, arcname=current.name)
            current = nxt
        drop = tmp_path / "drop"
        drop.mkdir()
        current.replace(drop / "level1.zip")

        with caplog.at_level("WARNING"):
            items = await _items(drop)
        assert items == []
        assert "nested deeper than 3" in caplog.text

    async def test_the_uncompressed_budget_stops_an_archive_bomb(
        self, tmp_path, monkeypatch, caplog
    ):
        """A tiny archive that expands enormously is refused, not unpacked."""
        from jarvis.ultrawiki.connectors import export_import as module

        bomb = tmp_path / "bomb.zip"
        with zipfile.ZipFile(bomb, "w", zipfile.ZIP_DEFLATED) as archive:
            archive.writestr("huge.txt", "0" * (4 * 1024 * 1024))
        assert bomb.stat().st_size < 64 * 1024  # it really is small on disk
        monkeypatch.setattr(module, "ZIP_UNCOMPRESSED_BUDGET", 1024)

        with caplog.at_level("WARNING"):
            assert await _items(tmp_path) == []
        assert "cannot be used to fill this machine" in caplog.text

    async def test_a_broken_archive_is_reported_and_the_walk_continues(
        self, tmp_path, caplog
    ):
        (tmp_path / "corrupt.zip").write_bytes(b"PK\x03\x04 not really a zip")
        (tmp_path / "ok.md").write_text("# Fine\n\nstill imported", encoding="utf-8")
        with caplog.at_level("INFO"):
            items = await _items(tmp_path)
        assert [i.external_id for i in items] == ["ok.md"]
        assert "corrupt.zip" in caplog.text

    async def test_mac_metadata_and_hidden_entries_are_skipped(self, tmp_path):
        with zipfile.ZipFile(tmp_path / "mac.zip", "w") as archive:
            archive.writestr("__MACOSX/._readme.md", "junk")
            archive.writestr(".hidden/secret.md", "# hidden")
            archive.writestr("readme.md", "# Real\n\ncontent")
        items = await _items(tmp_path)
        assert [i.external_id for i in items] == ["mac.zip!readme.md"]


# ---------------------------------------------------------------------------
# Limits, honesty, ordering
# ---------------------------------------------------------------------------


class TestLimitsAndHonesty:
    async def test_a_body_is_capped_and_says_so(self, tmp_path, monkeypatch):
        from jarvis.ultrawiki.connectors import export_import as module

        (tmp_path / "huge.md").write_text("x" * 2_000_000, encoding="utf-8")
        item = (await _items(tmp_path))[0]
        assert len(item.body) <= module.BODY_CAP + 200
        assert "truncated" in item.body
        assert item.metadata["truncated"] is True

    async def test_an_unknown_format_is_skipped_and_counted_never_raised(
        self, tmp_path, caplog
    ):
        (tmp_path / "blob.bin").write_bytes(bytes([0, 1, 2, 3] * 64))
        (tmp_path / "also.dat").write_bytes(bytes([0, 255] * 64))
        (tmp_path / "keep.md").write_text("# Keep\n\nme", encoding="utf-8")
        with caplog.at_level("INFO"):
            items = await _items(tmp_path)
        assert [i.external_id for i in items] == ["keep.md"]
        assert "2 skipped as unrecognised" in caplog.text

    async def test_hidden_files_and_folders_are_never_walked(self, tmp_path):
        (tmp_path / ".hidden.md").write_text("# no", encoding="utf-8")
        secret = tmp_path / ".git"
        secret.mkdir()
        (secret / "config.md").write_text("# no", encoding="utf-8")
        (tmp_path / "yes.md").write_text("# yes", encoding="utf-8")
        assert [i.external_id for i in await _items(tmp_path)] == ["yes.md"]

    async def test_every_item_carries_a_permalink_and_a_timestamp(self):
        for item in await _items(FIXTURES):
            assert item.permalink.startswith("file:///"), item.external_id
            assert item.timestamp_utc.endswith("Z"), item.external_id

    async def test_the_walk_order_is_deterministic(self):
        first = [i.external_id for i in await _items(FIXTURES)]
        second = [i.external_id for i in await _items(FIXTURES)]
        assert first == second
        assert len(first) == len(set(first))

    async def test_a_checkpoint_resumes_after_the_files_before_it(self):
        every = await _items(FIXTURES)
        checkpoint = _one(every, "#mail-001@example.com").external_id
        resumed = [i.external_id for i in await _items(FIXTURES, checkpoint)]
        # Everything sorting before mail/inbox.mbox is gone...
        assert not any(i.startswith("calendar/") for i in resumed)
        assert not any(i.startswith("chats/") for i in resumed)
        # ...the checkpoint's OWN file is re-read (idempotent upsert), and
        # everything after it is still delivered.
        assert checkpoint in resumed
        assert "notes/readme.md" in resumed

    async def test_a_checkpoint_from_inside_an_archive_resumes_on_the_archive(
        self, tmp_path
    ):
        _zip_fixture(tmp_path / "b-archive.zip", ["calendar/schedule.ics"])
        (tmp_path / "a-first.md").write_text("# First", encoding="utf-8")
        (tmp_path / "c-last.md").write_text("# Last", encoding="utf-8")
        every = [i.external_id for i in await _items(tmp_path)]
        assert every == [
            "a-first.md",
            "b-archive.zip!calendar/schedule.ics#event-001@example.com",
            "c-last.md",
        ]
        resumed = [i.external_id for i in await _items(tmp_path, every[1])]
        assert resumed == every[1:]

    async def test_a_single_file_path_imports_just_that_file(self):
        items = await _items(FIXTURES / "notes" / "readme.md")
        assert [i.external_id for i in items] == ["readme.md"]


# ---------------------------------------------------------------------------
# The preview pass
# ---------------------------------------------------------------------------


class TestScanExport:
    def test_the_report_names_every_format_it_found(self):
        report = scan_export(FIXTURES)
        assert report["exists"] is True
        assert report["is_dir"] is True
        assert report["formats"]["mbox"] == {
            "files": 1,
            "items_estimate": 2,
            "exact": True,
        }
        assert report["formats"]["whatsapp"]["files"] == 2
        assert report["formats"]["whatsapp"]["items_estimate"] == 4
        assert report["truncated"] is False
        assert report["total_bytes"] > 0

    async def test_the_preview_estimate_matches_what_the_import_delivers(self):
        report = scan_export(FIXTURES)
        assert report["items_estimate"] == len(await _items(FIXTURES))

    def test_formats_are_reported_in_the_declared_order(self):
        report = scan_export(FIXTURES)
        order = [fmt for fmt in report["formats"]]
        assert order == [fmt for fmt in EXPORT_FORMATS if fmt in report["formats"]]

    def test_unknown_extensions_are_counted_with_their_extension(self, tmp_path):
        for index in range(3):
            (tmp_path / f"blob{index}.bin").write_bytes(bytes([0, 1, 2, 3]))
        (tmp_path / "one.dll").write_bytes(bytes([0, 9]))
        report = scan_export(tmp_path)
        assert report["unknown"] == [
            {"extension": ".bin", "files": 3},
            {"extension": ".dll", "files": 1},
        ]
        assert report["unknown_files"] == 4

    def test_a_missing_path_answers_honestly_instead_of_raising(self, tmp_path):
        report = scan_export(tmp_path / "nowhere")
        assert report["exists"] is False
        assert report["formats"] == {}
        assert report["notes"]

    def test_the_file_budget_marks_the_answer_truncated(self, tmp_path):
        for index in range(6):
            (tmp_path / f"note{index}.md").write_text("# n", encoding="utf-8")
        report = scan_export(tmp_path, budget_files=3)
        assert report["truncated"] is True
        assert report["formats"]["markdown"]["files"] <= 4
        assert any("stopped counting" in note for note in report["notes"])

    def test_an_archive_is_reported_as_the_container_it_is(self, tmp_path):
        _zip_fixture(tmp_path / "takeout.zip", ["mail/inbox.mbox", "notes/readme.md"])
        report = scan_export(tmp_path)
        assert report["archives"]["files"] == 1
        assert report["archives"]["entries"] == 2
        assert report["formats"]["mbox"]["items_estimate"] == 2
        # The archive itself is never an item-producing format.
        assert "zip" not in report["formats"]

    async def test_the_preview_counts_mbox_separators_exactly_as_the_import_splits(
        self, tmp_path
    ):
        """An unescaped ``From `` after a blank line IS a separator (mbox's own
        rule — which is why writers escape body lines as ``>From ``). The
        preview must apply the SAME rule, or it promises a number the import
        then contradicts."""
        (tmp_path / "tricky.mbox").write_text(
            "From a@b.example Mon Jan 01 09:00:00 2024\n"
            "Subject: One\n"
            "\n"
            "From here on the writer forgot to escape the line.\n"
            "\n"
            ">From this one it did escape, so it stays in the body.\n",
            encoding="utf-8",
        )
        estimate = scan_export(tmp_path)["formats"]["mbox"]["items_estimate"]
        assert estimate == len(await _items(tmp_path)) == 2


class TestUploadsDir:
    def test_it_anchors_a_relative_data_dir_at_the_repo_root(self):
        from jarvis.core.paths import repo_root

        assert uploads_dir("data") == (
            repo_root() / "data" / "ultrawiki" / "uploads"
        ).resolve(strict=False)

    def test_an_absolute_data_dir_is_used_as_given(self, tmp_path):
        assert uploads_dir(tmp_path) == (
            tmp_path / "ultrawiki" / "uploads"
        ).resolve(strict=False)


class TestFormatParity:
    """Five-layer discipline (AP-4): the UI renders one label per format id."""

    def test_typescript_mirrors_the_python_format_list(self):
        import re

        import jarvis

        api_ts = (
            Path(jarvis.__file__).resolve().parent
            / "ui" / "web" / "frontend" / "src" / "lib" / "ultrawikiApi.ts"
        )
        source = api_ts.read_text(encoding="utf-8")
        match = re.search(
            r"const ULTRAWIKI_EXPORT_FORMATS = \[(.*?)\] as const",
            source,
            re.DOTALL,
        )
        assert match is not None
        assert re.findall(r'"([^"]+)"', match.group(1)) == list(EXPORT_FORMATS)

    def test_every_format_has_a_label_in_every_locale(self):
        import json

        import jarvis

        locales = (
            Path(jarvis.__file__).resolve().parent
            / "ui" / "web" / "frontend" / "src" / "i18n" / "locales"
        )
        for name in ("en", "de", "es"):
            data = json.loads((locales / f"{name}.json").read_text(encoding="utf-8"))
            export = data["ultrawiki"]["export"]
            for fmt in EXPORT_FORMATS:
                label = export.get(f"format_{fmt}")
                assert isinstance(label, str) and label.strip(), (
                    f"{name} has no label for the {fmt} format"
                )


# ---------------------------------------------------------------------------
# The half of Google Takeout that used to be refused
# ---------------------------------------------------------------------------


def _tar_fixture(target: Path, members: dict[str, bytes], *, compress: bool) -> Path:
    """A tar written at test time, gzipped or not."""
    import io
    import tarfile

    mode = "w:gz" if compress else "w"
    with tarfile.open(target, mode) as archive:
        for name, payload in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(payload)
            info.mtime = 1_700_000_000
            archive.addfile(info, io.BytesIO(payload))
    return target


class TestTarArchives:
    """Takeout offers .zip AND .tgz, and .tgz used to be turned away.

    A compressed tar has no central directory, so it can only be read forward
    — which is why it needs its own walk rather than reusing the ZIP one.
    """

    async def test_a_gzipped_tar_is_walked_like_any_other_archive(self, tmp_path):
        _tar_fixture(
            tmp_path / "takeout-20240101.tgz",
            {
                "Takeout/Notes/readme.md": b"# Readme\n\nThe project notes.",
                "Takeout/Contacts/all.vcf": (
                    b"BEGIN:VCARD\r\nVERSION:3.0\r\nFN:Ada Lovelace\r\nEND:VCARD\r\n"
                ),
            },
            compress=True,
        )
        items = await _items(tmp_path)
        assert {item.metadata["format"] for item in items} == {"markdown", "vcard"}
        assert any("Ada Lovelace" in item.body for item in items)
        # Nothing was unpacked next to the archive.
        assert sorted(p.name for p in tmp_path.iterdir()) == ["takeout-20240101.tgz"]

    async def test_a_plain_tar_is_recognised_by_content_not_by_name(self, tmp_path):
        """The magic sits at offset 257, so even a misnamed tar is walked."""
        _tar_fixture(
            tmp_path / "archive.bin",
            {"notes/readme.md": b"# Readme\n\nStill readable."},
            compress=False,
        )
        items = await _items(tmp_path)
        assert [item.metadata["format"] for item in items] == ["markdown"]

    async def test_an_entry_too_large_to_buffer_is_refused_with_a_reason(
        self, tmp_path, monkeypatch
    ):
        """Refusing loudly beats an unbounded read; the rest still imports."""
        import jarvis.ultrawiki.connectors.export_import as module

        monkeypatch.setattr(module, "MAX_BUFFERED_TAR_ENTRY_BYTES", 32)
        _tar_fixture(
            tmp_path / "big.tgz",
            {
                "huge.md": b"# Huge\n\n" + b"x" * 500,
                "small.md": b"# Small\n\nfine",
            },
            compress=True,
        )
        items = await _items(tmp_path)
        assert [item.metadata["format"] for item in items] == ["markdown"]
        assert "Small" in items[0].body

        report = scan_export(tmp_path / "big.tgz")
        assert any("huge.md" in row["path"] for row in report["unreadable"])

    async def test_a_truncated_archive_keeps_everything_read_so_far(self, tmp_path):
        """A half-finished download is the normal way this ends."""
        full = _tar_fixture(
            tmp_path / "full.tgz",
            {f"notes/n{index}.md": b"# Note\n\nbody" for index in range(20)},
            compress=True,
        )
        data = full.read_bytes()
        (tmp_path / "full.tgz").write_bytes(data[: len(data) // 2])
        items = await _items(tmp_path)
        # Some prefix survived and nothing raised.
        assert all(item.metadata["format"] == "markdown" for item in items)


# ---------------------------------------------------------------------------
# Documents and media through the shared extractor
# ---------------------------------------------------------------------------


def _docx_bytes(paragraphs: list[str]) -> bytes:
    import io

    body = "".join(f"<w:p><w:r><w:t>{text}</w:t></w:r></w:p>" for text in paragraphs)
    document = (
        '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/'
        f'wordprocessingml/2006/main"><w:body>{body}</w:body></w:document>'
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document)
    return buffer.getvalue()


class TestDocumentsAndMedia:
    async def test_a_word_file_in_a_drop_is_read_not_walked_as_an_archive(
        self, tmp_path
    ):
        """Every modern Office file IS a zip. Walking one would import a
        document as a heap of its own internal XML parts."""
        (tmp_path / "contract.docx").write_bytes(
            _docx_bytes(["The quarterly ledger", "reconciliation lives here."])
        )
        items = await _items(tmp_path)
        assert [item.metadata["format"] for item in items] == ["document"]
        assert "quarterly ledger" in items[0].body
        assert "word/document.xml" not in items[0].external_id

    async def test_a_photo_in_a_drop_is_captured_and_marked_for_description(
        self, tmp_path
    ):
        (tmp_path / "IMG_0001.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        items = await _items(tmp_path)
        assert [item.metadata["format"] for item in items] == ["image"]
        item = items[0]
        assert item.metadata["media_kind"] == "image"
        assert item.metadata["enrich_pending"] is True
        assert item.metadata["media_ref_kind"] == "file"
        assert "IMG_0001.png" in item.body

    async def test_a_photo_inside_a_zip_can_still_be_reopened_later(self, tmp_path):
        """Describing it happens days later, in another process — the item has
        to carry a way back to the exact bytes."""
        archive_path = tmp_path / "takeout.zip"
        with zipfile.ZipFile(archive_path, "w") as archive:
            archive.writestr("Photos/beach.png", b"\x89PNG\r\n\x1a\n" + b"\x00" * 64)
        items = await _items(tmp_path)
        assert [item.metadata["format"] for item in items] == ["image"]
        assert items[0].metadata["media_ref_kind"] == "zip-entry"
        assert items[0].metadata["media_ref_entry"] == "Photos/beach.png"
        assert Path(items[0].metadata["media_ref_path"]).name == "takeout.zip"

    async def test_a_photo_inside_a_tar_says_why_it_cannot_be_described(
        self, tmp_path
    ):
        """Silence would be the bug: a tar entry cannot be reopened, so the
        item states that instead of waiting forever in a queue."""
        _tar_fixture(
            tmp_path / "photos.tgz",
            {"Photos/beach.png": b"\x89PNG\r\n\x1a\n" + b"\x00" * 64},
            compress=True,
        )
        items = await _items(tmp_path)
        assert [item.metadata["format"] for item in items] == ["image"]
        assert items[0].metadata["enrich_pending"] is False
        assert items[0].metadata["enrich_blocked_reason"]
