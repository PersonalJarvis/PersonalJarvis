#!/usr/bin/env python3
"""Validate the reader-facing ``docs/product`` documentation corpus.

The public documentation is a product surface, not a loose collection of
Markdown files.  This check keeps its navigation metadata, cross-links,
readability, and privacy contract consistent.  Findings intentionally contain
only a path and a stable category: content matched by the privacy checks is
never echoed to a terminal or CI log.

Usage:
    python scripts/ci/check_public_docs.py [PATH ...]

With no PATH, every Markdown file below ``docs/product`` is checked.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORPUS = REPO_ROOT / "docs" / "product"
DEFAULT_MANIFEST = REPO_ROOT / "docs" / "authoring" / "public-docs-manifest.yaml"

MANIFEST_PAGE_FIELDS = (
    "file",
    "title",
    "slug",
    "summary",
    "diataxis",
    "section",
    "section_order",
    "order",
    "related",
)

REQUIRED_FIELDS = (
    "title",
    "slug",
    "summary",
    "diataxis",
    "status",
    "owner",
    "last_reviewed",
    "audience",
    "section",
    "section_order",
    "order",
    "tags",
    "related",
)
ALLOWED_DIATAXIS = {
    "tutorial",
    "howto",
    "reference",
    "explanation",
    "troubleshooting",
    "adr",
}
ALLOWED_AUDIENCES = {"end-user", "operator"}
REQUIRED_SECTIONS = {
    "how it fits together",
    "check that it works",
    "troubleshooting",
    "next steps",
}
MIN_SUMMARY_CHARS = 80
MAX_SUMMARY_CHARS = 160
MIN_BODY_WORDS = 120
MAX_BODY_WORDS = 2_500
MAX_CODE_BLOCK_LINES = 25

SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*(?:/[a-z0-9]+(?:-[a-z0-9]+)*)*$")
HEADING_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*$")
FENCE_RE = re.compile(r"^\s*(`{3,}|~{3,})(?:[^`]*)$")
MARKDOWN_LINK_RE = re.compile(r"(?<!!)\[[^\]]+\]\(([^)\s]+)")
WORD_RE = re.compile(r"\b[\w'-]+\b", re.UNICODE)
UNFINISHED_RE = re.compile(
    r"(?ix)"
    r"\b(?:TODO|FIXME|TBD|TBC|WIP)\b|"
    r"\?\?\?|"
    r"\blorem\s+ipsum\b|"
    r"\bcoming\s+soon\b|"
    r"\b(?:fill|write)\s+(?:this|me)\s+in\b|"
    r"\bplaceholder\b|"
    r"<\s*insert\b[^>]*>"
)

# Privacy patterns are intentionally broad for a public, end-user corpus.
# Obvious documentation placeholders are filtered separately below.
EMAIL_RE = re.compile(
    r"(?i)\b[A-Z0-9.!#$%&'*+/=?^_`{|}~-]+@"
    r"[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?"
    r"(?:\.[A-Z0-9](?:[A-Z0-9-]{0,61}[A-Z0-9])?)+\b"
)
WINDOWS_USER_PATH_RE = re.compile(r"(?i)\b[A-Z]:\\Users\\([^\\\s]+)(?:\\|\b)")
WINDOWS_SAFE_USERS = {
    "<user>",
    "<username>",
    "user",
    "username",
    "yourname",
    "example",
    "public",
    "default",
    "%username%",
    "${username}",
}
SID_RE = re.compile(r"\bS-1-5-21-(?:\d{8,10}-){2}\d{8,10}-\d{3,10}\b")
PRIVATE_KEY_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
    r"[\r\n]+[A-Za-z0-9+/=\r\n]{40,}?"
    r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
)
KNOWN_SECRET_RE = re.compile(
    r"(?x)\b(?:"
    r"sk-(?:ant-)?[A-Za-z0-9_-]{20,}|"
    r"ghp_[A-Za-z0-9]{30,}|"
    r"github_pat_[A-Za-z0-9_]{30,}|"
    r"glpat-[A-Za-z0-9_-]{20,}|"
    r"xox[baprs]-[A-Za-z0-9-]{20,}|"
    r"AKIA[A-Z0-9]{16}|"
    r"AIza[A-Za-z0-9_-]{30,}|"
    r"sk_live_[A-Za-z0-9]{20,}"
    r")\b"
)
ASSIGNMENT_RE = re.compile(
    r"(?ix)(?<![A-Za-z0-9])(?:"
    r"api[_-]?(?:key|token)|access[_-]?token|auth[_-]?token|client[_-]?secret|"
    r"secret[_-]?key|password|passphrase|credential|token"
    r")\b\s*(?:=|:)\s*[\"']?([^\s\"'`,;]+)"
)
SAFE_EMAIL_DOMAINS = {"example.com", "example.org", "example.net"}
SAFE_VALUE_RE = re.compile(
    r"(?ix)^(?:"
    r"<[^>]+>|\$\{[^}]+\}|\{\{[^}]+\}\}|"
    r"(?:your|example|sample|demo|test|fake|dummy)[-_a-z0-9]*|"
    r"replace[-_ ]?me|change[-_ ]?me|x{3,}|\*{3,}"
    r")$"
)


@dataclass(frozen=True)
class Page:
    """A parsed documentation page used for corpus-level validation."""

    path: Path
    display_path: str
    metadata: dict[str, Any]
    body: str


Finding = tuple[str, str]


def _display_path(path: Path) -> str:
    try:
        return path.resolve().relative_to(REPO_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _discover(paths: Iterable[Path]) -> tuple[list[Path], list[Finding]]:
    files: set[Path] = set()
    findings: list[Finding] = []
    for path in paths:
        path = path.resolve()
        if not path.exists():
            findings.append((_display_path(path), "corpus.path-missing"))
        elif path.is_file():
            if path.suffix.lower() == ".md":
                files.add(path)
            else:
                findings.append((_display_path(path), "corpus.not-markdown"))
        else:
            files.update(candidate.resolve() for candidate in path.rglob("*.md"))
    if not files and not findings:
        root = next(iter(paths), DEFAULT_CORPUS)
        findings.append((_display_path(root), "corpus.empty"))
    return sorted(files), findings


def _parse_frontmatter(path: Path) -> tuple[Page | None, list[Finding]]:
    display = _display_path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except (UnicodeDecodeError, OSError):
        return None, [(display, "file.unreadable-utf8")]

    lines = text.removeprefix("\ufeff").splitlines()
    if not lines or lines[0].strip() != "---":
        return None, [(display, "frontmatter.missing")]
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        return None, [(display, "frontmatter.unclosed")]

    raw = "\n".join(lines[1:end])
    try:
        metadata = yaml.safe_load(raw)
    except yaml.YAMLError:
        return None, [(display, "frontmatter.invalid-yaml")]
    if not isinstance(metadata, dict):
        return None, [(display, "frontmatter.not-a-map")]
    body = "\n".join(lines[end + 1 :]).strip() + "\n"
    return Page(path, display, metadata, body), []


def _is_nonempty_string(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip())


def _valid_review_date(value: Any) -> bool:
    if isinstance(value, dt.datetime):
        return False
    if isinstance(value, dt.date):
        return True
    if not isinstance(value, str):
        return False
    try:
        dt.date.fromisoformat(value)
    except ValueError:
        return False
    return bool(re.fullmatch(r"\d{4}-\d{2}-\d{2}", value))


def _metadata_findings(page: Page) -> list[Finding]:
    meta = page.metadata
    found: list[Finding] = []
    for field in REQUIRED_FIELDS:
        if field not in meta:
            found.append((page.display_path, f"frontmatter.missing.{field}"))

    for field in ("title", "summary", "slug", "section"):
        if field in meta and not _is_nonempty_string(meta[field]):
            found.append((page.display_path, f"frontmatter.invalid.{field}"))

    summary = meta.get("summary")
    if _is_nonempty_string(summary) and not (
        MIN_SUMMARY_CHARS <= len(summary.strip()) <= MAX_SUMMARY_CHARS
    ):
        found.append((page.display_path, "frontmatter.invalid.summary-length"))

    slug = meta.get("slug")
    if _is_nonempty_string(slug) and not SLUG_RE.fullmatch(slug):
        found.append((page.display_path, "frontmatter.invalid.slug"))
    if meta.get("diataxis") not in ALLOWED_DIATAXIS:
        found.append((page.display_path, "frontmatter.invalid.diataxis"))
    if meta.get("status") != "active":
        found.append((page.display_path, "frontmatter.invalid.status"))
    if meta.get("owner") != "maintainers":
        found.append((page.display_path, "frontmatter.invalid.owner"))
    if meta.get("audience") not in ALLOWED_AUDIENCES:
        found.append((page.display_path, "frontmatter.invalid.audience"))
    if "last_reviewed" in meta and not _valid_review_date(meta["last_reviewed"]):
        found.append((page.display_path, "frontmatter.invalid.last_reviewed"))

    for field in ("section_order", "order"):
        value = meta.get(field)
        if not isinstance(value, int) or isinstance(value, bool) or value < 1:
            found.append((page.display_path, f"frontmatter.invalid.{field}"))

    for field in ("tags", "related"):
        value = meta.get(field)
        valid = isinstance(value, list) and all(_is_nonempty_string(item) for item in value)
        if not valid or (field == "tags" and not value):
            found.append((page.display_path, f"frontmatter.invalid.{field}"))
    related = meta.get("related")
    if isinstance(related, list) and not 2 <= len(related) <= 4:
        found.append((page.display_path, "frontmatter.invalid.related-count"))
    return found


def _fenced_code_findings(page: Page) -> list[Finding]:
    found: list[Finding] = []
    opener: str | None = None
    content_lines = 0
    for line in page.body.splitlines():
        match = FENCE_RE.match(line)
        if opener is None:
            if match:
                opener = match.group(1)
                content_lines = 0
            continue
        if re.match(rf"^\s*{re.escape(opener[0])}{{{len(opener)},}}\s*$", line):
            if content_lines > MAX_CODE_BLOCK_LINES:
                found.append((page.display_path, "content.code-block-too-long"))
            opener = None
        else:
            content_lines += 1
    if opener is not None:
        found.append((page.display_path, "content.code-fence-unclosed"))
    return found


def _content_findings(page: Page) -> list[Finding]:
    found: list[Finding] = []
    word_count = len(WORD_RE.findall(page.body))
    if word_count < MIN_BODY_WORDS:
        found.append((page.display_path, "content.too-short"))
    elif word_count > MAX_BODY_WORDS:
        found.append((page.display_path, "content.too-long"))

    headings = [
        (len(match.group(1)), match.group(2).strip().rstrip("#").strip())
        for line in page.body.splitlines()
        if (match := HEADING_RE.match(line))
    ]
    h1s = [title for level, title in headings if level == 1]
    # The application renders frontmatter.title as the page's sole H1.  A
    # Markdown H1 would duplicate that accessible heading, even if it appears
    # only once in the source body.
    if h1s:
        found.append((page.display_path, "content.h1-duplicate"))
    if any(level > 3 for level, _title in headings):
        found.append((page.display_path, "content.heading-level-too-deep"))

    level_two = {title.casefold() for level, title in headings if level == 2}
    for required in sorted(REQUIRED_SECTIONS):
        if required not in level_two:
            category = required.replace(" ", "-")
            found.append((page.display_path, f"content.section-missing.{category}"))

    if UNFINISHED_RE.search(page.body):
        found.append((page.display_path, "content.unfinished-placeholder"))
    found.extend(_fenced_code_findings(page))
    return found


def _safe_email(match: re.Match[str]) -> bool:
    domain = match.group(0).rsplit("@", 1)[1].casefold()
    return domain in SAFE_EMAIL_DOMAINS


def _looks_like_literal_secret(value: str) -> bool:
    stripped = value.strip().rstrip(".)]")
    if SAFE_VALUE_RE.fullmatch(stripped):
        return False
    if stripped.startswith(("os.getenv(", "get_secret(", "env[", "settings.")):
        return False
    if len(stripped) < 12:
        return False
    compact = re.sub(r"[-_]", "", stripped)
    character_classes = sum(
        bool(re.search(pattern, compact))
        for pattern in (r"[a-z]", r"[A-Z]", r"\d", r"[^A-Za-z0-9]")
    )
    return character_classes >= 2 or len(compact) >= 20


def _privacy_findings(page: Page) -> list[Finding]:
    text = page.path.read_text(encoding="utf-8")
    categories: set[str] = set()
    if any(not _safe_email(match) for match in EMAIL_RE.finditer(text)):
        categories.add("privacy.email")
    for match in WINDOWS_USER_PATH_RE.finditer(text):
        if match.group(1).casefold() not in WINDOWS_SAFE_USERS:
            categories.add("privacy.windows-user-path")
            break
    if SID_RE.search(text):
        categories.add("privacy.windows-sid")
    if PRIVATE_KEY_RE.search(text):
        categories.add("privacy.private-key")
    if KNOWN_SECRET_RE.search(text):
        categories.add("privacy.secret-pattern")
    if any(_looks_like_literal_secret(match.group(1)) for match in ASSIGNMENT_RE.finditer(text)):
        categories.add("privacy.suspicious-assignment")
    return [(page.display_path, category) for category in sorted(categories)]


def _corpus_findings(pages: list[Page]) -> list[Finding]:
    found: list[Finding] = []
    slugs: defaultdict[str, list[Page]] = defaultdict(list)
    positions: defaultdict[tuple[str, int], list[Page]] = defaultdict(list)
    section_orders: defaultdict[int, set[str]] = defaultdict(set)
    order_by_section: defaultdict[str, set[int]] = defaultdict(set)

    for page in pages:
        meta = page.metadata
        slug = meta.get("slug")
        section = meta.get("section")
        order = meta.get("order")
        section_order = meta.get("section_order")
        if _is_nonempty_string(slug) and SLUG_RE.fullmatch(slug):
            slugs[slug].append(page)
        if _is_nonempty_string(section) and isinstance(order, int) and not isinstance(order, bool):
            positions[(section, order)].append(page)
        if (
            _is_nonempty_string(section)
            and isinstance(section_order, int)
            and not isinstance(section_order, bool)
        ):
            section_orders[section_order].add(section)
            order_by_section[section].add(section_order)

    for duplicates in slugs.values():
        if len(duplicates) > 1:
            found.extend((page.display_path, "corpus.duplicate-slug") for page in duplicates)
    for duplicates in positions.values():
        if len(duplicates) > 1:
            found.extend(
                (page.display_path, "corpus.duplicate-section-order") for page in duplicates
            )
    for numeric_order, sections in section_orders.items():
        if len(sections) > 1:
            for page in pages:
                if page.metadata.get("section_order") == numeric_order:
                    found.append((page.display_path, "corpus.section-order-shared"))
    for section, numeric_orders in order_by_section.items():
        if len(numeric_orders) > 1:
            for page in pages:
                if page.metadata.get("section") == section:
                    found.append((page.display_path, "corpus.section-order-inconsistent"))

    known_slugs = set(slugs)
    for page in pages:
        related = page.metadata.get("related")
        if not isinstance(related, list):
            continue
        for slug in related:
            if not isinstance(slug, str):
                continue
            if slug == page.metadata.get("slug"):
                found.append((page.display_path, "corpus.related-self"))
            elif slug not in known_slugs:
                found.append((page.display_path, "corpus.related-missing"))

        for raw_target in MARKDOWN_LINK_RE.findall(page.body):
            target = raw_target.strip("<>")
            if target.startswith(("https://", "http://", "mailto:", "#")):
                continue
            if target.startswith(("javascript:", "data:")):
                found.append((page.display_path, "corpus.link-unsafe"))
                continue
            target_slug = target.split("#", 1)[0].split("?", 1)[0]
            if target_slug.startswith("/api/docs/asset/"):
                continue
            if target_slug not in known_slugs:
                found.append((page.display_path, "corpus.link-missing"))
    return found


def _manifest_findings(pages: list[Page], files: list[Path], manifest_path: Path) -> list[Finding]:
    """Validate that the materialized public corpus matches its reviewed manifest."""
    display = _display_path(manifest_path)
    try:
        manifest = yaml.safe_load(manifest_path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return [(display, "manifest.missing")]
    except (OSError, UnicodeDecodeError, yaml.YAMLError):
        return [(display, "manifest.unreadable")]

    if not isinstance(manifest, dict):
        return [(display, "manifest.not-a-map")]
    entries = manifest.get("pages")
    if not isinstance(entries, list) or not all(isinstance(entry, dict) for entry in entries):
        return [(display, "manifest.invalid.pages")]

    found: list[Finding] = []
    declared_count = manifest.get("page_count")
    if not isinstance(declared_count, int) or isinstance(declared_count, bool):
        found.append((display, "manifest.invalid.page-count"))
    elif declared_count != len(entries):
        found.append((display, "manifest.page-count-mismatch"))

    raw_content_root = manifest.get("content_root")
    if not _is_nonempty_string(raw_content_root):
        found.append((display, "manifest.invalid.content-root"))
        corpus_root = DEFAULT_CORPUS.resolve()
    else:
        corpus_root = (REPO_ROOT / raw_content_root).resolve()

    expected: dict[Path, dict[str, Any]] = {}
    declared_slugs: set[str] = set()
    for entry in entries:
        if any(field not in entry for field in MANIFEST_PAGE_FIELDS):
            found.append((display, "manifest.page-fields-missing"))
            continue
        raw_file = entry.get("file")
        if not _is_nonempty_string(raw_file):
            found.append((display, "manifest.invalid.file"))
            continue
        expected_path = (REPO_ROOT / raw_file).resolve()
        if not expected_path.is_relative_to(corpus_root) or expected_path.suffix.lower() != ".md":
            found.append((display, "manifest.file-outside-corpus"))
            continue
        if expected_path in expected:
            found.append((display, "manifest.duplicate-file"))
        expected[expected_path] = entry

        for field in ("title", "summary", "section"):
            if not _is_nonempty_string(entry.get(field)):
                found.append((display, f"manifest.invalid.{field}"))
        summary = entry.get("summary")
        if _is_nonempty_string(summary) and not (
            MIN_SUMMARY_CHARS <= len(summary.strip()) <= MAX_SUMMARY_CHARS
        ):
            found.append((display, "manifest.invalid.summary-length"))
        if entry.get("diataxis") not in ALLOWED_DIATAXIS:
            found.append((display, "manifest.invalid.diataxis"))
        for field in ("section_order", "order"):
            value = entry.get(field)
            if not isinstance(value, int) or isinstance(value, bool) or value < 1:
                found.append((display, f"manifest.invalid.{field}"))
        related = entry.get("related")
        if not (
            isinstance(related, list)
            and 2 <= len(related) <= 4
            and all(_is_nonempty_string(item) and SLUG_RE.fullmatch(item) for item in related)
        ):
            found.append((display, "manifest.invalid.related"))

        slug = entry.get("slug")
        if not _is_nonempty_string(slug) or not SLUG_RE.fullmatch(slug):
            found.append((display, "manifest.invalid.slug"))
        elif slug in declared_slugs:
            found.append((display, "manifest.duplicate-slug"))
        else:
            declared_slugs.add(slug)

        sources = entry.get("authoritative_sources")
        if (
            not isinstance(sources, list)
            or not sources
            or not all(_is_nonempty_string(source) for source in sources)
        ):
            found.append((display, "manifest.invalid.authoritative-sources"))
        elif any(not (REPO_ROOT / source).resolve().exists() for source in sources):
            found.append((display, "manifest.authoritative-source-missing"))

    discovered = {path.resolve() for path in files}
    for path in sorted(expected.keys() - discovered):
        found.append((_display_path(path), "manifest.file-missing"))
    for path in sorted(discovered - expected.keys()):
        found.append((_display_path(path), "manifest.file-unlisted"))

    parsed = {page.path.resolve(): page for page in pages}
    for path in sorted(expected.keys() & parsed.keys()):
        entry = expected[path]
        metadata = parsed[path].metadata
        for field in MANIFEST_PAGE_FIELDS:
            if field == "file":
                continue
            if metadata.get(field) != entry.get(field):
                found.append((_display_path(path), f"manifest.metadata-mismatch.{field}"))

    if isinstance(declared_count, int) and not isinstance(declared_count, bool):
        if declared_count != len(discovered):
            found.append((display, "manifest.corpus-count-mismatch"))
    return found


def check_paths(paths: Iterable[Path], *, manifest_path: Path | None = None) -> list[Finding]:
    """Return sorted, de-duplicated ``(path, category)`` findings."""
    materialized = tuple(paths)
    files, found = _discover(materialized)
    pages: list[Page] = []
    for path in files:
        page, parse_findings = _parse_frontmatter(path)
        found.extend(parse_findings)
        if page is None:
            continue
        pages.append(page)
        found.extend(_metadata_findings(page))
        found.extend(_content_findings(page))
        found.extend(_privacy_findings(page))
    found.extend(_corpus_findings(pages))
    if manifest_path is not None:
        found.extend(_manifest_findings(pages, files, manifest_path.resolve()))
    return sorted(set(found))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("paths", nargs="*", help="Markdown files or corpus directories")
    args = parser.parse_args(argv)
    targets = [Path(path) for path in args.paths] or [DEFAULT_CORPUS]
    manifest_path = None if args.paths else DEFAULT_MANIFEST
    try:
        findings = check_paths(targets, manifest_path=manifest_path)
    except Exception:  # pragma: no cover - last-resort fail-closed CI guard
        print(f"{_display_path(DEFAULT_CORPUS)}: runtime.error", file=sys.stderr)
        return 1
    for path, category in findings:
        print(f"{path}: {category}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
