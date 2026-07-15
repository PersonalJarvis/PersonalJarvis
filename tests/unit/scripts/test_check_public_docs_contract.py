"""Contract tests for the fail-closed public product-docs validator."""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import yaml

from scripts.ci.check_public_docs import check_paths

SCRIPT = Path(__file__).resolve().parents[3] / "scripts" / "ci" / "check_public_docs.py"
FRIENDLY_SUMMARY = (
    "A friendly explanation of this part of Personal Jarvis, what it does, "
    "and when you may want to use it."
)


def _body(title: str, *, extra: str = "", second_h1: bool = False) -> str:
    prose = " ".join(
        [
            "Jarvis keeps the important controls close together so a reader can understand "
            "what happens before choosing an action."
        ]
        * 14
    )
    duplicate = f"\n# {title} Again\n" if second_h1 else ""
    return f"""## Overview

{prose}
{duplicate}
## How It Fits Together

This feature shares clear status with the rest of Jarvis and hands work to the next feature.

## Check That It Works

Complete one representative action and confirm that the expected result appears in the app.

## Troubleshooting

Check the visible status, required permission, and connected service before trying again.

## Next Steps

Open the related guide when you are ready to continue.

{extra}
"""


def _write_page(
    root: Path,
    filename: str,
    *,
    title: str,
    slug: str,
    order: int,
    related: list[str],
    section: str = "Start",
    section_order: int = 1,
    diataxis: str = "explanation",
    body: str | None = None,
) -> Path:
    path = root / filename
    path.parent.mkdir(parents=True, exist_ok=True)
    links = "\n".join(f"  - {item}" for item in related) or "[]"
    related_yaml = f"\n{links}" if related else " []"
    path.write_text(
        f"""---
title: {title}
slug: {slug}
summary: {FRIENDLY_SUMMARY}
diataxis: {diataxis}
status: active
owner: maintainers
last_reviewed: 2026-07-15
audience: end-user
section: {section}
section_order: {section_order}
order: {order}
tags:
  - getting-started
related:{related_yaml}
---
{body or _body(title)}
""",
        encoding="utf-8",
    )
    return path


def _categories(root: Path) -> set[str]:
    return {category for _path, category in check_paths([root])}


def test_clean_corpus_accepts_cross_links_and_safe_secret_placeholders(tmp_path: Path) -> None:
    root = tmp_path / "product"
    _write_page(
        root,
        "welcome.md",
        title="Welcome",
        slug="welcome",
        order=1,
        related=["daily-use", "reference"],
        diataxis="howto",
        body=_body(
            "Welcome",
            extra="""```text
API_KEY=<YOUR_API_KEY>
TOKEN=${JARVIS_TOKEN}
email: reader@example.com
C:\\Users\\<username>\\Jarvis
```""",
        ),
    )
    _write_page(
        root,
        "daily-use.md",
        title="Daily Use",
        slug="daily-use",
        order=2,
        related=["welcome", "reference"],
    )
    _write_page(
        root,
        "reference.md",
        title="Reference",
        slug="reference",
        order=3,
        related=["welcome", "daily-use"],
    )

    assert check_paths([root]) == []


def test_runtime_diataxis_values_are_exact(tmp_path: Path) -> None:
    root = tmp_path / "product"
    runtime_values = (
        "tutorial",
        "howto",
        "reference",
        "explanation",
        "troubleshooting",
        "adr",
    )
    for order, value in enumerate(runtime_values, start=1):
        related = [
            runtime_values[order % len(runtime_values)],
            runtime_values[(order + 1) % len(runtime_values)],
        ]
        _write_page(
            root,
            f"{value}.md",
            title=value.title(),
            slug=value,
            order=order,
            related=related,
            diataxis=value,
        )

    assert check_paths([root]) == []

    invented = _write_page(
        root,
        "invented.md",
        title="Invented",
        slug="invented",
        order=len(runtime_values) + 1,
        related=["tutorial", "reference"],
        diataxis="how-to",
    )
    findings = [
        category for path, category in check_paths([root]) if Path(path).name == invented.name
    ]
    assert set(findings) == {"frontmatter.invalid.diataxis"}


def test_metadata_and_corpus_graph_fail_closed(tmp_path: Path) -> None:
    root = tmp_path / "product"
    first = _write_page(
        root,
        "one.md",
        title="One",
        slug="same-slug",
        order=1,
        related=["missing-page", "same-slug"],
    )
    _write_page(
        root,
        "two.md",
        title="Two",
        slug="same-slug",
        order=1,
        related=["same-slug", "missing-page"],
    )
    first.write_text(
        first.read_text(encoding="utf-8")
        .replace("audience: end-user", "audience: developer")
        .replace(FRIENDLY_SUMMARY, "Too short.")
        .replace(
            "Open the related guide when you are ready to continue.",
            "Open the [missing body guide](missing-body) when you are ready.",
        ),
        encoding="utf-8",
    )

    categories = _categories(root)
    assert "frontmatter.invalid.audience" in categories
    assert "frontmatter.invalid.summary-length" in categories
    assert "corpus.duplicate-slug" in categories
    assert "corpus.duplicate-section-order" in categories
    assert "corpus.related-missing" in categories
    assert "corpus.related-self" in categories
    assert "corpus.link-missing" in categories


def test_content_rules_block_unfinished_short_pages_and_large_code_blocks(tmp_path: Path) -> None:
    root = tmp_path / "product"
    long_block = "```text\n" + "\n".join(f"line {i}" for i in range(26)) + "\n```"
    _write_page(
        root,
        "unfinished.md",
        title="Unfinished",
        slug="unfinished",
        order=1,
        related=["missing-one", "missing-two"],
        body=(
            "# Duplicate rendered title\n\n#### Too deep\n\n"
            f"TODO: fill this in.\n\n{long_block}\n"
        ),
    )

    categories = _categories(root)
    assert "content.too-short" in categories
    assert "content.h1-duplicate" in categories
    assert "content.section-missing.how-it-fits-together" in categories
    assert "content.section-missing.check-that-it-works" in categories
    assert "content.section-missing.troubleshooting" in categories
    assert "content.section-missing.next-steps" in categories
    assert "content.heading-level-too-deep" in categories
    assert "content.unfinished-placeholder" in categories
    assert "content.code-block-too-long" in categories


def test_privacy_rules_find_fake_sensitive_shapes_without_exposing_values(tmp_path: Path) -> None:
    root = tmp_path / "product"
    key_header = "-----BEGIN " + "PRIVATE KEY-----"
    key_footer = "-----END " + "PRIVATE KEY-----"
    fake_key = f"{key_header}\n{'A' * 88}\n{key_footer}"
    fake_token = "notAReal" + "Token1234567890"
    fake_sid = "S-1-5-21-123456789-234567890-345678901-1001"
    private_text = f"""Contact person@private.invalid for access.
C:\\Users\\PrivatePerson\\Jarvis
{fake_sid}
API_TOKEN={fake_token}
{fake_key}
"""
    _write_page(
        root,
        "private.md",
        title="Private",
        slug="private",
        order=1,
        related=["missing-one", "missing-two"],
        body=_body("Private", extra=private_text),
    )

    categories = _categories(root)
    assert "privacy.email" in categories
    assert "privacy.windows-user-path" in categories
    assert "privacy.windows-sid" in categories
    assert "privacy.suspicious-assignment" in categories
    assert "privacy.private-key" in categories

    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(root)],
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert fake_token not in result.stderr
    assert fake_sid not in result.stderr
    assert "PrivatePerson" not in result.stderr
    assert all(": " in line for line in result.stderr.splitlines())


def test_malformed_frontmatter_is_reported_without_a_traceback(tmp_path: Path) -> None:
    root = tmp_path / "product"
    root.mkdir()
    (root / "broken.md").write_text("---\ntitle: [broken\n---\n# Broken\n", encoding="utf-8")

    assert _categories(root) == {"frontmatter.invalid-yaml"}


def test_manifest_requires_the_exact_reviewed_corpus(tmp_path: Path) -> None:
    root = tmp_path / "product"
    first = _write_page(
        root,
        "welcome.md",
        title="Welcome",
        slug="welcome",
        order=1,
        related=["missing-one", "missing-two"],
    )
    unlisted = _write_page(
        root,
        "unlisted.md",
        title="Unlisted",
        slug="unlisted",
        order=2,
        related=[],
    )
    source = tmp_path / "source.md"
    source.write_text("source", encoding="utf-8")
    manifest = tmp_path / "manifest.yaml"
    manifest.write_text(
        yaml.safe_dump(
            {
                "content_root": root.as_posix(),
                "page_count": 2,
                "pages": [
                    {
                        "file": first.as_posix(),
                        "title": "Wrong title",
                        "slug": "welcome",
                        "summary": FRIENDLY_SUMMARY,
                        "diataxis": "explanation",
                        "section": "Start",
                        "section_order": 1,
                        "order": 1,
                        "related": [],
                        "authoritative_sources": [source.as_posix()],
                    },
                    {
                        "file": (root / "missing.md").as_posix(),
                        "title": "Missing",
                        "slug": "missing",
                        "summary": "A missing page.",
                        "diataxis": "explanation",
                        "section": "Start",
                        "section_order": 1,
                        "order": 3,
                        "related": [],
                        "authoritative_sources": [source.as_posix()],
                    },
                ],
            },
            sort_keys=False,
        ),
        encoding="utf-8",
    )

    findings = check_paths([root], manifest_path=manifest)
    categories = {category for _path, category in findings}
    assert "manifest.file-missing" in categories
    assert "manifest.file-unlisted" in categories
    assert "manifest.metadata-mismatch.title" in categories
    assert unlisted.exists()
