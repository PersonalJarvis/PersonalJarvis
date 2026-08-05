#!/usr/bin/env python3
"""CI privacy gate — Wave 2, layer 3 (the non-bypassable backstop).

The local pre-push hook (layer 1) can be bypassed with `git push --no-verify`;
this CI job cannot. It re-runs the SAME secret / forbidden-file detection over
the changed files of a PR (reusing scripts/ci/privacy_pre_push.py as the single
source of truth) and — when the private-email block-set is provided via the
PRIVACY_PRIVATE_EMAILS env var (comma-separated) — also re-checks commit
identity.

It deliberately does NOT scan for the maintainer's real name / PII: that is the
ship skill's job on the way to the PUBLIC repo, and a name scan would
false-positive on the PRIVATE repo (where the real name legitimately lives).
The public-repo PII backstop is a separate, ship-time concern.

Secret VALUES are never echoed to the CI log (only pattern + path), so the gate
cannot itself leak a credential into a public build log.

Usage:
    python scripts/ci/privacy_scan_ci.py --base <ref> [--head <ref>]
"""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

# privacy_pre_push lives in the same directory; import it as the single source
# of truth for the detection logic (secret patterns, forbidden basenames,
# allowlist, diff reader, offender parser).
sys.path.insert(0, str(Path(__file__).resolve().parent))
import privacy_pre_push as gate  # noqa: E402


def _private_emails_from_env() -> set[str]:
    """Private emails to block, from the PRIVACY_PRIVATE_EMAILS env var.

    Comma-separated, lowercased. Kept in CI config (a repo variable), never in a
    tracked file, mirroring the local `git config privacy.private-email`.
    """
    raw = os.environ.get("PRIVACY_PRIVATE_EMAILS", "")
    return {e.strip().lower() for e in raw.split(",") if e.strip()}


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description="CI privacy gate")
    parser.add_argument("--base", required=True, help="diff base ref/sha")
    parser.add_argument("--head", default="HEAD", help="diff head ref/sha")
    args = parser.parse_args(argv[1:])

    blocked = False

    # (1) Secret / forbidden-file scan over the changed files. ----------------
    compiled, forbidden, allowlist = gate.load_secret_scanner(gate._repo_root())
    if compiled is None:
        print(
            "privacy-ci: WARNING secret scanner unavailable; secret scan SKIPPED.",
            file=sys.stderr,
        )
    else:
        try:
            files = gate.pushed_text_files(args.base, args.head)
        except Exception as exc:
            print(
                f"privacy-ci: ERROR could not diff {args.base}..{args.head} "
                f"({exc!r}).",
                file=sys.stderr,
            )
            return 1
        for rel, text in files:
            basename = rel.rsplit("/", 1)[-1]
            if gate.forbidden_file(basename, forbidden):
                blocked = True
                print(f"PRIVACY-CI BLOCK: forbidden secret file: {rel}", file=sys.stderr)
            for finding in gate.scan_text_for_secrets(rel, text, compiled, allowlist):
                blocked = True
                # Never echo the secret value into the (potentially public) log.
                print(
                    f"PRIVACY-CI BLOCK: secret ({finding['pattern']}) in "
                    f"{finding['path']}",
                    file=sys.stderr,
                )

    # (2) Commit-identity scan (only if the block-set is configured). ---------
    private_emails = _private_emails_from_env()
    if private_emails:
        try:
            log = gate._git(
                "log", "--format=%H%x09%ae%x09%ce", f"{args.base}..{args.head}"
            )
        except Exception as exc:
            print(
                f"privacy-ci: ERROR could not read git log "
                f"({exc!r}).",
                file=sys.stderr,
            )
            return 1
        for off in gate.offenders_from_log(log, private_emails):
            blocked = True
            print(
                f"PRIVACY-CI BLOCK: private maintainer email on {off['sha'][:12]} "
                f"({off['role']})",
                file=sys.stderr,
            )
    else:
        print(
            "privacy-ci: note PRIVACY_PRIVATE_EMAILS not set; identity check "
            "skipped (set it as a repo variable to enable).",
            file=sys.stderr,
        )

    if blocked:
        print(
            "\nPRIVACY-CI FAILED: remove the findings above before merging.",
            file=sys.stderr,
        )
        return 1
    print(
        "privacy-ci: OK — no secrets / forbidden files / private-email commits "
        "in the diff."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
