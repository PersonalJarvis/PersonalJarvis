"""Reading the text out of a document file, whatever format it is in.

A format-agnostic extraction service: PDF, the Office and OpenDocument
archives, EPUB, HTML, CSV, JSON and plain text, plus the self-declared
metadata a media file carries (capture time, camera, GPS). Format is decided
by magic bytes first and by filename second, and nothing here ever raises —
an unreadable file returns a result that says why.

Almost all of it is standard library on purpose: the office formats are ZIP
archives of XML, so ``zipfile`` plus ``xml.etree`` reads them without
``python-docx``, ``openpyxl``, ``python-pptx`` or ``ebooklib``. Every avoided
dependency is one that cannot fail to build a wheel on a Mac, bloat a
``python:3.11-slim`` image, or break a base install (repo rule §3). PDF is the
one exception and needs ``pypdf``, which is already a dependency.

Package discipline (AP-26): this ``__init__`` imports nothing. Consumers
import the modules lazily, so ``import jarvis`` stays boot-cheap even though
the document parsers live here.
"""
