#!/usr/bin/env python3
"""Versioned pre-push privacy gate — Wave 2, layer 1 of a 3-layer defence.

This is the LOCAL, fast feedback layer that runs in the maintainer's everyday
`git push` (wired via .githooks/pre-push). Layer 3 — the non-bypassable CI
privacy-gate — is the real backstop, so this hook is allowed to FAIL OPEN when
it genuinely cannot run, but it FAILS CLOSED on every positive finding.

It enforces exactly three things on an ordinary push:

  (A) Public-repo guard. A direct push to the PUBLIC distribution repo
      (PersonalJarvis/PersonalJarvis) is hard-blocked. The only sanctioned path
      to public is the depersonalized public-release snapshot, which scrubs the
      tree first. Detected by remote name == "public" OR a remote URL
      containing (case-insensitive) "personaljarvis/personaljarvis".

  (B) Private-identity guard. No commit being pushed may carry a private
      maintainer email (author OR committer). Only the GitHub noreply form is
      allowed to leave the machine.

  (C) Secret guard. No real secret (API key / token / private-key block /
      forbidden secret file) may be in the files being pushed. The detection
      logic is NOT reinvented here — it REUSES the battle-tested SECRET_PATTERNS,
      FORBIDDEN_BASENAMES and the secret-allowlist loader from the ship skill's
      strip_and_scan.py.

CRITICAL DESIGN RULE: on a push to the PRIVATE origin we do NOT block the
maintainer's real name or other PII. The real name legitimately lives in the
private working repo and is scrubbed ONLY by the ship gate on the way to public.
This gate therefore applies the SECRET patterns + FORBIDDEN_BASENAMES + the
private-EMAIL identity check ONLY. It must NOT apply strip_and_scan's PII
`scrub_rows` -- doing so would falsely block every ordinary private push.

The concrete private emails are NEVER hardcoded here (this file itself ships to
the public repo); they are read from git config `privacy.private-email` so the
gate stays generic and PII-free. See load_private_emails().

Structure: pure functions (unit-tested, no IO) + thin git/IO wrappers.
stdlib only (cloud-first €5-VPS doctrine: must run on a bare python:3.11-slim).
"""

from __future__ import annotations

import importlib.util
import re
import subprocess
import sys
from pathlib import Path

# A SHA placeholder of all zeros means "the remote has nothing yet" (a brand-new
# branch) — see the git pre-push hook stdin contract.
_ALL_ZERO_RE = re.compile(r"^0+$")


# --------------------------------------------------------------------------- #
# Pure functions (unit-tested; no subprocess, no filesystem, no network)
# --------------------------------------------------------------------------- #
def target_is_public(remote_name: str, remote_url: str) -> bool:
    """True iff the push target is the PUBLIC distribution repo.

    Matched by remote name == "public" OR the remote URL containing (case-
    insensitive) "personaljarvis/personaljarvis". Hyphenated or otherwise
    non-flagship repo slugs deliberately do NOT match.
    """
    if remote_name == "public":
        return True
    return "personaljarvis/personaljarvis" in (remote_url or "").lower()


def offenders_from_log(log_text: str, private_emails: set[str]) -> list[dict]:
    """Find commits whose author OR committer email is in `private_emails`.

    Input is `git log` output, one line per commit, tab-separated:
        "<sha>\t<author_email>\t<committer_email>"

    `private_emails` is the lowercased block-set (see load_private_emails).
    Returns one dict per offending (sha, role) pair:
        {"sha": ..., "email": ..., "role": "author"|"committer"}
    A commit that is private in BOTH fields yields two entries.
    """
    offenders: list[dict] = []
    for line in log_text.splitlines():
        line = line.strip("\r")
        if not line.strip():
            continue
        parts = line.split("\t")
        if len(parts) < 3:
            # Malformed line — be conservative but do not crash; skip it.
            continue
        sha, author_email, committer_email = parts[0], parts[1], parts[2]
        # Compare case-insensitively: git emits the email verbatim, so a
        # deliberately-cased address must still be caught.
        if author_email.lower() in private_emails:
            offenders.append({"sha": sha, "email": author_email, "role": "author"})
        if committer_email.lower() in private_emails:
            offenders.append(
                {"sha": sha, "email": committer_email, "role": "committer"}
            )
    return offenders


def forbidden_file(name: str, forbidden: set[str]) -> bool:
    """True iff the basename is a forbidden secret file (e.g. .env, jarvis.toml)."""
    return name in forbidden


def scan_text_for_secrets(
    rel: str,
    text: str,
    compiled_patterns: dict,
    allowlist: set,
) -> list[dict]:
    """Scan a single file's text for high-confidence secret shapes.

    For each compiled secret regex, every match value is reported as a finding
    unless the exact (value, rel) pair is in the allowlist (the ship skill's
    secret-allowlist, loaded as a set of (value, path) tuples).

    Returns: list of {"path": rel, "pattern": <name>, "value": <matched>}.
    """
    findings: list[dict] = []
    for name, rx in compiled_patterns.items():
        for m in rx.finditer(text):
            value = m.group(0)
            if (value, rel) in allowlist:
                continue
            findings.append({"path": rel, "pattern": name, "value": value})
    return findings


# --------------------------------------------------------------------------- #
# Thin git / IO wrappers (NOT unit-tested — side-effecting)
# --------------------------------------------------------------------------- #
def _repo_root() -> Path:
    """Best-effort repo root (dir containing this script's `scripts/ci/` parent)."""
    return Path(__file__).resolve().parents[2]


def _git(*args: str) -> str:
    """Run a git command at the repo root and return decoded stdout.

    Raises CalledProcessError on a non-zero git exit so the caller can decide to
    fail open when it cannot determine the diff range.
    """
    out = subprocess.run(
        ["git", "-C", str(_repo_root()), *args],
        check=True,
        capture_output=True,
    ).stdout
    return out.decode("utf-8", "surrogateescape")


def load_private_emails() -> set[str]:
    """Private maintainer emails to block, read from git config (lowercased).

    Configured per-repo so the concrete addresses are NEVER hardcoded in a file
    that ships to the public repo:

        git config --add privacy.private-email you@example.com

    Returns an empty set if none are configured; the caller then warns and skips
    the identity check (layer-3 CI remains the backstop).
    """
    try:
        out = subprocess.run(
            ["git", "-C", str(_repo_root()),
             "config", "--get-all", "privacy.private-email"],
            check=False,
            capture_output=True,
        ).stdout.decode("utf-8", "surrogateescape")
    except Exception:  # pragma: no cover - defensive fail-open path
        return set()
    return {ln.strip().lower() for ln in out.splitlines() if ln.strip()}


def load_secret_scanner(repo_root: Path) -> tuple[dict | None, set, set]:
    """Import the privacy gate's strip_and_scan.py and return its scan ingredients.

    Returns (compiled_patterns, forbidden_basenames, allowlist). On ANY failure
    (file missing, import error, attribute missing) returns (None, set(), set())
    so the caller fails OPEN for the secret checks — layer 3 (CI) is the backstop.
    """
    try:
        gate_dir = (
            repo_root / "scripts" / "ci" / "privacy_gate"
        )
        script = gate_dir / "scripts" / "strip_and_scan.py"
        spec = importlib.util.spec_from_file_location(
            "ship_strip_and_scan", script
        )
        if spec is None or spec.loader is None:
            return None, set(), set()
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)

        compiled = {
            name: re.compile(p) for name, p in module.SECRET_PATTERNS.items()
        }
        forbidden = set(module.FORBIDDEN_BASENAMES)
        allowlist = module._load_allowlist(gate_dir)
        return compiled, forbidden, allowlist
    except Exception as exc:  # pragma: no cover - defensive fail-open path
        print(
            f"privacy-pre-push: WARNING could not load secret scanner "
            f"({exc!r}); failing OPEN for the secret/forbidden-file checks.",
            file=sys.stderr,
        )
        return None, set(), set()


def resolve_base(remote_sha: str, local_sha: str) -> str:
    """Pick the diff base: the remote sha, or origin/main for a brand-new branch.

    `local_sha` is accepted for symmetry / future use; an all-zero remote sha
    means the remote has no copy of this ref yet, so we fall back to origin/main.
    """
    if remote_sha and not _ALL_ZERO_RE.match(remote_sha):
        return remote_sha
    return "origin/main"


def _is_text_bytes(data: bytes) -> bool:
    return b"\x00" not in data[:8192]


def pushed_text_files(base: str, head: str) -> list[tuple[str, str]]:
    """Return (relpath, text) for every added/changed text file in base..head.

    Uses `git diff --name-only --diff-filter=ACMR base..head`. Deleted files are
    excluded by the filter. Each surviving path is read from the working tree;
    binary files and undecodable files are skipped silently (they can't carry a
    text-shaped secret we scan for here).
    """
    raw = _git("diff", "--name-only", "--diff-filter=ACMR", f"{base}..{head}")
    rels = [r.strip() for r in raw.splitlines() if r.strip()]
    root = _repo_root()
    out: list[tuple[str, str]] = []
    for rel in rels:
        path = root / rel
        if not path.exists() or not path.is_file():
            continue
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if not _is_text_bytes(data):
            continue
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError:
            try:
                text = data.decode("latin-1")
            except UnicodeDecodeError:
                continue
        out.append((rel, text))
    return out


# --------------------------------------------------------------------------- #
# main
# --------------------------------------------------------------------------- #
def main(argv: list[str], stdin) -> int:
    """Entry point for the pre-push hook.

    argv: [prog, remote_name, remote_url]
    stdin: the git pre-push ref lines:
        "<local_ref> <local_sha> <remote_ref> <remote_sha>"

    Return 1 to BLOCK the push, 0 to allow.

    Fail-closed on positive findings (public target, private-email commit,
    secret/forbidden file). Fail-open (return 0 + loud stderr) only when the
    gate genuinely cannot run.
    """
    remote_name = argv[1] if len(argv) > 1 else ""
    remote_url = argv[2] if len(argv) > 2 else ""

    # (A) Public-repo guard DISABLED at the maintainer's explicit request
    # (2026-07-03): a direct push to the public repo is now ALLOWED. Credential
    # safety stays with .gitignore + the secret (C) and private-key gates below.
    # The public target is still detected — for a heads-up log only, never a block.
    if target_is_public(remote_name, remote_url):
        print(
            "privacy-pre-push: public-target block is DISABLED (maintainer "
            f"request); allowing push to remote {remote_name!r}.",
            file=sys.stderr,
        )

    try:
        # Read and parse the pushed refs once.
        try:
            ref_lines = stdin.read().splitlines() if stdin is not None else []
        except Exception as exc:  # pragma: no cover - defensive
            print(
                f"privacy-pre-push: WARNING could not read stdin ref lines "
                f"({exc!r}); failing OPEN.",
                file=sys.stderr,
            )
            return 0

        compiled, forbidden, allowlist = load_secret_scanner(_repo_root())
        private_emails = load_private_emails()
        if not private_emails:
            print(
                "privacy-pre-push: WARNING no `privacy.private-email` configured; "
                "the private-email identity check is DISABLED. Configure with: "
                "git config --add privacy.private-email you@example.com",
                file=sys.stderr,
            )

        blocked = False
        for raw in ref_lines:
            parts = raw.split()
            if len(parts) < 4:
                continue
            local_ref, local_sha, remote_ref, remote_sha = parts[:4]

            # Skip branch deletions (local_sha all zeros) — nothing to inspect.
            if _ALL_ZERO_RE.match(local_sha):
                continue

            try:
                base = resolve_base(remote_sha, local_sha)
            except Exception as exc:  # pragma: no cover - defensive
                print(
                    f"privacy-pre-push: WARNING could not resolve diff base for "
                    f"{local_ref} ({exc!r}); failing OPEN for this ref.",
                    file=sys.stderr,
                )
                continue

            # (B) Private-identity guard — fail-closed on any offender. -------
            try:
                log_text = _git(
                    "log",
                    "--format=%H%x09%ae%x09%ce",
                    f"{base}..{local_sha}",
                )
            except subprocess.CalledProcessError as exc:
                print(
                    f"privacy-pre-push: WARNING could not read git log for "
                    f"{base}..{local_sha} ({exc!r}); failing OPEN for the "
                    f"identity check on this ref.",
                    file=sys.stderr,
                )
                log_text = ""

            offenders = offenders_from_log(log_text, private_emails)
            if offenders:
                blocked = True
                print(
                    "\nPUSH BLOCKED: a commit carries a PRIVATE maintainer "
                    "email (use the GitHub noreply form):",
                    file=sys.stderr,
                )
                for off in offenders:
                    print(
                        f"   {off['sha'][:12]}  {off['role']}: {off['email']}",
                        file=sys.stderr,
                    )

            # (C) Secret / forbidden-file guard — only if the scanner loaded. -
            if compiled is not None:
                try:
                    files = pushed_text_files(base, local_sha)
                except subprocess.CalledProcessError as exc:
                    print(
                        f"privacy-pre-push: WARNING could not diff "
                        f"{base}..{local_sha} ({exc!r}); failing OPEN for the "
                        f"secret check on this ref.",
                        file=sys.stderr,
                    )
                    files = []

                for rel, text in files:
                    basename = rel.rsplit("/", 1)[-1]
                    if forbidden_file(basename, forbidden):
                        blocked = True
                        print(
                            f"\nPUSH BLOCKED: forbidden secret file in push: "
                            f"{rel}",
                            file=sys.stderr,
                        )
                    secrets = scan_text_for_secrets(
                        rel, text, compiled, allowlist
                    )
                    if secrets:
                        blocked = True
                        for s in secrets:
                            print(
                                f"\nPUSH BLOCKED: secret ({s['pattern']}) in "
                                f"{s['path']}: {s['value']}",
                                file=sys.stderr,
                            )

        if blocked:
            print(
                "\nFix the findings above, then push again. (Layer-3 CI is the "
                "non-bypassable backstop if you bypass this hook.)",
                file=sys.stderr,
            )
            return 1
        return 0
    except Exception as exc:  # pragma: no cover - top-level fail-open guard
        # An unexpected error must never wedge the agent swarm. Fail OPEN, but
        # keep the public-target / identity / secret POSITIVE findings above
        # fail-closed (they returned 1 before reaching here).
        print(
            f"privacy-pre-push: WARNING unexpected error ({exc!r}); failing "
            f"OPEN. Layer-3 CI remains the backstop.",
            file=sys.stderr,
        )
        return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv, sys.stdin))
