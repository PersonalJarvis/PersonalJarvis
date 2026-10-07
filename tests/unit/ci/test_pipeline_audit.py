"""Regression checks for CI evidence provenance and staged release publication."""

from __future__ import annotations

import copy
import hashlib
import json
import subprocess
from pathlib import Path

import pytest
import yaml

from scripts.ci import (
    check_workflow_policy,
    classify_changes,
    cut_release,
    ratchet_tests,
    release_admit,
    release_assets,
    required_results,
)

ROOT = Path(__file__).resolve().parents[3]
REPO = "example/project"
SHA = "a" * 40
TAG = "v1.2.3"


def run_record(**overrides):
    result = {
        "id": 10,
        "run_attempt": 1,
        "head_sha": SHA,
        "head_repository": {"full_name": REPO},
        "head_branch": TAG,
        "event": "workflow_dispatch",
        "path": ".github/workflows/ci.yml",
        "status": "completed",
        "conclusion": "success",
    }
    return result | overrides


class FakeActions:
    def __init__(self, runs, jobs=None):
        self.runs = runs
        self.jobs = (
            jobs
            if jobs is not None
            else [
                {"name": "CI gate", "conclusion": "success"},
                {"name": "release qualification", "conclusion": "success"},
            ]
        )
        self.calls = []

    def pages(self, endpoint, key):
        self.calls.append(endpoint)
        return self.jobs if key == "jobs" else self.runs


@pytest.mark.parametrize(
    "status,conclusion,expected",
    [
        ("completed", "failure", "failure"),
        ("completed", "cancelled", "failure"),
        ("in_progress", None, "pending"),
        ("completed", "success", "success"),
    ],
)
def test_newest_ci_run_overrides_an_older_green(monkeypatch, status, conclusion, expected):
    fake = FakeActions([run_record(), run_record(id=11, status=status, conclusion=conclusion)])
    monkeypatch.setattr(release_admit, "api_pages", fake.pages)
    assert release_admit.gate_state(REPO, SHA, require_qualification=True) == expected


@pytest.mark.parametrize(
    "changed",
    [
        {"head_sha": "b" * 40},
        {"head_repository": {"full_name": "fork/project"}},
        {"head_branch": "feature"},
        {"event": "pull_request"},
        {"path": ".github/workflows/pretend-ci.yml"},
    ],
)
def test_named_checks_without_ci_workflow_provenance_do_not_admit(monkeypatch, changed):
    fake = FakeActions([run_record(**changed)])
    monkeypatch.setattr(release_admit, "api_pages", fake.pages)
    assert release_admit.gate_state(REPO, SHA) == "missing"


def test_only_the_latest_attempts_jobs_count(monkeypatch):
    fake = FakeActions([run_record(run_attempt=3)])
    monkeypatch.setattr(release_admit, "api_pages", fake.pages)
    assert release_admit.gate_state(REPO, SHA, require_qualification=True) == "success"
    assert "/attempts/3/jobs?" in fake.calls[-1]


def test_tag_qualification_uses_the_requested_tag_not_a_later_main_run(monkeypatch):
    fake = FakeActions([run_record(id=10), run_record(id=11, head_branch="main")])
    monkeypatch.setattr(release_admit, "api_pages", fake.pages)
    assert release_admit.gate_state(REPO, SHA, require_qualification=True, tag=TAG) == "success"
    assert "/runs/10/attempts/" in fake.calls[-1]


def test_other_tag_ci_cannot_qualify_this_release(monkeypatch):
    fake = FakeActions([run_record(head_branch="v9.9.9")])
    monkeypatch.setattr(release_admit, "api_pages", fake.pages)
    assert release_admit.gate_state(REPO, SHA, require_qualification=True, tag=TAG) == "missing"


@pytest.mark.parametrize(
    "jobs",
    [
        [],
        [{"name": "CI gate", "conclusion": "success"}],
        [
            {"name": "CI gate", "conclusion": "success"},
            {"name": "release qualification", "conclusion": "skipped"},
        ],
    ],
)
def test_release_requires_qualification_in_same_attempt(monkeypatch, jobs):
    fake = FakeActions([run_record()], jobs)
    monkeypatch.setattr(release_admit, "api_pages", fake.pages)
    assert release_admit.gate_state(REPO, SHA, require_qualification=True) == "failure"


def test_api_lookup_failure_cannot_reuse_stale_green(monkeypatch):
    def unavailable(*args):
        raise subprocess.CalledProcessError(1, ["gh", "api"])

    monkeypatch.setattr(release_admit, "api_pages", unavailable)
    assert release_admit.gate_state(REPO, SHA) == "failure"


@pytest.mark.parametrize("tag_sha,fetch_fails", [("b" * 40, False), (SHA, True)])
def test_commit_identity_and_fresh_main_are_required(monkeypatch, tag_sha, fetch_fails):
    def command(args, **kwargs):
        if args[1] == "fetch" and fetch_fails:
            raise subprocess.CalledProcessError(1, args)
        output = tag_sha if "refs/tags/" in args[-1] else SHA
        return subprocess.CompletedProcess(args, 0, output + "\n", "")

    monkeypatch.setattr(release_admit, "run_command", command)
    assert release_admit.check_commit(TAG, SHA)


def pipeline_needs():
    flags = {name: "true" for name in classify_changes.LANES}
    flags.update(full="true", macos="true", release="true")
    needs = {name: {"result": "success"} for name in required_results.expected_jobs(flags)}
    needs["detect"]["outputs"] = flags
    return needs


@pytest.mark.parametrize(
    "job", ["gates", "zizmor", "python-fast", "tests-macos", "frontend", "release-qualification"]
)
@pytest.mark.parametrize("missing", [False, True])
def test_expected_lane_cannot_be_skipped_or_omitted(job, missing):
    needs = pipeline_needs()
    assert required_results.evaluate(needs, pipeline=True)["ok"]
    if missing:
        del needs[job]
    else:
        needs[job]["result"] = "skipped"
    assert not required_results.evaluate(needs, pipeline=True)["ok"]


def test_invalid_detection_does_not_turn_checks_off():
    needs = pipeline_needs()
    del needs["detect"]["outputs"]["python"]
    assert not required_results.evaluate(needs, pipeline=True)["ok"]


def test_unaffected_lanes_can_still_skip():
    flags = {name: "false" for name in classify_changes.LANES}
    flags.update(full="false", macos="false", release="false")
    needs = {
        "detect": {"result": "success", "outputs": flags},
        "gates": {"result": "success"},
        "zizmor": {"result": "success"},
        "tests-macos": {"result": "skipped"},
    }
    assert required_results.evaluate(needs, pipeline=True)["ok"]


def test_dependency_review_runs_only_on_pull_requests_and_may_skip_elsewhere():
    ci = workflows()["ci.yml"]["jobs"]
    assert ci["dependency-review"]["if"] == "github.event_name == 'pull_request'"
    assert "if" not in ci["zizmor"]  # the workflow audit runs on every event
    needs = pipeline_needs()
    needs["dependency-review"] = {"result": "skipped"}
    assert required_results.evaluate(needs, pipeline=True, strict=True)["ok"]
    needs["dependency-review"] = {"result": "failure"}
    assert not required_results.evaluate(needs, pipeline=True)["ok"]


def test_merge_queue_runs_the_whole_suite_and_is_never_cancelled():
    ci = workflows()["ci.yml"]
    triggers = ci.get("on", ci.get(True))
    assert triggers["merge_group"] == {"types": ["checks_requested"]}
    assert "merge_group" in ci["concurrency"]["group"]
    assert "merge_group" not in ci["concurrency"]["cancel-in-progress"]
    mode = next(s for s in ci["jobs"]["detect"]["steps"] if s.get("id") == "mode")
    assert "push|merge_group) tests_full=true" in mode["run"]
    assert "merge_group.base_sha" in mode["env"]["PR_BASE"]
    assert ci["jobs"]["gate"]["if"] == "${{ !cancelled() }}"


@pytest.mark.parametrize("payload", [{}, {"failed_ids": [], "counts": {}, "files": 0}])
def test_empty_test_evidence_fails_even_without_a_baseline(tmp_path, payload):
    report = tmp_path / "report.json"
    report.write_text(json.dumps(payload), encoding="utf-8")
    assert (
        ratchet_tests.main(["check", "--baseline", str(tmp_path / "missing.json"), str(report)])
        == 1
    )


def test_missing_baseline_accepts_a_real_green_report(tmp_path):
    report = tmp_path / "report.json"
    report.write_text(
        json.dumps({"failed_ids": [], "counts": {"passed": 2, "tests": 2}, "files": 1})
    )
    assert (
        ratchet_tests.main(["check", "--baseline", str(tmp_path / "missing.json"), str(report)])
        == 0
    )


def partition():
    files = ["tests/a.py", "tests/b.py"]
    digest = hashlib.sha256("\n".join(files).encode()).hexdigest()
    return [
        {"shard": f"{i + 1}/2", "file_paths": [file], "suite_files": 2, "suite_digest": digest}
        for i, file in enumerate(files)
    ]


def test_shard_evidence_proves_complete_partition():
    reports = partition()
    assert ratchet_tests.partition_errors(reports, 2) == []
    assert ratchet_tests.partition_errors(reports[:1], 2)
    reports[1]["file_paths"] = reports[0]["file_paths"]
    assert ratchet_tests.partition_errors(reports, 2)
    reports[1]["file_paths"] = []
    assert ratchet_tests.partition_errors(reports, 2)


def asset_fixture():
    digest = "b" * 64
    assets = [
        {"name": name, "size": 1, "state": "uploaded", "digest": f"sha256:{digest}"}
        for name in release_assets.REQUIRED_ASSETS
    ]
    manifests = {
        "installers-SHA256SUMS.txt": "".join(
            f"{digest}  {n}\n" for n in sorted(release_assets.INSTALLERS)
        ),
        "checksums.txt": "".join(
            f"{digest}  {n}\n" for n in sorted(release_assets.SIGNED - {"payload-commit.txt"})
        ),
    }
    return assets, manifests


@pytest.mark.parametrize(
    "defect", ["missing", "empty", "digest", "duplicate", "traversal", "manifest"]
)
def test_release_assets_must_be_complete_and_match_manifest(defect):
    assets, manifests = asset_fixture()
    assert release_assets.asset_errors(assets, manifests) == []
    installer = next(a for a in assets if a["name"] in release_assets.INSTALLERS)
    if defect == "missing":
        assets.remove(installer)
    elif defect == "empty":
        installer["size"] = 0
    elif defect == "digest":
        installer["digest"] = "sha256:" + "c" * 64
    elif defect == "duplicate":
        assets.append(installer)
    elif defect == "traversal":
        manifests["checksums.txt"] += "b" * 64 + "  ../outside\n"
    else:
        manifests["checksums.txt"] = ""
    assert release_assets.asset_errors(assets, manifests)


def test_in_progress_duplicate_publisher_blocks_finalization():
    workflow = "desktop-installers.yml"
    path = f".github/workflows/{workflow}"
    runs = [run_record(path=path, status="in_progress"), run_record(path=path, id=11)]
    assert release_assets.publisher_state(runs, REPO, TAG, SHA, workflow) == "pending"
    runs[0].update(status="completed", conclusion="failure")
    assert release_assets.publisher_state(runs, REPO, TAG, SHA, workflow) == "success"


@pytest.mark.parametrize(
    "workflow,job_name",
    [
        ("release.yml", "Publish to PyPI"),
        ("desktop-installers.yml", "Checksums and GitHub Release"),
        ("sign-installer.yml", "release"),
    ],
)
@pytest.mark.parametrize("conclusion", ["success", "skipped", "failure", None])
def test_green_publisher_workflow_requires_the_actual_publish_job(
    monkeypatch,
    workflow,
    job_name,
    conclusion,
):
    calls = []

    def pages(endpoint, key):
        calls.append(endpoint)
        if key == "jobs":
            return [{"name": job_name, "status": "completed", "conclusion": conclusion}]
        return [run_record(path=f".github/workflows/{workflow}", run_attempt=2)]

    monkeypatch.setattr(release_assets, "api_pages", pages)
    expected = "success" if conclusion == "success" else "failure"
    assert release_assets.publisher_evidence(REPO, TAG, SHA, workflow) == expected
    assert "/attempts/2/jobs?" in calls[-1]


def test_missing_publish_job_fails_closed(monkeypatch):
    def pages(endpoint, key):
        return [] if key == "jobs" else [run_record(path=".github/workflows/release.yml")]

    monkeypatch.setattr(release_assets, "api_pages", pages)
    assert release_assets.publisher_evidence(REPO, TAG, SHA, "release.yml") == "failure"


def test_resuming_release_does_not_repeat_successful_or_running_publishers(monkeypatch):
    commands = []

    def pages(endpoint, key):
        if key == "jobs":
            return [{"name": "Publish to PyPI", "status": "completed", "conclusion": "success"}]
        workflow = endpoint.split("/workflows/")[1].split("/")[0]
        status = "in_progress" if workflow == "desktop-installers.yml" else "completed"
        conclusion = "failure" if workflow == "sign-installer.yml" else "success"
        return [
            run_record(path=f".github/workflows/{workflow}", status=status, conclusion=conclusion)
        ]

    monkeypatch.setattr(release_assets, "api_pages", pages)
    monkeypatch.setattr(release_assets, "run_command", lambda args: commands.append(args))
    release_assets.dispatch_missing_publishers(REPO, TAG, SHA)
    assert commands == [
        ["gh", "workflow", "run", "sign-installer.yml", "--repo", REPO, "--ref", TAG]
    ]


def test_published_release_cannot_be_reopened_or_overwritten(monkeypatch):
    monkeypatch.setattr(
        release_assets, "release_info", lambda *a: {"tagName": TAG, "isDraft": False}
    )
    with pytest.raises(ValueError, match="already published"):
        release_assets.prepare(REPO, TAG)


@pytest.mark.parametrize("conclusion", [None, "failure"])
def test_incomplete_publishers_never_write_a_release(monkeypatch, conclusion):
    def pages(endpoint, key):
        workflow = endpoint.split("/workflows/")[1].split("/")[0]
        return [
            run_record(
                path=f".github/workflows/{workflow}",
                conclusion=conclusion,
                status="completed" if conclusion else "in_progress",
            )
        ]

    monkeypatch.setattr(release_assets, "api_pages", pages)
    if conclusion:
        with pytest.raises(ValueError, match="publisher failed"):
            release_assets.finalize(REPO, TAG, SHA, 0)
    else:
        assert release_assets.finalize(REPO, TAG, SHA, 0) == 0


def workflows():
    return {
        p.name: yaml.safe_load(p.read_text(encoding="utf-8"))
        for p in (ROOT / ".github/workflows").glob("*.yml")
    }


def test_workflow_security_policy_and_mutation_regressions():
    clean = workflows()
    assert check_workflow_policy.audit(clean) == []
    for mutate in (
        lambda w: w["release.yml"]["jobs"]["publish"].pop("if"),
        lambda w: w["ci.yml"]["jobs"]["gate"]["needs"].remove("frontend"),
        lambda w: w["sign-installer.yml"]["permissions"].update({"id-token": "write"}),
        lambda w: w["sign-installer.yml"]["jobs"]["release"]["needs"].remove("provenance"),
        lambda w: w["stargazer-map.yml"]["jobs"]["verify"]["steps"][0].update(
            {"uses": "actions/checkout@v4"}
        ),
    ):
        changed = copy.deepcopy(clean)
        mutate(changed)
        assert check_workflow_policy.audit(changed)


def test_all_shards_consume_the_same_detect_output_including_partial_reruns():
    ci = workflows()["ci.yml"]["jobs"]
    for name in ("tests-linux", "tests-windows", "tests-macos"):
        restores = [
            s for s in ci[name]["steps"] if s.get("uses", "").startswith("actions/cache/restore@")
        ]
        assert not restores
        snapshot = next(
            s for s in ci[name]["steps"] if s.get("name") == "Download the shared duration snapshot"
        )
        assert snapshot["with"]["name"] == "${{ needs.detect.outputs.duration_snapshot }}"


def test_release_ci_targets_tag_with_all_platforms():
    steps = workflows()["release-cut.yml"]["jobs"]["cut"]["steps"]
    dispatch = next(s["run"] for s in steps if s.get("name") == "Dispatch the publishing workflows on the tag")
    assert 'ci.yml --ref "v$V" -f full=true -f include_macos=true' in dispatch


def test_release_tag_ci_is_excluded_from_branch_cancellation():
    concurrency = workflows()["ci.yml"]["concurrency"]
    assert "github.ref_type == 'branch'" in concurrency["cancel-in-progress"]
    assert "github.ref_type == 'branch'" in concurrency["group"]


def test_release_cut_lands_through_protection_and_tags_the_merge_commit():
    cut = workflows()["release-cut.yml"]["jobs"]["cut"]
    commands = "\n".join(step.get("run", "") for step in cut["steps"])
    assert "git push origin HEAD:main" not in commands
    assert 'gh pr merge "$candidate" --merge --match-head-commit "$sha"' in commands
    assert 'git checkout --detach "$merged"' in commands
    assert '[ "$TOKEN_IS_BOT" = "true" ] || [ "$TAG_PUSHED" != "true" ]' in commands


def test_release_cut_updates_only_the_root_lock_version(tmp_path):
    (tmp_path / "jarvis").mkdir()
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "personal-jarvis"\nversion = "1.0.0"\n'
    )
    (tmp_path / "jarvis/__init__.py").write_text('__version__ = "1.0.0"\n')
    (tmp_path / "CHANGELOG.md").write_text("# Changes\n\n## [Unreleased]\n\n- Added\n")
    (tmp_path / "uv.lock").write_text(
        '[[package]]\nname = "personal-jarvis"\nversion = "1.0.0"\n'
        'source = { editable = "." }\n\n[[package]]\nname = "other"\nversion = "1.0.0"\n'
    )
    cut_release.apply("1.0.1", "Notes", "2026-10-02", tmp_path)
    lock = (tmp_path / "uv.lock").read_text()
    assert 'name = "personal-jarvis"\nversion = "1.0.1"' in lock
    assert 'name = "other"\nversion = "1.0.0"' in lock


class FakeRelease:
    """Exercise the full coordinator with in-memory GitHub state and local files."""

    def __init__(self, *, corrupt_source=False, wrong_payload=False):
        self.assets, self.manifests = asset_fixture()
        self.manifests["payload-commit.txt"] = ("c" * 40 if wrong_payload else SHA) + "\n"
        self.corrupt_source = corrupt_source
        self.commands = []

    def pages(self, endpoint, key):
        if "/assets?" in endpoint:
            return self.assets
        if key == "jobs":
            return [
                {"name": name, "status": "completed", "conclusion": "success"}
                for name in release_assets.PUBLISHER_JOBS.values()
            ]
        workflow = endpoint.split("/workflows/")[1].split("/")[0]
        return [run_record(path=f".github/workflows/{workflow}")]

    def command(self, args, **kwargs):
        self.commands.append(args)
        output = ""
        if args[:3] == ["gh", "release", "download"]:
            name = args[args.index("--pattern") + 1]
            folder = Path(args[args.index("--dir") + 1])
            (folder / name).write_text(self.manifests[name], encoding="utf-8")
        elif args[:2] == ["git", "archive"]:
            assert args[-1] == f"refs/tags/{TAG}"
            Path(args[args.index("-o") + 1]).write_bytes(b"source archive fixture")
        elif args[:2] == ["git", "show"]:
            output = "## [1.2.3]\n\nRelease notes.\n"
        elif args[:3] == ["gh", "release", "upload"]:
            for file in map(Path, args[6:8]):
                digest = hashlib.sha256(file.read_bytes()).hexdigest()
                self.assets.append({"name": file.name, "digest": f"sha256:{digest}"})
            if self.corrupt_source:
                self.assets[-1]["digest"] = "sha256:" + "0" * 64
        elif args[:3] == ["gh", "release", "edit"]:
            assert "--draft=false" in args
            assert Path(args[args.index("--notes-file") + 1]).read_text() == "Release notes."
        else:
            raise AssertionError(f"unexpected external command: {args}")
        return subprocess.CompletedProcess(args, 0, output, "")

    def install(self, monkeypatch):
        monkeypatch.setattr(release_assets, "api_pages", self.pages)
        monkeypatch.setattr(release_assets, "run_command", self.command)
        monkeypatch.setattr(release_assets, "gate_state", lambda *a, **k: "success")
        monkeypatch.setattr(
            release_assets,
            "release_info",
            lambda *a: {
                "databaseId": 123,
                "tagName": TAG,
                "isDraft": True,
                "isPrerelease": False,
            },
        )


def test_finalization_uploads_and_verifies_source_before_publication(monkeypatch):
    fake = FakeRelease()
    fake.install(monkeypatch)
    assert release_assets.finalize(REPO, TAG, SHA, 0) == 0
    assert fake.commands[-1][:3] == ["gh", "release", "edit"]
    assert sum(command[:3] == ["gh", "release", "upload"] for command in fake.commands) == 1


@pytest.mark.parametrize("defect", ["corrupt_source", "wrong_payload"])
def test_corrupt_release_never_becomes_public(monkeypatch, defect):
    fake = FakeRelease(**{defect: True})
    fake.install(monkeypatch)
    with pytest.raises(ValueError, match="digest mismatch|payload commit"):
        release_assets.finalize(REPO, TAG, SHA, 0)
    assert not any(command[:3] == ["gh", "release", "edit"] for command in fake.commands)


def test_skipped_pypi_publication_cannot_finalize_a_release(monkeypatch):
    fake = FakeRelease()
    fake.install(monkeypatch)

    def pages(endpoint, key):
        result = fake.pages(endpoint, key)
        if key == "jobs":
            for job in result:
                if job["name"] == "Publish to PyPI":
                    job["conclusion"] = "skipped"
        return result

    monkeypatch.setattr(release_assets, "api_pages", pages)
    with pytest.raises(ValueError, match="publisher failed"):
        release_assets.finalize(REPO, TAG, SHA, 0)
    assert fake.commands == []


def test_publisher_evidence_job_names_match_the_actual_workflows():
    definitions = workflows()
    for workflow, expected_name in release_assets.PUBLISHER_JOBS.items():
        job_id = "publish" if workflow == "release.yml" else "release"
        assert definitions[workflow]["jobs"][job_id].get("name", job_id) == expected_name


@pytest.mark.parametrize("version", ["1.0.0", "0.9.0", "01.1.0"])
def test_release_cut_refuses_repeated_or_lower_versions_before_writing(monkeypatch, version):
    monkeypatch.setattr(cut_release, "versions", lambda: ("1.0.0", "1.0.0"))
    with pytest.raises(SystemExit, match="greater|leading zeroes"):
        cut_release.main(["--version", version])


def test_normal_full_run_does_not_require_release_only_evidence():
    needs = pipeline_needs()
    needs["detect"]["outputs"]["release"] = "false"
    needs["release-qualification"]["result"] = "skipped"
    assert required_results.evaluate(needs, pipeline=True, strict=True)["ok"]


def test_native_signing_keys_are_imported_only_on_admitted_tags_and_always_removed():
    desktop = workflows()["desktop-installers.yml"]["jobs"]
    mac = desktop["macos"]
    assert "github.ref_type == 'tag'" in mac["env"]["HAS_APPLE_SIGNING"]
    imports = [s for s in mac["steps"] if "security import" in s.get("run", "")]
    assert len(imports) == 1 and imports[0]["if"] == "env.HAS_APPLE_SIGNING == 'true'"
    cleanup = next(s for s in mac["steps"] if "security delete-keychain" in s.get("run", ""))
    assert "always()" in cleanup["if"]
    build = next(s for s in mac["steps"] if s.get("name") == "Build the DMG")
    assert "APPLE_CERTIFICATE_PASSWORD" not in build["env"]
    assert "AZURE_CLIENT_SECRET" not in desktop["windows"]["env"]
    signing = workflows()["sign-installer.yml"]["jobs"]["sign"]
    assert any(
        s.get("if") == "always()"
        and "offline-ceremony.key" in s.get("run", "")
        and "pq-mldsa65.key" in s["run"]
        for s in signing["steps"]
    )


class FakeAssetServer:
    def __init__(self, *, draft=True, race=None):
        self.draft = draft
        self.race = race
        self.assets = {}
        self.commands = []

    @staticmethod
    def metadata(path):
        raw = path.read_bytes()
        return {
            "name": path.name,
            "size": len(raw),
            "state": "uploaded",
            "digest": "sha256:" + hashlib.sha256(raw).hexdigest(),
        }

    def command(self, args):
        self.commands.append(args)
        assert args[:3] == ["gh", "release", "upload"]
        assert "--clobber" not in args
        path = Path(args[-1])
        if self.race:
            # A competing upload/finalizer completed between listing and POST.
            self.draft = False
            self.assets[path.name] = self.metadata(path)
            if self.race == "different":
                self.assets[path.name]["digest"] = "sha256:" + "0" * 64
            raise subprocess.CalledProcessError(1, args)
        assert path.name not in self.assets
        self.assets[path.name] = self.metadata(path)
        return subprocess.CompletedProcess(args, 0, "", "")

    def install(self, monkeypatch):
        monkeypatch.setattr(release_assets, "run_command", self.command)
        monkeypatch.setattr(release_assets, "api_pages", lambda *a: list(self.assets.values()))
        monkeypatch.setattr(
            release_assets,
            "release_info",
            lambda *a: {
                "databaseId": 123,
                "tagName": TAG,
                "isDraft": self.draft,
            },
        )


def test_draft_upload_is_idempotent_and_never_changes_release_visibility(monkeypatch, tmp_path):
    server = FakeAssetServer()
    server.install(monkeypatch)
    files = {name: tmp_path / name for name in ("installer.exe", "checksums.txt")}
    for path in files.values():
        path.write_bytes(b"verified build bytes")
    release_assets.upload_draft_files(REPO, TAG, files)
    release_assets.upload_draft_files(REPO, TAG, files)
    assert len(server.commands) == 2
    assert server.draft


@pytest.mark.parametrize("defect", ["different", "incomplete"])
def test_conflicting_draft_assets_block_all_uploads_before_the_first_write(
    monkeypatch,
    tmp_path,
    defect,
):
    server = FakeAssetServer()
    server.install(monkeypatch)
    existing, fresh = tmp_path / "z-existing.exe", tmp_path / "a-fresh.exe"
    existing.write_bytes(b"existing")
    fresh.write_bytes(b"fresh")
    server.assets[existing.name] = server.metadata(existing)
    if defect == "different":
        server.assets[existing.name]["digest"] = "sha256:" + "0" * 64
    else:
        server.assets[existing.name]["state"] = "uploading"
    with pytest.raises(ValueError, match="refusing to replace"):
        release_assets.upload_draft_files(REPO, TAG, {p.name: p for p in (fresh, existing)})
    assert server.commands == []


@pytest.mark.parametrize("race", ["identical", "different"])
def test_competing_upload_and_publication_never_reopens_or_replaces_assets(
    monkeypatch,
    tmp_path,
    race,
):
    server = FakeAssetServer(race=race)
    server.install(monkeypatch)
    path = tmp_path / "installer.exe"
    path.write_bytes(b"fixture installer")
    if race == "different":
        with pytest.raises(subprocess.CalledProcessError):
            release_assets.upload_draft_files(REPO, TAG, {path.name: path})
    else:
        release_assets.upload_draft_files(REPO, TAG, {path.name: path})
    assert not server.draft
    assert len(server.commands) == 1
    assert all(command[:3] == ["gh", "release", "upload"] for command in server.commands)


def test_asset_uploader_rejects_public_release_before_any_write(monkeypatch, tmp_path):
    server = FakeAssetServer(draft=False)
    server.install(monkeypatch)
    path = tmp_path / "installer.exe"
    path.write_bytes(b"bytes")
    with pytest.raises(ValueError, match="unpublished draft"):
        release_assets.upload_draft_files(REPO, TAG, {path.name: path})
    assert server.commands == []


@pytest.mark.parametrize("profile", ["desktop", "signing"])
def test_staging_requires_the_complete_profile_and_rejects_duplicate_names(tmp_path, profile):
    required = (
        release_assets.DESKTOP_ASSETS if profile == "desktop" else release_assets.SIGNING_ASSETS
    )
    for name in required:
        (tmp_path / name).write_bytes(b"asset fixture")
    (tmp_path / "unexpected-private-file.key").write_bytes(b"not an artifact")
    assert set(release_assets.staged_assets(tmp_path, profile)) == required
    duplicate = tmp_path / "nested"
    duplicate.mkdir()
    (duplicate / next(iter(required))).write_bytes(b"duplicate")
    with pytest.raises(ValueError, match="duplicate staged"):
        release_assets.staged_assets(tmp_path, profile)


def test_empty_staging_directory_cannot_upload_a_partial_release(tmp_path):
    with pytest.raises(ValueError, match="missing staged"):
        release_assets.staged_assets(tmp_path, "signing")


def test_upload_cli_uses_the_complete_desktop_profile_without_git_mutation(monkeypatch, tmp_path):
    server = FakeAssetServer()
    server.install(monkeypatch)
    for name in release_assets.DESKTOP_ASSETS:
        (tmp_path / name).write_bytes(b"staged artifact")
    assert (
        release_assets.main(
            [
                "upload",
                "--repo",
                REPO,
                "--tag",
                TAG,
                "--directory",
                str(tmp_path),
                "--profile",
                "desktop",
            ]
        )
        == 0
    )
    assert set(server.assets) == release_assets.DESKTOP_ASSETS


def test_workflow_policy_rejects_provenance_reopening_releases():
    definitions = workflows()
    definitions["sign-installer.yml"]["jobs"]["provenance"]["with"]["upload-assets"] = True
    assert check_workflow_policy.audit(definitions)


def test_producers_cannot_edit_visibility_or_replace_assets():
    definitions = workflows()
    for filename in ("desktop-installers.yml", "sign-installer.yml"):
        steps = definitions[filename]["jobs"]["release"]["steps"]
        assert not any(s.get("uses", "").startswith("softprops/") for s in steps)
        upload = next(s for s in steps if "release_assets.py upload" in s.get("run", ""))
        assert "--clobber" not in upload["run"]
    assert definitions["sign-installer.yml"]["jobs"]["provenance"]["with"]["upload-assets"] is False
