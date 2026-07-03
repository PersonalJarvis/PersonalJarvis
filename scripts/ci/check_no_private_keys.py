#!/usr/bin/env python3
"""Fail-closed guard: private signing-key material must NEVER enter the repo.

Why this exists
---------------
The installer is signed on four axes; two of them (Wave 2 Ed25519, Wave 4
ML-DSA-65) are key-bound. Those PRIVATE keys live ONLY in GitHub Actions
secrets (base64 of the PKCS#8 PEM) - never in the repo, not even encrypted.

The predecessor scheme committed the encrypted private keys
(`install/keys/*.key.enc`) plus a DEMO passphrase in plaintext in the docs.
The passphrase leaked into 14 public snapshots - a permanent, world-readable
exposure. This guard makes that class of mistake fail-closed: any attempt to
commit or push private-key material is blocked here, before it can reach the
public history where deletion no longer un-publishes it.

What it blocks (any of these is a CONFIRMED finding -> exit 1)
-------------------------------------------------------------
  1. A full PEM PRIVATE KEY block of any flavor
     (RSA / EC / DSA / OPENSSH / ENCRYPTED / PGP / PKCS#8 "PRIVATE KEY").
     A bare mention of the words "BEGIN PRIVATE KEY" (in a grep command, a
     detector regex, a doc) is NOT a block - only a full BEGIN..base64..END
     block is.
  2. A tracked `*.key` or `*.key.enc` file (encrypted-at-rest private key).
  3. A `WAVE2_CEREMONY_PASSPHRASE = <value>` assignment (the retired
     passphrase scheme). The GitHub Actions reference
     `${{ secrets.WAVE2_OFFLINE_KEY_B64 }}` has no inline value and is fine.

What it allows
--------------
  * `-----BEGIN PUBLIC KEY-----` blocks (public keys are meant to ship).
  * Secret *names* without a value (WAVE2_OFFLINE_KEY_B64, ...).
  * Paths listed in the allowlist (test fixtures with throwaway keys).

Modes
-----
  --staged   scan the STAGED blobs (wired into .githooks/pre-commit)
  (default)  scan every tracked file (wired into .githooks/pre-push + CI)

Exit codes: 0 = clean, 1 = confirmed finding. Fails OPEN (exit 0) only when it
genuinely cannot run (no git, unreadable tree) - the layered CI run is the
backstop. stdlib only, cross-platform (pathlib, UTF-8, subprocess git).
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

# Optional allowlist: repo-relative glob patterns, one per line, "#"=comment.
ALLOWLIST_FILE = Path("scripts/ci/privacy_gate/references/private-key-allowlist.txt")

# A FULL PEM private-key block: header, >=80 chars of base64 body, footer.
# The [A-Za-z0-9+/=\r\n] body class is what distinguishes a real key from a
# mere textual mention of the header line.
PEM_PRIVATE_RE = re.compile(
    r"-----BEGIN (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
    r"[\r\n]+[A-Za-z0-9+/=\r\n]{40,}?"
    r"-----END (?:[A-Z0-9 ]+ )?PRIVATE KEY-----"
)

# Retired passphrase scheme: an assignment with an actual secret-shaped value
# (>=12 base64 chars, no spaces/brackets). This catches `...=env++<secret>`
# while NOT tripping on prose that mentions the scheme or a `<placeholder>`,
# and never on the Actions secret reference `${{ secrets.WAVE2_... }}`.
PASSPHRASE_ASSIGN_RE = re.compile(r"WAVE2_CEREMONY_PASSPHRASE\s*=\s*[A-Za-z0-9+/]{12,}")

KEY_FILE_RE = re.compile(r"\.key(\.enc)?$", re.IGNORECASE)

# This guard file necessarily NAMES the patterns it hunts for; never scan it.
SELF = "scripts/ci/check_no_private_keys.py"


def _git(args: list[str]) -> str:
    return subprocess.run(
        ["git", *args], capture_output=True, text=True, check=True
    ).stdout


def _tracked_files(staged: bool) -> list[str]:
    if staged:
        out = _git(["diff", "--cached", "--name-only", "--diff-filter=ACM"])
    else:
        out = _git(["ls-files"])
    return [ln for ln in out.splitlines() if ln.strip()]


def _read(path: str, staged: bool) -> str | None:
    try:
        if staged:
            return subprocess.run(
                ["git", "show", f":{path}"],
                capture_output=True, check=True,
            ).stdout.decode("utf-8", "replace")
        return Path(path).read_text(encoding="utf-8", errors="replace")
    except (subprocess.CalledProcessError, OSError):
        return None


def _load_allowlist() -> list[str]:
    if not ALLOWLIST_FILE.exists():
        return []
    pats = []
    for ln in ALLOWLIST_FILE.read_text(encoding="utf-8").splitlines():
        ln = ln.strip()
        if ln and not ln.startswith("#"):
            pats.append(ln)
    return pats


def _allowed(path: str, patterns: list[str]) -> bool:
    p = Path(path)
    return any(p.match(pat) or path == pat for pat in patterns)


def main() -> int:
    staged = "--staged" in sys.argv[1:]
    try:
        files = _tracked_files(staged)
    except (subprocess.CalledProcessError, FileNotFoundError):
        # No git / not a repo -> cannot run -> fail OPEN (CI is the backstop).
        return 0

    allowlist = _load_allowlist()
    findings: list[str] = []

    for path in files:
        if path == SELF or _allowed(path, allowlist):
            continue

        if KEY_FILE_RE.search(path):
            findings.append(
                f"{path}: tracked private-key file (*.key/*.key.enc) - private "
                f"keys belong ONLY in GitHub Actions secrets, never in the repo"
            )
            continue

        # Only scan plausibly-textual files; skip large/binary blobs cheaply.
        text = _read(path, staged)
        if text is None or "\x00" in text[:4096]:
            continue

        if PEM_PRIVATE_RE.search(text):
            m = PEM_PRIVATE_RE.search(text)
            line = text[: m.start()].count("\n") + 1
            findings.append(
                f"{path}:{line}: full PEM PRIVATE KEY block - never commit a "
                f"private key (public keys / GitHub secrets only)"
            )
        if PASSPHRASE_ASSIGN_RE.search(text):
            m = PASSPHRASE_ASSIGN_RE.search(text)
            line = text[: m.start()].count("\n") + 1
            findings.append(
                f"{path}:{line}: WAVE2_CEREMONY_PASSPHRASE=<value> - the "
                f"passphrase scheme is retired; keys live only in secrets"
            )

    if findings:
        sys.stderr.write(
            "check_no_private_keys: BLOCKED - private-key material must never "
            "enter the repo (CLAUDE.md rule). Findings:\n"
        )
        for f in findings:
            sys.stderr.write(f"  - {f}\n")
        sys.stderr.write(
            "\nFix: remove the key material. Store signing private keys ONLY as\n"
            "GitHub Actions secrets (base64 of the PEM): the workflow reads\n"
            "WAVE2_OFFLINE_KEY_B64 / WAVE4_MLDSA65_KEY_B64. Keep a local backup\n"
            "in your password manager. See docs/supply-chain/wave2-key-ceremony.md.\n"
        )
        return 1

    print("check_no_private_keys: OK - no private-key material in the tree.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
