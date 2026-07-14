#!/usr/bin/env python3
"""Deterministic privacy gate for the depersonalized public-release snapshot.

This script is the *deterministic core* of the public-release workflow —
everything that decides *what leaves the machine* lives here so it is
reproducible and not subject to LLM improvisation. The human-facing parts
(choosing the version bump, showing the review, committing, tagging, pushing)
are driven separately by the maintainer.

Three subcommands:

  build   Export the working tree's git-tracked files into a clean staging dir:
          apply the distribution denylist, run the deterministic PII scrub,
          write a build report. (Layers A+B+C of the gate.)

  scan    Run the blocking secret/PII scan over a tree (the staging tree, and
          again over the reconciled distribution tree before commit). Exits
          non-zero if any BLOCKING finding survives the allowlist. (Layer D.)

  set-version  Set the version string in pyproject.toml + jarvis/__init__.py
               inside a staged tree and assert the two agree.

Design rules:
  * stdlib only (cloud-first: must run on a bare python:3.11-slim container).
  * fail-closed: anything uncertain is reported and, for `scan`, blocks.
  * the LLM may only ADD findings on top of this, never clear what this blocks.

Reports are JSON so the human review can summarise them.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

# ----------------------------------------------------------------------------
# Block-tier secret patterns (security logic — intentionally hardcoded, not in
# a data file, so they cannot be silently weakened by editing a reference file).
#
# Lengths are tuned to REAL credential shapes. Intentional test fixtures in this
# repo (e.g. "sk-leakable-XYZ-789", "AIzaSyB1234567890abcdefXYZ", "test-key-xxx")
# are deliberately shorter/hyphenated and do NOT match these — so they don't
# train the user to ignore the gate. A genuinely leaked key is always full length
# and WILL match.
# ----------------------------------------------------------------------------
SECRET_PATTERNS: dict[str, str] = {
    "openai_legacy": r"\bsk-[A-Za-z0-9]{48}\b",
    "openai_project": r"\bsk-proj-[A-Za-z0-9_-]{40,}\b",
    "anthropic": r"\bsk-ant-[A-Za-z0-9_-]{60,}\b",
    "google_api_key": r"\bAIza[0-9A-Za-z_-]{35}\b",
    "github_token": r"\bgh[pousr]_[A-Za-z0-9]{36}\b",
    "aws_access_key": r"\bAKIA[0-9A-Z]{16}\b",
    "slack_token": r"\bxox[baprs]-[0-9A-Za-z-]{10,}\b",
    "groq_key": r"\bgsk_[A-Za-z0-9]{40,}\b",
    "google_oauth_secret": r"\bGOCSPX-[A-Za-z0-9_-]{20,}\b",
    "private_key_block": r"-----BEGIN (?:RSA |EC |DSA |OPENSSH |PGP )?PRIVATE KEY-----",
}

# Filenames that must never appear in a shipping tree even if some path slips the
# .gitignore / denylist (defence in depth). Matched on the basename.
FORBIDDEN_BASENAMES = (
    ".env",
    "jarvis.toml",
    "mcp.json",
)
FORBIDDEN_SUFFIXES = (".pem", ".key", ".sqlite", ".sqlite3", ".db")

# How many bytes to sniff for a NUL byte when deciding text vs binary.
_SNIFF = 8192

# Text types the English-only artifact gate inspects (mirrors
# scripts/ci/check_no_new_german.py SCAN_EXT). Data blobs / binaries are skipped.
_GERMAN_EXT = frozenset(
    {
        ".py", ".md", ".txt", ".rst", ".ts", ".tsx", ".js", ".jsx",
        ".json", ".toml", ".yaml", ".yml", ".html", ".css", ".cfg", ".ini",
    }
)


# ----------------------------------------------------------------------------
# Small helpers
# ----------------------------------------------------------------------------
def _eprint(*a: object) -> None:
    print(*a, file=sys.stderr)


def _git_tracked(repo: Path) -> list[str]:
    """Return tracked paths (posix, repo-relative), sorted, via `git ls-files`."""
    out = subprocess.run(
        ["git", "-C", str(repo), "ls-files", "-z"],
        check=True,
        capture_output=True,
    ).stdout
    paths = [p for p in out.decode("utf-8", "surrogateescape").split("\0") if p]
    return sorted(paths)


def _compile_glob(pattern: str) -> re.Pattern[str]:
    """Translate a gitignore-ish glob into an anchored regex over posix relpaths.

    Supports `**` (any depth, incl. zero dirs), `*` (within a segment), `?`.
    A trailing `/**` matches the directory's whole subtree.
    """
    pattern = pattern.strip().replace("\\", "/")
    out: list[str] = ["^"]
    i = 0
    while i < len(pattern):
        c = pattern[i]
        if pattern.startswith("**/", i):
            out.append("(?:.*/)?")
            i += 3
        elif pattern.startswith("**", i):
            out.append(".*")
            i += 2
        elif c == "*":
            out.append("[^/]*")
            i += 1
        elif c == "?":
            out.append("[^/]")
            i += 1
        else:
            out.append(re.escape(c))
            i += 1
    out.append("$")
    return re.compile("".join(out))


def _matches_any(relpath: str, regexes: list[re.Pattern[str]]) -> bool:
    return any(rx.match(relpath) for rx in regexes)


def _read_lines(path: Path) -> list[str]:
    if not path.exists():
        return []
    return [
        ln.rstrip("\n")
        for ln in path.read_text("utf-8").splitlines()
        if ln.strip() and not ln.lstrip().startswith("#")
    ]


def _load_denylist(skill_dir: Path) -> list[re.Pattern[str]]:
    raw = _read_lines(skill_dir / "references" / "distribution-denylist.txt")
    return [_compile_glob(p) for p in raw]


def _load_scrub(skill_dir: Path) -> list[dict]:
    """Load pii-scrub.tsv → list of {action, pattern, replacement, note}.

    Columns (tab-separated): action <TAB> pattern <TAB> replacement <TAB> note
    action ∈ {scrub, block-only, warn}. `scrub` rows are substituted during
    build; all rows are residual checks during scan.
    """
    rows: list[dict] = []
    for ln in _read_lines(skill_dir / "references" / "pii-scrub.tsv"):
        parts = ln.split("\t")
        if len(parts) < 2:
            continue
        action = parts[0].strip()
        pattern = parts[1]
        replacement = parts[2] if len(parts) > 2 else ""
        note = parts[3] if len(parts) > 3 else ""
        rows.append(
            {
                "action": action,
                "pattern": pattern,
                "replacement": replacement,
                "note": note,
                "rx": re.compile(pattern),
            }
        )
    return rows


def _load_allowlist(skill_dir: Path) -> set[tuple[str, str]]:
    """Load secret-allowlist.tsv → set of (exact_value, exact_relpath)."""
    allow: set[tuple[str, str]] = set()
    for ln in _read_lines(skill_dir / "references" / "secret-allowlist.tsv"):
        parts = ln.split("\t")
        if len(parts) >= 2:
            allow.add((parts[0], parts[1].replace("\\", "/")))
    return allow


def _load_scrub_exempt(skill_dir: Path) -> list[re.Pattern[str]]:
    raw = _read_lines(skill_dir / "references" / "scrub-exempt.txt")
    return [_compile_glob(p) for p in raw]


def _load_keep(skill_dir: Path) -> list[re.Pattern[str]]:
    raw = _read_lines(skill_dir / "references" / "dist-only-keep.txt")
    return [_compile_glob(p) for p in raw]


def _is_text(data: bytes) -> bool:
    return b"\x00" not in data[:_SNIFF]


def _decode(data: bytes) -> tuple[str, str]:
    """Return (text, marker) so we can write back faithfully.

    marker ∈ {"", "bom", "latin1"}. latin-1 is a lossless byte<->char bijection,
    so a file that is not valid UTF-8 still round-trips (decode+encode latin-1)
    and we never crash on an odd encoding (real distribution clones have them).
    """
    if data.startswith(b"\xef\xbb\xbf"):
        try:
            return data[3:].decode("utf-8"), "bom"
        except UnicodeDecodeError:
            return data.decode("latin-1"), "latin1"
    try:
        return data.decode("utf-8"), ""
    except UnicodeDecodeError:
        return data.decode("latin-1"), "latin1"


def _encode(text: str, marker: str) -> bytes:
    if marker == "latin1":
        return text.encode("latin-1")
    out = text.encode("utf-8")
    return b"\xef\xbb\xbf" + out if marker == "bom" else out


def _iter_text_files(tree: Path):
    for p in sorted(tree.rglob("*")):
        if ".git" in p.parts:  # never scan/scrub git internals
            continue
        if not p.is_file():
            continue
        try:
            data = p.read_bytes()
        except OSError:
            continue
        if _is_text(data):
            yield p, data


# ----------------------------------------------------------------------------
# build
# ----------------------------------------------------------------------------
def cmd_build(args: argparse.Namespace) -> int:
    working = Path(args.working).resolve()
    staging = Path(args.staging).resolve()
    skill_dir = Path(args.skill_dir).resolve()

    if staging.exists() and any(staging.iterdir()):
        _eprint(f"ERROR: staging dir {staging} is not empty; refusing to build.")
        return 1
    staging.mkdir(parents=True, exist_ok=True)

    denylist = _load_denylist(skill_dir)
    scrub_rows = _load_scrub(skill_dir)
    scrub_exempt = _load_scrub_exempt(skill_dir)

    tracked = _git_tracked(working)
    denied: list[str] = []
    missing: list[str] = []
    staged = 0

    for rel in tracked:
        if _matches_any(rel, denylist):
            denied.append(rel)
            continue
        src = working / rel
        if not src.exists():
            missing.append(rel)
            continue
        dst = staging / rel
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes(src.read_bytes())
        staged += 1

    # --- deterministic PII scrub over staged text files ----------------------
    scrubbed: dict[str, int] = {}
    exempted: list[str] = []
    scrub_only = [r for r in scrub_rows if r["action"] == "scrub"]
    for path, data in _iter_text_files(staging):
        rel = path.relative_to(staging).as_posix()
        if _matches_any(rel, scrub_exempt):
            exempted.append(rel)
            continue
        text, marker = _decode(data)
        total = 0
        for row in scrub_only:
            text, n = row["rx"].subn(row["replacement"], text)
            total += n
        if total:
            path.write_bytes(_encode(text, marker))
            scrubbed[rel] = total

    report = {
        "working": str(working),
        "staging": str(staging),
        "tracked_total": len(tracked),
        "staged": staged,
        "denylisted_count": len(denied),
        "denylisted": denied,
        "missing_on_disk": missing,
        "scrub_exempt": sorted(set(exempted)),
        "scrubbed_files": scrubbed,
        "scrub_total_substitutions": sum(scrubbed.values()),
    }
    _write_report(args.report, report)
    print(
        f"build: staged {staged} files, withheld {len(denied)} (denylist), "
        f"scrubbed {len(scrubbed)} files "
        f"({report['scrub_total_substitutions']} substitutions), "
        f"exempt {len(report['scrub_exempt'])}."
    )
    if missing:
        _eprint(f"WARNING: {len(missing)} tracked files missing on disk (skipped).")
    return 0


# ----------------------------------------------------------------------------
# English-only artifact gate (loaded from the SHIPPED tree, so detector +
# allowlist can never drift from what actually ships)
# ----------------------------------------------------------------------------
def _load_german_gate(tree: Path):
    """Return (looks_german_fn | None, allowlist_patterns).

    Loads the shipped detector + allowlist from the tree itself. Fail-OPEN
    (None) if the detector is absent, so a privacy scan never crashes on a tree
    that predates the gate — an actual German finding still blocks below.
    """
    import importlib.util

    detect = tree / "scripts" / "ci" / "_german_detect.py"
    allow = tree / "scripts" / "ci" / "german-allowlist.txt"
    if not detect.exists():
        return None, []
    # Never let importing the detector drop a __pycache__/*.pyc into the tree we
    # are about to ship (that .pyc would be copied into the release otherwise).
    prev = sys.dont_write_bytecode
    sys.dont_write_bytecode = True
    try:
        spec = importlib.util.spec_from_file_location("_german_detect_ship", detect)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)  # type: ignore[union-attr]
        fn = mod.looks_german
    except Exception:
        return None, []
    finally:
        sys.dont_write_bytecode = prev
    return fn, (_read_lines(allow) if allow.exists() else [])


def _german_allowlisted(relpath: str, patterns: list[str]) -> bool:
    import fnmatch

    norm = relpath.replace("\\", "/")
    for pat in patterns:
        if fnmatch.fnmatch(norm, pat):
            return True
        if pat.endswith("/*") and (norm == pat[:-2] or norm.startswith(pat[:-1])):
            return True
    return False


# ----------------------------------------------------------------------------
# scan  (fail-closed) — secrets + PII + English-only artifacts
# ----------------------------------------------------------------------------
def cmd_scan(args: argparse.Namespace) -> int:
    tree = Path(args.tree).resolve()
    skill_dir = Path(args.skill_dir).resolve()

    allow = _load_allowlist(skill_dir)
    scrub_rows = _load_scrub(skill_dir)
    scrub_exempt = _load_scrub_exempt(skill_dir)
    secret_rx = {name: re.compile(p) for name, p in SECRET_PATTERNS.items()}
    german_check, german_patterns = _load_german_gate(tree)

    blocking: list[dict] = []
    warnings: list[dict] = []
    suppressed: list[dict] = []

    # forbidden filenames anywhere in the tree
    for p in sorted(tree.rglob("*")):
        if ".git" in p.parts:
            continue
        if not p.is_file():
            continue
        name = p.name
        rel = p.relative_to(tree).as_posix()
        suffix = p.suffix.lower()
        # Unambiguous secret files block hard.
        if name in FORBIDDEN_BASENAMES:
            blocking.append({"kind": "forbidden_file", "path": rel, "detail": name})
            continue
        # Public / encrypted key material is allowed to ship.
        if suffix in (".enc", ".pub") or name.endswith(".pub.pem"):
            continue
        # Other key/db extensions only WARN — actual private-key *material* is
        # caught by the private_key_block content pattern regardless of suffix,
        # so this avoids false-blocking public certs or test fixtures.
        if suffix in FORBIDDEN_SUFFIXES:
            warnings.append({"kind": "sensitive_suffix", "path": rel, "detail": name})

    for path, data in _iter_text_files(tree):
        rel = path.relative_to(tree).as_posix()
        text, _ = _decode(data)
        exempt = _matches_any(rel, scrub_exempt)

        # high-confidence secret shapes
        for name, rx in secret_rx.items():
            for m in rx.finditer(text):
                value = m.group(0)
                if (value, rel) in allow:
                    suppressed.append({"path": rel, "pattern": name, "value": value})
                elif exempt:
                    warnings.append(
                        {"kind": "secret_in_exempt", "path": rel,
                         "pattern": name, "value": value}
                    )
                else:
                    blocking.append(
                        {"kind": "secret", "path": rel, "pattern": name,
                         "value": value}
                    )

        # residual PII (scrub + block-only rows must be 0 in non-exempt files)
        for row in scrub_rows:
            if row["action"] == "warn":
                continue
            for m in row["rx"].finditer(text):
                value = m.group(0)
                if (value, rel) in allow:
                    suppressed.append({"path": rel, "pattern": row["note"], "value": value})
                elif exempt:
                    warnings.append(
                        {"kind": "pii_in_exempt", "path": rel,
                         "note": row["note"], "value": value}
                    )
                else:
                    blocking.append(
                        {"kind": "pii_residual", "path": rel,
                         "note": row["note"], "value": value}
                    )

        # warn-tier PII heuristics
        for row in scrub_rows:
            if row["action"] != "warn":
                continue
            for m in row["rx"].finditer(text):
                if (m.group(0), rel) not in allow:
                    warnings.append(
                        {"kind": "pii_warn", "path": rel,
                         "note": row["note"], "value": m.group(0)}
                    )

        # English-only artifact gate (CLAUDE.md rule 1): no German outside the
        # allowlist may ship. `i18n-allow` inline marks and the path allowlist are
        # the only escapes — same contract as the CI/pre-commit language gate.
        if (
            german_check is not None
            and Path(rel).suffix.lower() in _GERMAN_EXT
            and not _german_allowlisted(rel, german_patterns)
        ):
            for lineno, line in enumerate(text.splitlines(), start=1):
                if "i18n-allow" in line:
                    continue
                if german_check(line):
                    blocking.append(
                        {"kind": "german", "path": rel, "line": lineno,
                         "value": line.strip()[:120]}
                    )

    report = {
        "tree": str(tree),
        "blocking_count": len(blocking),
        "blocking": blocking,
        "warning_count": len(warnings),
        "warnings": warnings,
        "suppressed_count": len(suppressed),
        "suppressed": suppressed,
    }
    _write_report(args.report, report)
    print(
        f"scan: {len(blocking)} BLOCKING, {len(warnings)} warnings, "
        f"{len(suppressed)} allowlisted-suppressed."
    )
    for b in blocking[:50]:
        _eprint(f"  BLOCK [{b['kind']}] {b['path']}: {b.get('value', b.get('detail',''))}")
    # fail-closed: any blocking finding => non-zero exit
    return 2 if blocking else 0


# ----------------------------------------------------------------------------
# reconcile  (make the dist working tree equal the staged tree)
# ----------------------------------------------------------------------------
def cmd_reconcile(args: argparse.Namespace) -> int:
    staging = Path(args.staging).resolve()
    dist = Path(args.dist).resolve()
    skill_dir = Path(args.skill_dir).resolve()

    if not (dist / ".git").exists():
        _eprint(f"ERROR: {dist} is not a git clone.")
        return 1

    keep = _load_keep(skill_dir)
    dist_tracked = _git_tracked(dist)
    staging_files = sorted(
        p.relative_to(staging).as_posix()
        for p in staging.rglob("*")
        if p.is_file()
    )
    staging_set = set(staging_files)

    # Deletions: dist-tracked files that are NOT in the staged tree and are not
    # on the dist-only keep list. Files present in both are overwritten by copy.
    deletes = [
        r for r in dist_tracked
        if r not in staging_set and not _matches_any(r, keep)
    ]
    kept = [r for r in dist_tracked if _matches_any(r, keep)]

    # Mass-deletion guard: a build that went wrong (empty/partial staging) shows
    # up as a huge delete set. Refuse unless explicitly forced.
    threshold = max(50, int(0.10 * max(1, len(dist_tracked))))
    if len(deletes) > threshold and not args.force:
        _eprint(
            f"ERROR: reconcile would delete {len(deletes)} files "
            f"(> guard threshold {threshold}). Refusing — staging looks wrong. "
            f"Re-run with --force only after confirming this is intended."
        )
        _write_report(
            args.report,
            {"aborted": "mass_deletion_guard", "would_delete": len(deletes),
             "threshold": threshold, "dist_tracked": len(dist_tracked)},
        )
        return 3

    for r in deletes:
        (dist / r).unlink(missing_ok=True)

    adds = 0
    mods = 0
    for rel in staging_files:
        dst = dist / rel
        if dst.exists():
            mods += 1
        else:
            adds += 1
        dst.parent.mkdir(parents=True, exist_ok=True)
        dst.write_bytes((staging / rel).read_bytes())

    report = {
        "dist_tracked_before": len(dist_tracked),
        "staged_files": len(staging_files),
        "planned_adds": adds,
        "planned_mods": mods,
        "deletes": deletes,
        "delete_count": len(deletes),
        "kept_dist_only": kept,
    }
    _write_report(args.report, report)
    print(
        f"reconcile: ~{adds} adds, ~{mods} overwrites, {len(deletes)} deletes, "
        f"{len(kept)} dist-only kept. Run `git -C <dist> add -A && git status` "
        f"for the authoritative diff."
    )
    return 0


# ----------------------------------------------------------------------------
# verify  (integrity: nothing got silently dropped; imports resolve)
# ----------------------------------------------------------------------------
_INTERNAL_IMPORT_RX = re.compile(
    r"^([ \t]*)(?:from|import)[ \t]+(jarvis(?:\.[A-Za-z0-9_]+)+)", re.MULTILINE
)


def _module_candidates(dotted: str) -> list[str]:
    """jarvis.state.chat_store -> ['jarvis/state/chat_store.py',
    'jarvis/state/chat_store/__init__.py']."""
    base = dotted.replace(".", "/")
    return [base + ".py", base + "/__init__.py"]


def cmd_verify(args: argparse.Namespace) -> int:
    """Catch the 'ship-broken-release' failure (Risk R2).

    Two checks, both fail-closed:
      1. Completeness — every file in the clean staged tree must actually be
         committed in the dist clone. The classic trap is `git add -A` honouring
         the dist .gitignore and SILENTLY dropping files that are tracked in the
         working repo only because they were force-added there (e.g. jarvis/state
         vs the unanchored `state/` ignore rule). Run AFTER `git add`.
      2. Internal-import resolution — every `import jarvis.x.y` in the staged
         tree must resolve to a file that is actually in the staged tree. Catches
         a module that is imported but untracked (so it never shipped) or
         denylisted-yet-imported.
    """
    staging = Path(args.staging).resolve()
    dist = Path(args.dist).resolve()

    staged = sorted(
        p.relative_to(staging).as_posix() for p in staging.rglob("*") if p.is_file()
    )
    staged_set = set(staged)
    committed = set(_git_tracked(dist))

    missing_from_commit = [s for s in staged if s not in committed]

    # Top-level unresolved imports break at import/boot time => BLOCK.
    # Indented (lazy, inside a function/try) ones only fail when that path runs
    # and are usually pre-existing working-repo dead imports => WARN, so an
    # unrelated latent bug doesn't make the repo permanently un-shippable.
    unresolved_block: list[dict] = []
    unresolved_warn: list[dict] = []
    # 3. Unresolved merge-conflict markers => BLOCK. A stash/merge conflict
    #    frozen by an auto-save commit shipped once (v1.0.6 follow-up: the
    #    delta privacy audit caught '<<<<<<< Updated upstream' blocks in
    #    tool_use_loop.py) — a .py file with markers cannot even import.
    conflict_markers: list[dict] = []
    _conflict_rx = re.compile(r"^(?:<{7}|>{7})(?: |$)", re.MULTILINE)
    for path, data in _iter_text_files(staging):
        rel = path.relative_to(staging).as_posix()
        text, _ = _decode(data)
        if path.suffix == ".py":
            seen: set[tuple[str, bool]] = set()
            for m in _INTERNAL_IMPORT_RX.finditer(text):
                indent, mod = m.group(1), m.group(2)
                top_level = indent == ""
                key = (mod, top_level)
                if key in seen:
                    continue
                seen.add(key)
                if not any(c in staged_set for c in _module_candidates(mod)):
                    row = {"file": rel, "module": mod, "top_level": top_level}
                    (unresolved_block if top_level else unresolved_warn).append(row)
        marker = _conflict_rx.search(text)
        if marker is not None:
            line_no = text.count("\n", 0, marker.start()) + 1
            conflict_markers.append({"file": rel, "line": line_no})

    report = {
        "staged_files": len(staged),
        "committed_files": len(committed),
        "missing_from_commit_count": len(missing_from_commit),
        "missing_from_commit": missing_from_commit,
        "unresolved_toplevel_count": len(unresolved_block),
        "unresolved_toplevel": unresolved_block,
        "unresolved_lazy_count": len(unresolved_warn),
        "unresolved_lazy": unresolved_warn,
        "conflict_marker_count": len(conflict_markers),
        "conflict_markers": conflict_markers,
    }
    _write_report(args.report, report)

    ok = not missing_from_commit and not unresolved_block and not conflict_markers
    print(
        f"verify: {len(missing_from_commit)} staged files MISSING from the commit, "
        f"{len(unresolved_block)} top-level broken imports (BLOCK), "
        f"{len(unresolved_warn)} lazy broken imports (warn), "
        f"{len(conflict_markers)} unresolved merge-conflict markers (BLOCK)."
    )
    for x in missing_from_commit[:30]:
        _eprint(f"  DROPPED (in staging, not committed): {x}")
    for u in unresolved_block[:30]:
        _eprint(f"  BROKEN IMPORT (top-level): {u['file']} -> {u['module']}")
    for u in unresolved_warn[:30]:
        _eprint(f"  warn lazy import: {u['file']} -> {u['module']}")
    for c in conflict_markers[:30]:
        _eprint(f"  CONFLICT MARKER: {c['file']}:{c['line']}")
    return 0 if ok else 2


# ----------------------------------------------------------------------------
# set-version
# ----------------------------------------------------------------------------
def cmd_set_version(args: argparse.Namespace) -> int:
    tree = Path(args.tree).resolve()
    version = args.version.lstrip("v")
    if not re.fullmatch(r"\d+\.\d+\.\d+", version):
        _eprint(f"ERROR: version '{version}' is not X.Y.Z")
        return 1

    targets = {
        tree / "pyproject.toml": (
            re.compile(r'^(version\s*=\s*")[^"]*(")', re.MULTILINE),
            rf'\g<1>{version}\g<2>',
        ),
        tree / "jarvis" / "__init__.py": (
            re.compile(r'^(__version__\s*=\s*")[^"]*(")', re.MULTILINE),
            rf'\g<1>{version}\g<2>',
        ),
        # uv.lock records the project's OWN version; leaving it behind makes
        # `uv lock --check` fail in the dist repo's CI (v1.0.6 forensic: the
        # portable-install-matrix gate went red on the release commit).
        tree / "uv.lock": (
            re.compile(
                r'(\[\[package\]\]\nname = "personal-jarvis"\nversion = ")[^"]*(")'
            ),
            rf'\g<1>{version}\g<2>',
        ),
    }
    changed = []
    for path, (rx, repl) in targets.items():
        if not path.exists():
            _eprint(f"ERROR: expected version file missing: {path}")
            return 1
        text, marker = _decode(path.read_bytes())
        new, n = rx.subn(repl, text, count=1)
        if n != 1:
            _eprint(f"ERROR: could not find version line in {path}")
            return 1
        path.write_bytes(_encode(new, marker))
        changed.append(str(path.relative_to(tree)))

    print(f"set-version: {version} written to {', '.join(changed)}")
    return 0


def _write_report(report_path: str | None, payload: dict) -> None:
    if report_path:
        Path(report_path).write_text(json.dumps(payload, indent=2), "utf-8")


# ----------------------------------------------------------------------------
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="cmd", required=True)

    b = sub.add_parser("build", help="export + denylist + scrub into a staging dir")
    b.add_argument("--working", required=True, help="working repo root")
    b.add_argument("--staging", required=True, help="empty staging dir to fill")
    b.add_argument("--skill-dir", required=True, help="this skill's directory")
    b.add_argument("--report", help="write build report JSON here")
    b.set_defaults(func=cmd_build)

    s = sub.add_parser("scan", help="fail-closed secret/PII scan over a tree")
    s.add_argument("--tree", required=True, help="tree to scan (staging or dist)")
    s.add_argument("--skill-dir", required=True, help="this skill's directory")
    s.add_argument("--report", help="write scan report JSON here")
    s.set_defaults(func=cmd_scan)

    r = sub.add_parser("reconcile", help="make the dist tree equal the staged tree")
    r.add_argument("--staging", required=True, help="clean staged tree")
    r.add_argument("--dist", required=True, help="fresh distribution clone")
    r.add_argument("--skill-dir", required=True, help="this skill's directory")
    r.add_argument("--report", help="write reconcile report JSON here")
    r.add_argument("--force", action="store_true", help="override mass-deletion guard")
    r.set_defaults(func=cmd_reconcile)

    vf = sub.add_parser("verify", help="integrity: staged files committed + imports resolve")
    vf.add_argument("--staging", required=True, help="clean staged tree")
    vf.add_argument("--dist", required=True, help="dist clone (after git add)")
    vf.add_argument("--report", help="write verify report JSON here")
    vf.set_defaults(func=cmd_verify)

    v = sub.add_parser("set-version", help="set version in a staged tree")
    v.add_argument("--tree", required=True, help="staged tree root")
    v.add_argument("--version", required=True, help="X.Y.Z (leading v ok)")
    v.set_defaults(func=cmd_set_version)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
