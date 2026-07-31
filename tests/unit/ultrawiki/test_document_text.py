"""Unit tests for reading text out of the document formats a real folder holds.

"Import my Desktop" means the PDFs, Word files and slide decks that live
there, not only the two plain-text suffixes the folder connector started
with. Everything here is generated at test time — no opaque binaries in git —
and every failure mode must degrade to a skip, never to a crash: a folder
walk that dies on one broken file imports nothing at all.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from jarvis.ultrawiki.document_text import (
    DOCUMENT_EXTENSIONS,
    extract_document_text,
    is_document,
)

WORD_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/'
    '2006/main"><w:body>'
    "<w:p><w:r><w:t>The quarterly ledger</w:t></w:r></w:p>"
    "<w:p><w:r><w:t>reconciliation lives here.</w:t></w:r></w:p>"
    "</w:body></w:document>"
)

SLIDE_XML = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<p:sld xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" '
    'xmlns:p="http://schemas.openxmlformats.org/presentationml/2006/main">'
    "<p:cSld><p:spTree>"
    "<p:sp><p:txBody><a:p><a:r><a:t>Telescope maintenance</a:t></a:r></a:p>"
    "</p:txBody></p:sp>"
    "</p:spTree></p:cSld></p:sld>"
)


def _make_docx(path: Path, document_xml: str = WORD_XML) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("word/document.xml", document_xml)
    return path


def _make_pptx(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("[Content_Types].xml", "<Types/>")
        archive.writestr("ppt/slides/slide1.xml", SLIDE_XML)
    return path


def _make_pdf(path: Path, pages: list[str]) -> Path:
    """A real, tiny PDF written at test time (mirrors the export-import tests)."""
    pypdf = pytest.importorskip("pypdf")
    from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

    writer = pypdf.PdfWriter()
    for text in pages:
        page = writer.add_blank_page(width=200, height=200)
        source = pypdf.PdfWriter()
        text_page = source.add_blank_page(width=200, height=200)
        content = DecodedStreamObject()
        content.set_data(
            f"BT /F1 12 Tf 20 100 Td ({text}) Tj ET".encode("latin-1", "replace")
        )
        font = DictionaryObject()
        font[NameObject("/Type")] = NameObject("/Font")
        font[NameObject("/Subtype")] = NameObject("/Type1")
        font[NameObject("/BaseFont")] = NameObject("/Helvetica")
        resources = DictionaryObject()
        fonts = DictionaryObject()
        fonts[NameObject("/F1")] = source._add_object(font)  # noqa: SLF001
        resources[NameObject("/Font")] = fonts
        text_page[NameObject("/Resources")] = resources
        text_page[NameObject("/Contents")] = source._add_object(content)  # noqa: SLF001
        page.merge_page(text_page)
    with path.open("wb") as handle:
        writer.write(handle)
    return path


class TestFormatRecognition:
    def test_the_document_suffixes_are_recognised(self):
        assert is_document(Path("a.pdf"))
        assert is_document(Path("a.docx"))
        assert is_document(Path("A.PDF")), "suffix matching must be case-blind"

    def test_plain_text_is_not_a_document(self):
        """Text files go down the UTF-8 path; only opaque formats come here."""
        assert not is_document(Path("a.md"))
        assert not is_document(Path("a.py"))

    def test_the_extension_set_is_lowercase_and_dotted(self):
        assert all(ext.startswith(".") and ext.islower() for ext in DOCUMENT_EXTENSIONS)


class TestWordAndSlides:
    def test_word_paragraphs_are_extracted_in_order(self, tmp_path: Path):
        path = _make_docx(tmp_path / "ledger.docx")
        text = extract_document_text(path)
        assert text is not None
        assert "The quarterly ledger" in text
        assert text.index("quarterly") < text.index("reconciliation")

    def test_word_paragraphs_do_not_run_together(self, tmp_path: Path):
        """Two paragraphs concatenated into one word would poison retrieval."""
        text = extract_document_text(_make_docx(tmp_path / "ledger.docx"))
        assert text is not None
        assert "ledgerreconciliation" not in text.replace(" ", "")

    def test_slide_text_is_extracted(self, tmp_path: Path):
        text = extract_document_text(_make_pptx(tmp_path / "deck.pptx"))
        assert text is not None
        assert "Telescope maintenance" in text

    def test_a_docx_without_a_document_part_is_skipped(self, tmp_path: Path):
        path = tmp_path / "empty.docx"
        with zipfile.ZipFile(path, "w") as archive:
            archive.writestr("[Content_Types].xml", "<Types/>")
        assert extract_document_text(path) is None

    def test_a_docx_that_is_not_a_zip_is_read_as_what_it_actually_is(
        self, tmp_path: Path
    ):
        """Not raising is the requirement; discarding it was never the point.

        Since this delegates to the shared extractor, content decides and the
        name is only a hint — so a text file somebody saved as ``.docx`` (an
        everyday mistake) now yields its text instead of being dropped. The
        contract this test guards is "never raises", and that still holds.
        """
        path = tmp_path / "broken.docx"
        path.write_bytes(b"this is not a zip at all")
        assert extract_document_text(path) == "this is not a zip at all"

    def test_malformed_xml_inside_a_docx_is_skipped_not_raised(self, tmp_path: Path):
        path = _make_docx(tmp_path / "torn.docx", document_xml="<w:document><w:body>")
        assert extract_document_text(path) is None


class TestHostileDocuments:
    """A folder holds files from the internet, not only files the user wrote.

    A downloaded ``.docx`` is attacker-controlled XML. The stdlib parser
    expands internal entities, so a few lines of declaration expand to
    gigabytes ("billion laughs") and take the whole app down with them during
    an unattended import. OOXML never needs custom entities, so the parser
    refuses to declare ANY - which removes the entity-expansion class outright
    rather than trying to bound it.
    """

    def test_an_entity_bomb_is_refused_rather_than_expanded(self, tmp_path: Path):
        """Nine levels of ten: one billion characters if anything expands it.

        The assertion is on the SIZE of what comes back, not on it being
        ``None``. Returning nothing was how the old parser happened to survive
        a bomb; surviving is the actual requirement, and the shared extractor
        reaches it differently — the declaration is refused, so the reference
        stays an inert nine characters of raw text. A test pinned to ``None``
        would have failed a change that is equally safe and strictly better at
        salvaging damaged files.
        """
        declarations = ['<!ENTITY lol "lol">']
        for level in range(1, 10):
            previous = "lol" if level == 1 else f"lol{level - 1}"
            declarations.append(
                f'<!ENTITY lol{level} "' + f"&{previous};" * 10 + '">'
            )
        bomb = (
            '<?xml version="1.0"?>'
            "<!DOCTYPE lolz [" + "".join(declarations) + "]>"
            '<w:document xmlns:w="http://schemas.openxmlformats.org/'
            'wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>&lol9;'
            "</w:t></w:r></w:p></w:body></w:document>"
        )
        path = _make_docx(tmp_path / "bomb.docx", document_xml=bomb)
        text = extract_document_text(path) or ""
        assert len(text) < 1000, "the entity expanded — this is the bomb going off"
        assert "lollollol" not in text

    def test_a_clean_document_still_parses_after_the_hardening(
        self, tmp_path: Path
    ):
        """The guard must not cost us ordinary Word files."""
        text = extract_document_text(_make_docx(tmp_path / "fine.docx"))
        assert text is not None
        assert "quarterly ledger" in text.lower()


class TestPdf:
    def test_pdf_page_text_is_extracted(self, tmp_path: Path):
        path = _make_pdf(tmp_path / "report.pdf", ["Quarterly ledger reconciliation"])
        text = extract_document_text(path)
        assert text is not None
        assert "ledger" in text.lower()

    def test_every_page_contributes(self, tmp_path: Path):
        path = _make_pdf(tmp_path / "two.pdf", ["First page here", "Second page here"])
        text = extract_document_text(path)
        assert text is not None
        assert "First page" in text
        assert "Second page" in text

    def test_a_pdf_without_extractable_text_is_skipped(self, tmp_path: Path):
        """A scan holds pixels, not text; an empty item is worse than no item."""
        pypdf = pytest.importorskip("pypdf")
        path = tmp_path / "scan.pdf"
        writer = pypdf.PdfWriter()
        writer.add_blank_page(width=200, height=200)
        with path.open("wb") as handle:
            writer.write(handle)
        assert extract_document_text(path) is None

    def test_a_corrupt_pdf_is_skipped_not_raised(self, tmp_path: Path):
        path = tmp_path / "torn.pdf"
        path.write_bytes(b"%PDF-1.7\nbut then nothing that parses")
        assert extract_document_text(path) is None

    def test_an_oversized_document_is_skipped(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        from jarvis.ultrawiki import document_text

        monkeypatch.setattr(document_text, "MAX_DOCUMENT_BYTES", 16)
        path = _make_pdf(tmp_path / "big.pdf", ["Quarterly ledger reconciliation"])
        assert extract_document_text(path) is None

    def test_a_missing_pypdf_degrades_to_a_skip(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ):
        """Charter §3: a headless install without the extra must still walk."""
        path = _make_pdf(tmp_path / "report.pdf", ["Quarterly ledger"])
        import builtins

        real_import = builtins.__import__

        def _refuse_pypdf(name, *args, **kwargs):
            if name == "pypdf" or name.startswith("pypdf."):
                raise ImportError("pypdf is not installed")
            return real_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", _refuse_pypdf)
        assert extract_document_text(path) is None
