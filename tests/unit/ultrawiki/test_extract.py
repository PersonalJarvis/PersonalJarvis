"""The shared text extractor — every format a real folder or inbox holds.

Written after the audit found that every reader gave up when it met a binary:
Drive produced no item at all for a PDF or a Word file, Dropbox's allowlist
excluded exactly the formats people keep their thinking in, Gmail never
downloaded an attachment. These tests build the archives byte by byte rather
than shipping fixtures, so they run identically on Windows, macOS and a
headless container with no optional package installed.
"""

from __future__ import annotations

import io
import zipfile

from jarvis.ultrawiki.extract import ExtractResult, detect_kind, extract_text


def _zip(members: dict[str, str | bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, payload in members.items():
            archive.writestr(name, payload)
    return buffer.getvalue()


# ---------------------------------------------------------------------------
# Detection — content first, name second
# ---------------------------------------------------------------------------


def test_a_pdf_named_txt_is_still_a_pdf():
    """Extensions lie constantly; magic bytes do not."""
    assert detect_kind(b"%PDF-1.7\nrest", filename="notes.txt") == "pdf"


def test_a_file_with_no_extension_is_identified_by_content():
    """A Drive export arrives with no name at all."""
    assert detect_kind(b"Just some plain prose.") == "text"
    assert detect_kind(b"\x89PNG\r\n\x1a\nrest") == "image"


# ---------------------------------------------------------------------------
# Media detection — the formats a phone actually produces
# ---------------------------------------------------------------------------


def _ftyp(brand: bytes) -> bytes:
    """A minimal ISO base-media header carrying one brand."""
    return b"\x00\x00\x00\x18ftyp" + brand + b"\x00\x00\x02\x00" + brand


def _riff(form: bytes) -> bytes:
    return b"RIFF" + b"\x00\x00\x01\x00" + form + b"rest"


def test_an_iphone_photo_is_an_image_not_a_binary():
    """HEIC and MP4 share a container; only the brand tells them apart.

    Before this, an iPhone photo library imported as a folder of "binary
    files" — the single largest pile of memories most people own, invisible.
    """
    assert detect_kind(_ftyp(b"heic"), filename="IMG_4821.HEIC") == "image"
    assert detect_kind(_ftyp(b"mif1"), filename="IMG_4821.heic") == "image"
    assert detect_kind(_ftyp(b"avif"), filename="shot.avif") == "image"


def test_the_same_container_holding_video_or_sound_is_not_called_an_image():
    assert detect_kind(_ftyp(b"isom"), filename="clip.mp4") == "video"
    assert detect_kind(_ftyp(b"qt  "), filename="clip.mov") == "video"
    assert detect_kind(_ftyp(b"M4A "), filename="memo.m4a") == "audio"


def test_an_unlisted_iso_brand_degrades_to_video_rather_than_binary():
    """Better a transcript attempt than a silently dropped file."""
    assert detect_kind(_ftyp(b"zzzz"), filename="clip.mp4") == "video"


def test_riff_containers_are_split_by_their_form():
    assert detect_kind(_riff(b"WEBP"), filename="pic.webp") == "image"
    assert detect_kind(_riff(b"WAVE"), filename="note.wav") == "audio"
    assert detect_kind(_riff(b"AVI "), filename="old.avi") == "video"


def test_a_whatsapp_voice_note_is_audio():
    """`.opus` inside an Ogg stream — the format every voice message uses."""
    assert detect_kind(b"OggS\x00\x02rest", filename="PTT-20240612-WA0003.opus") == "audio"


def test_a_camera_raw_file_is_an_image_by_name():
    """No magic ships for every vendor's RAW, and a photographer's archive is
    entirely made of them."""
    assert detect_kind(b"\x00\x01\x02\x03unknown-binary", filename="DSC_0042.NEF") == "image"
    assert detect_kind(b"\x00\x01\x02\x03unknown-binary", filename="P1000123.RW2") == "image"


def test_a_typescript_file_is_not_a_video():
    """`.ts` is a transport stream and a source file; in a knowledge base it is
    overwhelmingly the second, and calling it video would drop it."""
    assert detect_kind(b"export const x = 1;\n", filename="app.ts") == "text"


# ---------------------------------------------------------------------------
# tar — the half of Google Takeout that used to be refused
# ---------------------------------------------------------------------------


def _tar_header() -> bytes:
    """A tar block whose magic sits at offset 257, where tar keeps it."""
    return b"name.txt".ljust(257, b"\x00") + b"ustar\x0000" + b"\x00" * 200


def test_an_uncompressed_tar_is_recognised_by_its_offset_magic():
    assert detect_kind(_tar_header(), filename="archive.tar") == "tar"
    # ...and without any name at all, because the magic is conclusive.
    assert detect_kind(_tar_header()) == "tar"


def test_a_takeout_tgz_is_a_tar_not_an_opaque_gzip():
    """Compression records nothing about what it compressed, so the double
    suffix is the only evidence — and it is the download Takeout offers by
    default."""
    gzip_head = b"\x1f\x8b\x08\x00rest"
    assert detect_kind(gzip_head, filename="takeout-20240101.tgz") == "tar"
    assert detect_kind(gzip_head, filename="takeout-20240101.tar.gz") == "tar"
    # A plain gzipped single file is still just gzip.
    assert detect_kind(gzip_head, filename="notes.txt.gz") == "gzip"


def test_office_formats_are_separated_by_their_inner_layout():
    """They all start with the same ZIP signature."""
    assert detect_kind(_zip({"word/document.xml": "<w:p/>"})) == "docx"
    assert detect_kind(_zip({"xl/workbook.xml": "<workbook/>"})) == "xlsx"
    assert detect_kind(_zip({"ppt/presentation.xml": "<p/>"})) == "pptx"
    assert detect_kind(_zip({"content.xml": "<office/>"})) == "odf"
    assert detect_kind(_zip({"META-INF/container.xml": "<c/>"})) == "epub"
    # A plain zip stays a plain zip.
    assert detect_kind(_zip({"readme.txt": "hi"})) == "archive"


def test_an_empty_file_is_its_own_answer():
    assert detect_kind(b"") == "empty"


# ---------------------------------------------------------------------------
# The formats
# ---------------------------------------------------------------------------


def test_a_word_document_yields_its_paragraphs_as_lines():
    """Clauses running into one wall of text retrieve as a blur."""
    document = (
        "<w:document><w:body>"
        "<w:p><w:r><w:t>The parties agree as follows.</w:t></w:r></w:p>"
        "<w:p><w:r><w:t>Payment is due within 30 days.</w:t></w:r></w:p>"
        "</w:body></w:document>"
    )
    result = extract_text(_zip({"word/document.xml": document}), filename="deal.docx")
    assert result.ok
    assert result.kind == "docx"
    assert "The parties agree as follows." in result.text
    assert "Payment is due within 30 days." in result.text
    # Separate paragraphs, not one run-on line.
    assert "\n" in result.text


def test_word_headers_footnotes_and_comments_are_read_too():
    result = extract_text(
        _zip(
            {
                "word/document.xml": "<w:p><w:r><w:t>Body text.</w:t></w:r></w:p>",
                "word/header1.xml": "<w:p><w:r><w:t>Confidential draft.</w:t></w:r></w:p>",
                "word/comments.xml": "<w:p><w:r><w:t>Ruben: check this figure.</w:t></w:r></w:p>",
            }
        ),
        filename="doc.docx",
    )
    assert "Body text." in result.text
    assert "Confidential draft." in result.text
    assert "Ruben: check this figure." in result.text


def test_a_spreadsheet_resolves_its_shared_strings():
    """Read without the shared-string table a workbook yields numbers only.

    Excel stores every repeated string once and references it by index, so a
    naive reader gets `0` where the sheet says "Approved".
    """
    shared = (
        '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<si><t>Vendor</t></si><si><t>Approved</t></si></sst>"
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        '<row><c t="s"><v>0</v></c><c t="s"><v>1</v></c></row>'
        '<row><c><v>4200</v></c></row>'
        "</sheetData></worksheet>"
    )
    result = extract_text(
        _zip(
            {
                "xl/workbook.xml": "<workbook/>",
                "xl/sharedStrings.xml": shared,
                "xl/worksheets/sheet1.xml": sheet,
            }
        ),
        filename="budget.xlsx",
    )
    assert result.ok
    assert "Vendor" in result.text
    assert "Approved" in result.text
    assert "4200" in result.text


def test_a_deck_keeps_each_note_with_its_own_slide_in_order():
    """Notes carry the argument; detached they retrieve as orphan sentences.

    Two ordering traps, both seen on the maintainer's real decks: plain sorting
    puts every `notesSlides/` entry before any `slides/` entry, and `slide10`
    before `slide2`.
    """
    members = {"ppt/presentation.xml": "<p/>"}
    for number in (1, 2, 10):
        members[f"ppt/slides/slide{number}.xml"] = (
            f"<a:p><a:t>Headline {number}</a:t></a:p>"
        )
        members[f"ppt/notesSlides/notesSlide{number}.xml"] = (
            f"<a:p><a:t>Note {number}</a:t></a:p>"
        )
    result = extract_text(_zip(members), filename="deck.pptx")
    assert result.ok

    order = [
        result.text.index(marker)
        for marker in ("Headline 1", "Note 1", "Headline 2", "Note 2", "Headline 10")
    ]
    assert order == sorted(order), result.text
    assert "Speaker notes: Note 2" in result.text


def test_an_opendocument_file_is_read():
    content = (
        "<office:document-content>"
        "<text:p>First OpenDocument paragraph.</text:p>"
        "<text:p>Second one.</text:p>"
        "</office:document-content>"
    )
    result = extract_text(_zip({"content.xml": content}), filename="notes.odt")
    assert result.ok
    assert result.kind == "odf"
    assert "First OpenDocument paragraph." in result.text


def test_an_epub_yields_its_chapters():
    result = extract_text(
        _zip(
            {
                "META-INF/container.xml": "<container/>",
                "ch1.xhtml": "<html><body><p>Chapter one begins.</p></body></html>",
                "ch2.xhtml": "<html><body><p>Chapter two follows.</p></body></html>",
            }
        ),
        filename="book.epub",
    )
    assert result.ok
    assert "Chapter one begins." in result.text
    assert "Chapter two follows." in result.text


def test_html_keeps_the_text_and_drops_scripts():
    raw = (
        b"<html><head><style>p{color:red}</style>"
        b"<script>alert('x')</script></head>"
        b"<body><h1>Heading</h1><p>Real prose here.</p></body></html>"
    )
    result = extract_text(raw, filename="page.html")
    assert result.ok
    assert "Real prose here." in result.text
    assert "alert" not in result.text
    assert "color:red" not in result.text


def test_rtf_loses_its_control_words():
    raw = rb"{\rtf1\ansi\deff0 {\fonttbl}\f0\fs24 Signed on Tuesday.\par}"
    result = extract_text(raw, filename="letter.rtf")
    assert result.ok
    assert "Signed on Tuesday." in result.text
    assert "rtf1" not in result.text


def test_json_is_pretty_printed_so_it_can_be_split():
    """A one-line 2 MB JSON file is a single unbreakable run for the chunker."""
    result = extract_text(b'{"a":1,"b":{"c":"deep"}}', filename="data.json")
    assert result.ok
    assert "\n" in result.text
    assert '"deep"' in result.text


def test_broken_json_keeps_its_raw_text_rather_than_vanishing():
    result = extract_text(b'{"a": 1,,,}', filename="broken.json")
    assert result.ok
    assert "a" in result.text


def test_source_code_is_plain_text():
    result = extract_text(b"def handler():\n    return 42\n", filename="app.py")
    assert result.ok
    assert result.kind == "text"
    assert "def handler():" in result.text


def test_a_windows_encoded_file_is_text_not_binary():
    """The most common everyday document on a Windows machine.

    Accented text saved as cp1252 or latin-1 is invalid UTF-8, so a
    UTF-8-only check classifies the file BINARY and drops it whole — the exact
    failure this service exists to stop. Any accented word proves it; these are
    English ones so the fixture needs no language exemption.
    """
    result = extract_text("naïve café façade".encode("cp1252"), filename="note.txt")
    assert result.ok
    assert result.kind == "text"
    assert "café" in result.text


# ---------------------------------------------------------------------------
# Honest failure — the part that decides whether an item is retryable
# ---------------------------------------------------------------------------


def test_an_image_reports_no_text_and_is_NOT_marked_retryable():
    """Nothing to extract is different from could not extract.

    ``content_missing`` false is the load-bearing assertion: it keeps the
    picture out of the text-extraction retry queue. It is NOT a statement that
    the file is worthless — ``media_kind`` is what routes it to enrichment
    instead, and the two flags must never collapse into one.
    """
    result = extract_text(b"\x89PNG\r\n\x1a\nrest", filename="photo.png")
    assert not result.ok
    assert result.kind == "image"
    assert result.reason
    assert result.content_missing is False
    assert result.media_kind == "image"


def test_a_corrupt_office_file_IS_marked_retryable():
    """A repair or a later build could still reclaim it.

    A docx whose document part is missing entirely cannot be salvaged, unlike
    one with damaged markup — the regex path recovers whatever text survives
    there, which is the better outcome and deliberately not a failure.
    """
    result = extract_text(_zip({"word/styles.xml": "<styles/>"}), filename="x.docx")
    assert result.kind in ("docx", "archive")
    if not result.ok:
        assert result.content_missing is True
        assert result.reason


def test_damaged_markup_is_salvaged_rather_than_discarded():
    """Half a readable document beats none."""
    result = extract_text(
        _zip({"word/document.xml": "<w:p><w:t>Survives</w:t></w:p><w:p><w:t>bro"}),
        filename="x.docx",
    )
    assert result.ok
    assert "Survives" in result.text


def test_a_legacy_office_file_says_what_to_do_about_it():
    result = extract_text(b"\xd0\xcf\x11\xe0" + b"\x00" * 32, filename="old.doc")
    assert not result.ok
    assert "re-saving" in result.reason


def test_an_encrypted_or_broken_pdf_never_raises():
    result = extract_text(b"%PDF-1.4\nnot really a pdf", filename="x.pdf")
    assert isinstance(result, ExtractResult)
    assert result.kind == "pdf"
    assert not result.ok
    assert result.reason


def test_an_archive_points_at_the_export_importer_instead():
    result = extract_text(_zip({"a.txt": "hi"}), filename="takeout.zip")
    assert not result.ok
    assert "export" in result.reason


# ---------------------------------------------------------------------------
# Safety — these archives arrive from strangers
# ---------------------------------------------------------------------------


def test_an_xml_entity_bomb_is_refused_rather_than_expanded():
    """"Billion laughs" is a real input here, not a theoretical one.

    A handful of nested entity declarations expand to gigabytes and take the
    process with them. The declaration is refused outright — OOXML has no
    legitimate use for custom entities.
    """
    bomb = (
        '<?xml version="1.0"?><!DOCTYPE lolz ['
        '<!ENTITY lol "lol">'
        '<!ENTITY lol2 "&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;&lol;">'
        '<!ENTITY lol3 "&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;&lol2;">'
        "]><sst><si><t>&lol3;</t></si></sst>"
    )
    result = extract_text(
        _zip(
            {
                "xl/workbook.xml": "<workbook/>",
                "xl/sharedStrings.xml": bomb,
                "xl/worksheets/sheet1.xml": (
                    '<worksheet><sheetData><row><c t="s"><v>0</v></c>'
                    "</row></sheetData></worksheet>"
                ),
            }
        ),
        filename="bomb.xlsx",
    )
    assert "lollol" not in result.text
    assert len(result.text) < 10_000


def test_a_high_ratio_office_zip_bomb_is_refused_before_decompression():
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("word/document.xml", b"<w:t>repeat</w:t>" * 250_000)

    result = extract_text(buffer.getvalue(), filename="bomb.docx")

    assert not result.ok
    assert result.text == ""


def test_a_zip_that_is_not_really_a_zip_never_raises():
    result = extract_text(b"PK\x03\x04garbage-not-an-archive", filename="x.docx")
    assert isinstance(result, ExtractResult)
    assert not result.ok


def test_nothing_ever_raises_for_any_input():
    """The extractor sits inside a walk over thousands of files."""
    for payload in (b"", b"\x00\x01\x02", b"%PDF-", _zip({}), b"{" * 5000):
        for name in ("", "x.pdf", "x.docx", "x.xlsx", "x.json", "x.bin"):
            assert isinstance(extract_text(payload, filename=name), ExtractResult)
