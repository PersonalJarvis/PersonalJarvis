#!/usr/bin/env python3
"""Fail-closed gate: the README must not contain repo-relative URLs.

The README is the PyPI project page's whole body, and PyPI renders it on its
OWN domain — it does not resolve paths against the source repository the way
GitHub does. So `src="assets/brand/banner.png"` is a broken image there and
`](docs/BUGS.md)` is a dead link, while both look perfect in the repo. That
asymmetry is why this regresses silently: the person adding the link sees it
work.

`twine check` does not catch it. Twine maps `text/markdown` to no renderer at
all (its own comment: "Rendering cannot fail"), so a Markdown long_description
is never rendered and always passes — a green tick that inspected nothing.
This gate is what actually stands between a relative path and a published
project page full of broken images.

Absolute GitHub URLs render identically on GitHub, so there is exactly one
README, not a repo copy and a PyPI copy.

Cross-platform, stdlib-only. Exit codes: 0 = gate passed, 1 = gate failed,
2 = usage error.
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

# A target is fine if it addresses something outside the repo tree, or a
# fragment on the page itself.
_ABSOLUTE_PREFIXES = ("http://", "https://", "#", "mailto:", "data:", "//")

# HTML attributes that resolve against the page's base URL.
_HTML_URL = re.compile(r'\b(src|href)=(["\'])([^"\']+)\2')
# Markdown inline links and images: [label](target) / ![alt](target).
_MARKDOWN_URL = re.compile(r"!?\[[^\]]*\]\(([^)\s]+)\)")


def find_relative_urls(text: str) -> list[tuple[int, str]]:
    """Return every ``(line_number, target)`` that PyPI could not resolve."""
    offenders: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        targets = [match.group(3) for match in _HTML_URL.finditer(line)]
        targets += _MARKDOWN_URL.findall(line)
        for target in targets:
            if not target.startswith(_ABSOLUTE_PREFIXES):
                offenders.append((lineno, target))
    return offenders


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "readme",
        nargs="?",
        default="README.md",
        help="README to check (default: README.md in the current directory)",
    )
    args = parser.parse_args(argv)

    path = Path(args.readme)
    if not path.is_file():
        print(f"check_readme_absolute_urls: no such file: {path}", file=sys.stderr)
        return 2

    offenders = find_relative_urls(path.read_text(encoding="utf-8"))
    if not offenders:
        print(f"check_readme_absolute_urls: OK - {path} has no repo-relative URLs.")
        return 0

    print(
        f"check_readme_absolute_urls: FAILED - {len(offenders)} repo-relative URL(s) "
        f"in {path}. PyPI cannot resolve these; they render as broken images and "
        "dead links on the project page. Use the full "
        "https://github.com/PersonalJarvis/PersonalJarvis/{raw,blob,tree}/main/... "
        "form instead:",
        file=sys.stderr,
    )
    for lineno, target in offenders:
        print(f"    {path}:{lineno}: {target}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
