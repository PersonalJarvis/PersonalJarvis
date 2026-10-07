#!/usr/bin/env python3
"""Keep a release private until its publishers and complete asset set are proven."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.ci.release_admit import api_pages, gate_state, run_command  # noqa: E402

PUBLISHERS = ("release.yml", "desktop-installers.yml", "sign-installer.yml")
PUBLISHER_JOBS = {
    "release.yml": "Publish to PyPI",
    "desktop-installers.yml": "Checksums and GitHub Release",
    "sign-installer.yml": "release",
}
INSTALLERS = {
    "PersonalJarvis-Setup-x64.exe",
    "PersonalJarvis-macOS-arm64.dmg",
    "PersonalJarvis-macOS-x64.dmg",
    "PersonalJarvis-Linux-x86_64.AppImage",
}
SIGNED = {
    "install.sh",
    "install.ps1",
    "installer.py",
    "install-verify.sh",
    "install-verify.ps1",
    "requirements.txt",
    "payload-commit.txt",
}
REQUIRED_ASSETS = (
    INSTALLERS
    | SIGNED
    | {
        name + suffix
        for name in SIGNED
        for suffix in (".sig", ".pem", ".bundle", ".cosign.sig", ".mldsa.sig")
    }
    | {
        "installers-SHA256SUMS.txt",
        "checksums.txt",
        "personal-jarvis.intoto.jsonl",
        "hashes-cross-runner-verified.json",
        "layout-content-anchor.json",
        "offline-ceremony.pub",
        "pq-mldsa65.pub.pem",
    }
)
DESKTOP_ASSETS = INSTALLERS | {"installers-SHA256SUMS.txt"}
SIGNING_ASSETS = REQUIRED_ASSETS - DESKTOP_ASSETS
PLATFORM_INSTALLERS = {
    "linux": {"PersonalJarvis-Linux-x86_64.AppImage"},
    "windows": {"PersonalJarvis-Setup-x64.exe"},
    "macos": {"PersonalJarvis-macOS-arm64.dmg", "PersonalJarvis-macOS-x64.dmg"},
}
DEB_PATTERN = re.compile(r"personal-jarvis_[0-9.]+_amd64\.deb")
DESKTOP_WORKFLOW = ".github/workflows/desktop-installers.yml"


def release_info(repo: str, tag: str) -> dict:
    # gh resolves drafts by tag too; REST releases/tags only resolves published releases.
    proc = run_command(
        [
            "gh",
            "release",
            "view",
            tag,
            "--repo",
            repo,
            "--json",
            "databaseId,tagName,isDraft,isPrerelease",
        ]
    )
    return json.loads(proc.stdout)


def prepare(repo: str, tag: str) -> None:
    try:
        info = release_info(repo, tag)
    except subprocess.CalledProcessError:
        # Concurrent publishers may both observe a missing draft. Re-read after
        # creation (including a conflicting create); every other failure stays fatal.
        run_command(
            [
                "gh",
                "release",
                "create",
                tag,
                "--repo",
                repo,
                "--draft",
                "--verify-tag",
                "--title",
                tag,
                "--notes",
                "Release verification in progress.",
            ],
            check=False,
        )
        info = release_info(repo, tag)
    if info.get("tagName") != tag or not info.get("isDraft"):
        raise ValueError("refusing to replace assets of an already published release")


def staged_assets(directory: Path, profile: str) -> dict[str, Path]:
    """Flatten only the approved asset set; reject missing files and duplicate names."""
    required = DESKTOP_ASSETS if profile == "desktop" else SIGNING_ASSETS
    files = {}
    for path in directory.rglob("*"):
        optional_deb = profile == "desktop" and re.fullmatch(
            r"personal-jarvis_[0-9.]+_amd64\.deb", path.name
        )
        if path.name not in required and not optional_deb:
            continue
        if path.is_symlink() or not path.is_file() or path.stat().st_size == 0:
            raise ValueError(f"invalid staged release asset: {path.name}")
        if path.name in files:
            raise ValueError(f"duplicate staged release asset: {path.name}")
        files[path.name] = path
    if missing := required - files.keys():
        raise ValueError(f"missing staged release assets: {', '.join(sorted(missing))}")
    return files


def upload_draft_files(repo: str, tag: str, files: dict[str, Path]) -> None:
    """Upload each name once; never edit visibility, delete, or replace an asset.

    All existing digests are checked before the first upload. This makes a
    partial retry idempotent and prevents mixing files from different builds.
    A concurrent publisher can only win the same-name create with identical
    bytes; it cannot reopen a public release or clobber an already verified file.
    """
    info = release_info(repo, tag)
    if info.get("tagName") != tag or not info.get("isDraft"):
        raise ValueError("asset uploads require an existing unpublished draft")
    endpoint = f"repos/{repo}/releases/{info['databaseId']}/assets?per_page=100"

    def remote_assets() -> dict[str, dict]:
        assets = api_pages(endpoint, "")
        by_name = {asset["name"]: asset for asset in assets}
        if len(by_name) != len(assets):
            raise ValueError("duplicate release asset names")
        return by_name

    expected = {}
    for name, path in files.items():
        with path.open("rb") as stream:
            expected[name] = (
                "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest(),
                path.stat().st_size,
            )

    def matches(name: str, asset: dict) -> bool:
        digest, size = expected[name]
        return asset.get("state") == "uploaded" and (asset.get("digest"), asset.get("size")) == (
            digest,
            size,
        )

    existing = remote_assets()
    for name in files.keys() & existing.keys():
        if not matches(name, existing[name]):
            raise ValueError(f"refusing to replace different or incomplete asset: {name}")
    for name, path in sorted(files.items()):
        if name in existing:
            continue
        try:
            # No --clobber: the API atomically rejects an already used name.
            run_command(["gh", "release", "upload", tag, "--repo", repo, str(path)])
        except subprocess.CalledProcessError:
            # The request may have completed before a network error, or another
            # job may have uploaded this same file. Accept only identical bytes.
            if not matches(name, remote_assets().get(name, {})):
                raise
    uploaded = remote_assets()
    if any(not matches(name, uploaded.get(name, {})) for name in files):
        raise ValueError("uploaded release assets do not match the staged bytes")
    print(f"[release] verified {len(files)} assets without changing release visibility")


def publisher_runs(runs: list[dict], repo: str, tag: str, sha: str, workflow: str) -> list[dict]:
    return [
        run
        for run in runs
        if run.get("head_sha") == sha
        and run.get("head_branch") == tag
        and (run.get("head_repository") or {}).get("full_name") == repo
        and run.get("path", "").split("@", 1)[0] == f".github/workflows/{workflow}"
        and run.get("event") in {"push", "workflow_dispatch"}
    ]


def publisher_state(runs: list[dict], repo: str, tag: str, sha: str, workflow: str) -> str:
    candidates = publisher_runs(runs, repo, tag, sha, workflow)
    if not candidates:
        return "missing"
    # Even an older duplicate must finish before finalization: it can still upload.
    if any(run.get("status") != "completed" for run in candidates):
        return "pending"
    latest = max(candidates, key=lambda run: (run["id"], run["run_attempt"]))
    return "success" if latest.get("conclusion") == "success" else "failure"


def publisher_evidence(repo: str, tag: str, sha: str, workflow: str) -> str:
    """A green workflow may have skipped publication; require its actual leaf job."""
    runs = api_pages(
        f"repos/{repo}/actions/workflows/{workflow}/runs?head_sha={sha}&per_page=100",
        "workflow_runs",
    )
    state = publisher_state(runs, repo, tag, sha, workflow)
    if state != "success":
        return state
    latest = max(
        publisher_runs(runs, repo, tag, sha, workflow),
        key=lambda run: (run["id"], run["run_attempt"]),
    )
    jobs = api_pages(
        f"repos/{repo}/actions/runs/{latest['id']}/attempts/{latest['run_attempt']}/jobs?per_page=100",
        "jobs",
    )
    matching = [job for job in jobs if job.get("name") == PUBLISHER_JOBS[workflow]]
    if len(matching) != 1:
        return "failure"
    job = matching[0]
    return (
        "success"
        if job.get("status") == "completed" and job.get("conclusion") == "success"
        else "failure"
    )


def dispatch_missing_publishers(repo: str, tag: str, sha: str) -> None:
    """Resume a partial release without repeating successful immutable uploads."""
    for workflow in PUBLISHERS:
        state = publisher_evidence(repo, tag, sha, workflow)
        if state in {"success", "pending"}:
            print(f"[release] {workflow}: {state}; no duplicate dispatch")
            continue
        run_command(["gh", "workflow", "run", workflow, "--repo", repo, "--ref", tag])


def parse_manifest(text: str) -> tuple[dict[str, str], list[str]]:
    """Read ``sha256sum`` output into ``{name: hex digest}`` plus format errors."""
    digests: dict[str, str] = {}
    errors = []
    for line in text.splitlines():
        match = re.fullmatch(r"([0-9a-f]{64})  ([A-Za-z0-9_.-]+)", line)
        if not match:
            errors.append("invalid checksum record")
            continue
        digest, name = match.groups()
        if name in digests:
            errors.append(f"duplicate checksum: {name}")
        digests[name] = digest
    return digests, errors


def asset_errors(assets: list[dict], manifests: dict[str, str]) -> list[str]:
    """Validate required nonempty assets and manifests against GitHub's stored digest."""
    by_name = {asset["name"]: asset for asset in assets}
    errors = []
    if len(by_name) != len(assets):
        errors.append("duplicate release asset names")
    for name in sorted(REQUIRED_ASSETS):
        asset = by_name.get(name, {})
        if asset.get("size", 0) <= 0 or asset.get("state") != "uploaded":
            errors.append(f"missing or incomplete asset: {name}")
    for manifest, required in (
        ("installers-SHA256SUMS.txt", INSTALLERS),
        ("checksums.txt", SIGNED - {"payload-commit.txt"}),
    ):
        digests, problems = parse_manifest(manifests.get(manifest, ""))
        errors.extend(f"{problem} in {manifest}" for problem in problems)
        for name, digest in digests.items():
            if by_name.get(name, {}).get("digest") != f"sha256:{digest}":
                errors.append(f"release asset checksum mismatch: {name}")
        if required - digests.keys():
            errors.append(f"incomplete manifest: {manifest}")
    return errors


def finalize(repo: str, tag: str, sha: str, wait_minutes: int) -> int:
    deadline = time.monotonic() + wait_minutes * 60
    while True:
        states = {workflow: publisher_evidence(repo, tag, sha, workflow) for workflow in PUBLISHERS}
        print(f"[release] publishers: {states}", flush=True)
        if "failure" in states.values():
            raise ValueError("a release publisher failed; the release remains a draft")
        if set(states.values()) == {"success"}:
            break
        if time.monotonic() >= deadline:
            print("[release] publishers are incomplete; leaving the release as a draft")
            return 1 if wait_minutes else 0
        time.sleep(min(30, max(0, deadline - time.monotonic())))
    if gate_state(repo, sha, require_qualification=True, tag=tag) != "success":
        raise ValueError("the exact release commit lacks successful qualified CI")
    info = release_info(repo, tag)
    if info.get("tagName") != tag or info.get("isPrerelease"):
        raise ValueError("release identity or prerelease state is unexpected")
    if not info.get("isDraft"):
        print("[release] already published; no assets changed")
        return 0
    # REST asset metadata includes upload state and a server-computed SHA-256.
    assets = api_pages(f"repos/{repo}/releases/{info['databaseId']}/assets?per_page=100", "")
    with tempfile.TemporaryDirectory(prefix="jarvis-release-") as raw:
        folder = Path(raw)
        manifests = {}
        for name in ("checksums.txt", "installers-SHA256SUMS.txt", "payload-commit.txt"):
            run_command(
                [
                    "gh",
                    "release",
                    "download",
                    tag,
                    "--repo",
                    repo,
                    "--pattern",
                    name,
                    "--dir",
                    str(folder),
                ]
            )
            manifests[name] = (folder / name).read_text(encoding="utf-8")
        errors = asset_errors(assets, manifests)
        if manifests["payload-commit.txt"].strip() != sha:
            errors.append("signed payload commit does not match the release commit")
        if errors:
            raise ValueError("; ".join(errors))
        archive = folder / "personal-jarvis-src.tar.gz"
        run_command(
            [
                "git",
                "archive",
                "--format=tar.gz",
                "--prefix=personal-jarvis/",
                "-o",
                str(archive),
                f"refs/tags/{tag}",
            ]
        )
        checksum = folder / "source-SHA256SUMS.txt"
        with archive.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        checksum.write_text(f"{digest}  {archive.name}\n", encoding="utf-8")
        changelog = run_command(["git", "show", f"refs/tags/{tag}:CHANGELOG.md"]).stdout
        from scripts.ci.cut_release import section_notes

        notes = folder / "notes.md"
        notes.write_text(section_notes(changelog, tag[1:]), encoding="utf-8")
        run_command(
            [
                "gh",
                "release",
                "upload",
                tag,
                "--repo",
                repo,
                str(archive),
                str(checksum),
                "--clobber",
            ]
        )
        uploaded = api_pages(f"repos/{repo}/releases/{info['databaseId']}/assets?per_page=100", "")
        uploaded_by_name = {asset["name"]: asset for asset in uploaded}
        for file in (archive, checksum):
            with file.open("rb") as stream:
                expected = "sha256:" + hashlib.file_digest(stream, "sha256").hexdigest()
            if uploaded_by_name.get(file.name, {}).get("digest") != expected:
                raise ValueError(f"uploaded source digest mismatch: {file.name}")
        run_command(
            [
                "gh",
                "release",
                "edit",
                tag,
                "--repo",
                repo,
                "--notes-file",
                str(notes),
                "--draft=false",
            ]
        )
    print(f"[release] published {tag} with complete verified assets")
    return 0


def _download(repo: str, tag: str, name: str, folder: Path) -> Path:
    run_command(
        ["gh", "release", "download", tag, "--repo", repo, "--pattern", name, "--dir", str(folder)]
    )
    return folder / name


def published_errors(repo: str, tag: str) -> list[str]:
    """Re-check a PUBLISHED release the way finalization checked its draft.

    Runs after publication, so it proves what users can download today: the
    complete asset set, both signed manifests and the source checksum, all
    against GitHub's server-side digests. It never writes to the release.
    """
    info = release_info(repo, tag)
    if info.get("tagName") != tag or info.get("isDraft"):
        return [f"{tag} is not a published release"]
    assets = api_pages(f"repos/{repo}/releases/{info['databaseId']}/assets?per_page=100", "")
    with tempfile.TemporaryDirectory(prefix="jarvis-release-smoke-") as raw:
        folder = Path(raw)
        manifests = {
            name: _download(repo, tag, name, folder).read_text(encoding="utf-8")
            for name in ("checksums.txt", "installers-SHA256SUMS.txt", "source-SHA256SUMS.txt")
        }
    errors = asset_errors(assets, manifests)
    by_name = {asset["name"]: asset for asset in assets}
    source, problems = parse_manifest(manifests["source-SHA256SUMS.txt"])
    errors.extend(f"{problem} in source-SHA256SUMS.txt" for problem in problems)
    archive = "personal-jarvis-src.tar.gz"
    if archive not in source:
        errors.append("incomplete manifest: source-SHA256SUMS.txt")
    elif by_name.get(archive, {}).get("digest") != f"sha256:{source[archive]}":
        errors.append(f"release asset checksum mismatch: {archive}")
    return errors


def platform_installers(platform: str, manifest: dict[str, str]) -> list[str]:
    """The installer files a user on ``platform`` downloads from this release."""
    names = sorted(PLATFORM_INSTALLERS[platform])
    if platform == "linux":
        names += sorted(name for name in manifest if DEB_PATTERN.fullmatch(name))
    return names


def attestation_state(repo: str, tag: str, path: Path, digest: str) -> str:
    """``verified``, ``absent`` or ``failed`` for one downloaded installer.

    Only the tag build of desktop-installers.yml may vouch for a native
    installer, so the verification pins both the signing workflow and the ref.
    """
    endpoint = f"repos/{repo}/attestations/sha256:{digest}"
    lookup = run_command(["gh", "api", endpoint, "--jq", ".attestations | length"], check=False)
    if lookup.returncode != 0:
        if "HTTP 404" in lookup.stderr:
            return "absent"
        print(f"::error::attestation lookup failed for {path.name}: {lookup.stderr.strip()}")
        return "failed"
    if lookup.stdout.strip() in {"", "0"}:
        return "absent"
    verify = run_command(
        [
            "gh",
            "attestation",
            "verify",
            str(path),
            "--repo",
            repo,
            "--signer-workflow",
            f"{repo}/{DESKTOP_WORKFLOW}",
            "--source-ref",
            f"refs/tags/{tag}",
            "--deny-self-hosted-runners",
        ],
        check=False,
    )
    if verify.returncode != 0:
        print(verify.stdout + verify.stderr)
        return "failed"
    return "verified"


def platform_errors(
    repo: str, tag: str, platform: str, folder: Path, *, require_attestation: bool
) -> list[str]:
    """Download this platform's installers and prove them like a careful user would."""
    folder.mkdir(parents=True, exist_ok=True)
    manifest, errors = parse_manifest(
        _download(repo, tag, "installers-SHA256SUMS.txt", folder).read_text(encoding="utf-8")
    )
    for name in platform_installers(platform, manifest):
        if name not in manifest:
            errors.append(f"installers-SHA256SUMS.txt does not list {name}")
            continue
        path = _download(repo, tag, name, folder)
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        if digest != manifest[name]:
            errors.append(f"downloaded {name} does not match installers-SHA256SUMS.txt")
            continue
        state = attestation_state(repo, tag, path, digest)
        if state == "failed" or (state == "absent" and require_attestation):
            errors.append(f"build provenance attestation {state}: {name}")
        elif state == "absent":
            print(f"::notice::{name} has no build provenance attestation (older build)")
        print(f"[release] {name}: checksum OK, attestation {state}")
    return errors


def verify(repo: str, tag: str, platform: str | None, folder: Path | None, attest: bool) -> int:
    errors = published_errors(repo, tag)
    if platform:
        directory = folder or Path(tempfile.mkdtemp(prefix="jarvis-release-smoke-"))
        errors += platform_errors(repo, tag, platform, directory, require_attestation=attest)
    for error in errors:
        print(f"::error::{error}")
    if errors:
        return 1
    scope = f" and {platform} installers" if platform else ""
    print(f"[release] {tag}: published assets{scope} verified")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("prepare", "upload", "dispatch", "finalize", "verify"))
    parser.add_argument("--repo", required=True)
    parser.add_argument("--tag", required=True)
    parser.add_argument("--sha")
    parser.add_argument("--wait-minutes", type=int, default=0)
    parser.add_argument("--directory", type=Path)
    parser.add_argument("--profile", choices=("desktop", "signing"))
    parser.add_argument(
        "--platform",
        choices=sorted(PLATFORM_INSTALLERS),
        help="verify: also download and check this platform's installers",
    )
    parser.add_argument(
        "--require-attestation",
        action="store_true",
        help="verify: a downloaded installer without build provenance fails",
    )
    args = parser.parse_args(argv)
    if not re.fullmatch(r"v\d+\.\d+\.\d+", args.tag):
        parser.error("a stable vX.Y.Z tag is required")
    if args.command == "upload" and (args.directory is None or args.profile is None):
        parser.error("upload requires --directory and --profile")
    try:
        if args.command == "prepare":
            prepare(args.repo, args.tag)
            return 0
        if args.command == "upload":
            upload_draft_files(args.repo, args.tag, staged_assets(args.directory, args.profile))
            return 0
        if args.command == "verify":
            # Read-only and tag-free: a smoke runner needs no git history.
            return verify(
                args.repo, args.tag, args.platform, args.directory, args.require_attestation
            )
        sha = run_command(["git", "rev-parse", f"refs/tags/{args.tag}^{{commit}}"]).stdout.strip()
        if args.sha and sha != args.sha:
            raise ValueError("release tag moved away from the publisher's commit")
        run_command(["git", "merge-base", "--is-ancestor", sha, "origin/main"])
        if args.command == "dispatch":
            dispatch_missing_publishers(args.repo, args.tag, sha)
            return 0
        return finalize(args.repo, args.tag, sha, args.wait_minutes)
    except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError) as exc:
        print(f"::error::Release verification failed: {type(exc).__name__}: {exc}")
        if detail := (getattr(exc, "stderr", None) or "").strip():
            print(detail)
        return 1


if __name__ == "__main__":
    sys.exit(main())
