"""Post-release smoke: published assets, installer checksums and build provenance."""

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.ci import release_assets

ROOT = Path(__file__).resolve().parents[3]
REPO = "example/project"
TAG = "v1.2.3"


def workflows() -> dict[str, dict]:
    return {
        p.name: yaml.safe_load(p.read_text(encoding="utf-8"))
        for p in (ROOT / ".github/workflows").glob("*.yml")
    }


class FakePublishedRelease:
    """In-memory published release; any write command fails the test."""

    def __init__(self, *, attested=0, attestation_ok=True, draft=False):
        self.payloads = {
            "PersonalJarvis-Linux-x86_64.AppImage": b"appimage",
            "personal-jarvis_1.2.3_amd64.deb": b"deb",
            "PersonalJarvis-Setup-x64.exe": b"exe",
        }
        self.manifest = "".join(
            f"{hashlib.sha256(data).hexdigest()}  {name}\n"
            for name, data in sorted(self.payloads.items())
        )
        self.attested = attested
        self.attestation_ok = attestation_ok
        self.draft = draft
        self.commands = []

    def command(self, args, **kwargs):
        self.commands.append(args)
        if args[:3] == ["gh", "release", "download"]:
            name = args[args.index("--pattern") + 1]
            folder = Path(args[args.index("--dir") + 1])
            if name == "installers-SHA256SUMS.txt":
                (folder / name).write_text(self.manifest, encoding="utf-8")
            else:
                (folder / name).write_bytes(self.payloads[name])
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[:2] == ["gh", "api"] and "/attestations/sha256:" in args[2]:
            if not self.attested:
                return subprocess.CompletedProcess(args, 1, "", "gh: Not Found (HTTP 404)")
            return subprocess.CompletedProcess(args, 0, f"{self.attested}\n", "")
        if args[:3] == ["gh", "attestation", "verify"]:
            assert args[args.index("--source-ref") + 1] == f"refs/tags/{TAG}"
            assert args[args.index("--signer-workflow") + 1] == (
                f"{REPO}/.github/workflows/desktop-installers.yml"
            )
            return subprocess.CompletedProcess(args, 0 if self.attestation_ok else 1, "", "")
        raise AssertionError(f"unexpected external command: {args}")

    def install(self, monkeypatch):
        monkeypatch.setattr(release_assets, "run_command", self.command)
        monkeypatch.setattr(
            release_assets,
            "release_info",
            lambda *a: {"databaseId": 1, "tagName": TAG, "isDraft": self.draft},
        )


def test_linux_smoke_checks_appimage_and_deb_against_provenance(monkeypatch, tmp_path):
    fake = FakePublishedRelease(attested=1)
    fake.install(monkeypatch)
    errors = release_assets.platform_errors(REPO, TAG, "linux", tmp_path, require_attestation=True)
    assert errors == []
    verified = [c[3] for c in fake.commands if c[:3] == ["gh", "attestation", "verify"]]
    assert sorted(Path(p).name for p in verified) == [
        "PersonalJarvis-Linux-x86_64.AppImage",
        "personal-jarvis_1.2.3_amd64.deb",
    ]


@pytest.mark.parametrize(
    "defect,require,expected",
    [
        ("tampered", False, "does not match"),
        ("unattested", True, "attestation absent"),
        ("forged", False, "attestation failed"),
    ],
)
def test_installer_smoke_fails_closed(monkeypatch, tmp_path, defect, require, expected):
    fake = FakePublishedRelease(
        attested=0 if defect == "unattested" else 1,
        attestation_ok=defect != "forged",
    )
    if defect == "tampered":
        fake.payloads["PersonalJarvis-Setup-x64.exe"] = b"tampered"
    fake.install(monkeypatch)
    errors = release_assets.platform_errors(
        REPO, TAG, "windows", tmp_path, require_attestation=require
    )
    assert any(expected in error for error in errors), errors


def test_older_unattested_builds_pass_unless_provenance_is_required(monkeypatch, tmp_path):
    fake = FakePublishedRelease()
    fake.install(monkeypatch)
    errors = release_assets.platform_errors(
        REPO, TAG, "windows", tmp_path, require_attestation=False
    )
    assert errors == []


def test_smoke_refuses_a_draft_without_touching_it(monkeypatch):
    fake = FakePublishedRelease(draft=True)
    fake.install(monkeypatch)
    assert release_assets.published_errors(REPO, TAG) == [f"{TAG} is not a published release"]
    assert fake.commands == []


def test_source_archive_digest_is_part_of_the_published_check(monkeypatch):
    digest = "b" * 64
    assets = [
        {"name": name, "size": 1, "state": "uploaded", "digest": f"sha256:{digest}"}
        for name in release_assets.REQUIRED_ASSETS | {"personal-jarvis-src.tar.gz"}
    ]
    manifests = {
        "installers-SHA256SUMS.txt": "".join(
            f"{digest}  {n}\n" for n in sorted(release_assets.INSTALLERS)
        ),
        "checksums.txt": "".join(
            f"{digest}  {n}\n" for n in sorted(release_assets.SIGNED - {"payload-commit.txt"})
        ),
        "source-SHA256SUMS.txt": f"{'c' * 64}  personal-jarvis-src.tar.gz\n",
    }

    def command(args, **kwargs):
        assert args[:3] == ["gh", "release", "download"]
        name = args[args.index("--pattern") + 1]
        (Path(args[args.index("--dir") + 1]) / name).write_text(manifests[name], encoding="utf-8")
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr(release_assets, "run_command", command)
    monkeypatch.setattr(release_assets, "api_pages", lambda *a: assets)
    monkeypatch.setattr(
        release_assets, "release_info", lambda *a: {"databaseId": 1, "tagName": TAG}
    )
    assert release_assets.published_errors(REPO, TAG) == [
        "release asset checksum mismatch: personal-jarvis-src.tar.gz"
    ]
    manifests["source-SHA256SUMS.txt"] = f"{digest}  personal-jarvis-src.tar.gz\n"
    assert release_assets.published_errors(REPO, TAG) == []


def test_native_installers_are_attested_on_tags_before_upload():
    job = workflows()["desktop-installers.yml"]["jobs"]["release"]
    assert job["permissions"]["id-token"] == "write"
    assert job["permissions"]["attestations"] == "write"
    steps = job["steps"]
    attest = next(i for i, s in enumerate(steps) if "attest-build-provenance@" in s.get("uses", ""))
    upload = next(i for i, s in enumerate(steps) if "release_assets.py upload" in s.get("run", ""))
    assert steps[attest]["if"] == "github.ref_type == 'tag'"
    assert steps[attest]["with"]["subject-checksums"] == "installers/installers-SHA256SUMS.txt"
    assert attest < upload


def test_release_smoke_is_read_only_and_follows_every_cut():
    definitions = workflows()
    for name in ("release-smoke.yml", "release-wrapper-smoke.yml"):
        workflow = definitions[name]
        assert workflow["permissions"] == {"contents": "read"}
        for job in workflow["jobs"].values():
            assert set(job.get("permissions", {}).values()) <= {"read"}
    smoke = definitions["release-cut.yml"]["jobs"]["smoke"]
    assert smoke["uses"] == "./.github/workflows/release-smoke.yml"
    assert "publish" in smoke["needs"]
    assert set(smoke["permissions"].values()) == {"read"}
    assert smoke["with"]["require_attestation"] is True
    wrapper = definitions["installer-smoke.yml"]["jobs"]["verify-release"]
    assert wrapper["uses"] == "./.github/workflows/release-wrapper-smoke.yml"
