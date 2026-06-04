"""Minimal YAML frontmatter parser/writer.

We do not use an external dependency (`python-frontmatter`) because the format
is simple and we already have PyYAML. A `.md` file with frontmatter looks like:

    ---
    key: value
    list:
      - one
    ---

    # Body Markdown

`parse_frontmatter(text) -> (dict, body)` and
`write_frontmatter(meta, body) -> text` are the only API entry points.

Important: when no frontmatter is present, `parse_frontmatter` returns an
empty dict and the complete text as the body. This keeps legacy files working.
"""
from __future__ import annotations

from typing import Any

import yaml

FRONTMATTER_DELIM = "---"


def parse_frontmatter(text: str) -> tuple[dict[str, Any], str]:
    """Splits frontmatter from the Markdown body."""
    if not text.startswith(FRONTMATTER_DELIM + "\n") and not text.startswith(FRONTMATTER_DELIM + "\r\n"):
        return {}, text

    # Split at the first and second --- delimiter
    lines = text.splitlines(keepends=True)
    # First line is "---"
    end_idx = -1
    for i in range(1, len(lines)):
        if lines[i].strip() == FRONTMATTER_DELIM:
            end_idx = i
            break
    if end_idx == -1:
        return {}, text

    frontmatter_text = "".join(lines[1:end_idx])
    body = "".join(lines[end_idx + 1 :])

    # Strip leading newline after frontmatter (cosmetic)
    if body.startswith("\n"):
        body = body[1:]
    elif body.startswith("\r\n"):
        body = body[2:]

    try:
        meta = yaml.safe_load(frontmatter_text) or {}
        if not isinstance(meta, dict):
            meta = {"_raw": meta}
    except yaml.YAMLError:
        meta = {}
    return meta, body


def write_frontmatter(meta: dict[str, Any], body: str) -> str:
    """Renders (meta, body) back into a Markdown file text."""
    yaml_text = yaml.safe_dump(
        meta,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        indent=2,
    )
    body_clean = body.lstrip("\n")
    return f"{FRONTMATTER_DELIM}\n{yaml_text}{FRONTMATTER_DELIM}\n\n{body_clean}"


def replace_section(body: str, marker: str, content: str) -> str:
    """Replaces the content between `<!-- curator:<marker>:start/end -->` markers.

    `content` is inserted **between** the markers — the markers themselves are
    preserved. If the markers do not exist, the body is returned unchanged and
    we log the issue (no exception — we never want to lose the file).
    """
    start = f"<!-- curator:{marker}:start -->"
    end = f"<!-- curator:{marker}:end -->"
    i = body.find(start)
    j = body.find(end)
    if i == -1 or j == -1 or j < i:
        return body

    before = body[: i + len(start)]
    after = body[j:]
    # Surround the content between the markers with blank lines for readability
    content_clean = content.strip("\n")
    middle = f"\n{content_clean}\n" if content_clean else "\n"
    return before + middle + after


def append_to_section(body: str, marker: str, line: str) -> str:
    """Appends a single line to the existing section (instead of replacing it)."""
    start = f"<!-- curator:{marker}:start -->"
    end = f"<!-- curator:{marker}:end -->"
    i = body.find(start)
    j = body.find(end)
    if i == -1 or j == -1 or j < i:
        return body

    current_block = body[i + len(start) : j]
    lines = [l.rstrip() for l in current_block.strip("\n").splitlines() if l.strip()]
    line_clean = line.strip()
    if line_clean and line_clean not in lines:
        lines.append(line_clean)
    new_block = "\n" + "\n".join(lines) + "\n" if lines else "\n"
    return body[: i + len(start)] + new_block + body[j:]
