#!/usr/bin/env python3
"""Regenerate the README's contributor avatar wall from the GitHub API.

The wall lives between two HTML comment markers in ``README.md`` so the rest of
the file is never touched:

    <!-- contributors:start -->
    ...generated avatars...
    <!-- contributors:end -->

Run it with no arguments to rewrite the block in place, or with ``--check`` to
fail when the block is stale (that is the CI mode).

Two kinds of entries are deliberately excluded, both of which would otherwise
credit somebody who never worked on this project:

* GitHub Apps and bots (``type == "Bot"``, or a ``[bot]`` suffix) - Dependabot
  opening dependency bumps is not a contributor.
* The accounts in ``EXCLUDED_LOGINS``. ``Co-Authored-By: Claude
  <noreply@anthropic.com>`` trailers are attributed by GitHub to whichever
  account happens to own that address, which is a stranger to this repository.
  Crediting them on the README would be wrong twice over: it names the wrong
  person, and it presents an AI trailer as a human contributor.

Requires no dependencies. A token is optional but lifts the rate limit: the
script reads ``GITHUB_TOKEN`` (set for free inside GitHub Actions) and falls
back to an unauthenticated request.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

REPO = "PersonalJarvis/PersonalJarvis"
README = Path(__file__).resolve().parents[2] / "README.md"

START = "<!-- contributors:start -->"
END = "<!-- contributors:end -->"

#: Accounts that the contributors API reports but that never contributed here.
#: See the module docstring for why the AI co-author trailer lands on a
#: third-party account.
EXCLUDED_LOGINS = frozenset({"claude"})

#: Avatars per row, matching the layout OpenClaw uses. Purely cosmetic.
PER_ROW = 10
AVATAR_PX = 48


def fetch_contributors() -> list[dict]:
    """Every human contributor, most commits first."""
    people: list[dict] = []
    page = 1
    while True:
        url = f"https://api.github.com/repos/{REPO}/contributors?per_page=100&page={page}"
        req = urllib.request.Request(
            url,
            headers={
                "Accept": "application/vnd.github+json",
                "User-Agent": "personal-jarvis-contributors",
            },
        )
        token = os.environ.get("GITHUB_TOKEN")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            # noqa justification: the URL is a literal https constant built from
            # REPO above, never user input, so no custom scheme can reach here.
            with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310
                batch = json.load(resp)
        except urllib.error.HTTPError as exc:  # pragma: no cover - network path
            raise SystemExit(f"GitHub API returned {exc.code}: {exc.reason}") from exc
        if not batch:
            break
        people.extend(batch)
        if len(batch) < 100:
            break
        page += 1

    return [
        p
        for p in people
        if p.get("type") != "Bot"
        and not p.get("login", "").endswith("[bot]")
        and p.get("login") not in EXCLUDED_LOGINS
    ]


def render(people: list[dict]) -> str:
    """The avatar wall, one row per ``PER_ROW`` people."""
    if not people:
        return "_Nobody yet. That first square is yours._"

    cells = [
        f'<a href="https://github.com/{p["login"]}">'
        f'<img src="{p["avatar_url"]}&s={AVATAR_PX}" '
        f'width="{AVATAR_PX}" height="{AVATAR_PX}" alt="{p["login"]}"></a>'
        for p in people
    ]
    rows = [" ".join(cells[i : i + PER_ROW]) for i in range(0, len(cells), PER_ROW)]
    return "\n".join(rows)


def splice(text: str, block: str) -> str:
    """Replace whatever sits between the markers with ``block``."""
    try:
        i = text.index(START) + len(START)
        j = text.index(END)
    except ValueError:
        raise SystemExit(
            f"README.md is missing the {START} / {END} markers - add the "
            f"Contributors section before running this.",
        ) from None
    return text[:i] + "\n\n" + block + "\n\n" + text[j:]


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument(
        "--check",
        action="store_true",
        help="exit non-zero when the block is stale instead of rewriting it",
    )
    args = ap.parse_args()

    current = README.read_text(encoding="utf-8")
    people = fetch_contributors()
    updated = splice(current, render(people))

    if updated == current:
        print(f"contributors: up to date ({len(people)} people)")
        return 0

    if args.check:
        print(
            f"contributors: README is STALE ({len(people)} people on GitHub). "
            f"Run `python scripts/ci/update_contributors.py` and commit the result.",
            file=sys.stderr,
        )
        return 1

    README.write_text(updated, encoding="utf-8", newline="\n")
    print(f"contributors: README updated ({len(people)} people)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
