"""Release qualification must reject incomplete native targets."""

import hashlib
import json

import pytest

from scripts.ci.prepare_native_release import ASSETS, TARGET_ASSETS, TARGETS, prepare


def _stage(directory, *, signed=True):
    for name in ASSETS:
        (directory / name).write_bytes(name.encode())
    for target in TARGETS:
        proof = {
            "target": target,
            "tag": "v1.2.3",
            "commit": "a" * 40,
            "asset_sha256": hashlib.sha256(
                (directory / TARGET_ASSETS[target]).read_bytes()
            ).hexdigest(),
            "installed": True,
            "reinstalled": True,
            "upgraded": True,
            "rollback_tested": True,
            "previous_tag": "v1.2.2",
            "settings_preserved": True,
            "database_preserved": True,
            "credential_preserved": True,
            "signed": signed,
            "notarized": signed,
        }
        (directory / f"proof-{target}.json").write_text(json.dumps(proof))


def test_prepare_binds_tag_and_requires_every_native_target(tmp_path):
    _stage(tmp_path)
    prepare(tmp_path, "v1.2.3", "a" * 40)
    lines = (tmp_path / "installers-SHA256SUMS.txt").read_text().splitlines()
    assert lines[0] == "# release: v1.2.3"
    assert [line.split("  ")[1] for line in lines[1:]] == [*ASSETS, "release-qualification.json"]
    qualification = json.loads((tmp_path / "release-qualification.json").read_text())
    assert set(qualification["targets"]) == set(TARGETS)


def test_prepare_rejects_missing_asset_or_native_proof(tmp_path):
    _stage(tmp_path)
    (tmp_path / ASSETS[0]).unlink()
    with pytest.raises(ValueError, match="missing or empty release asset"):
        prepare(tmp_path, "v1.2.3", "a" * 40)
    (tmp_path / ASSETS[0]).write_bytes(b"installer")
    _stage(tmp_path)
    (tmp_path / "proof-macos-arm64.json").unlink()
    with pytest.raises(ValueError, match="missing native smoke proof"):
        prepare(tmp_path, "v1.2.3", "a" * 40)


def test_prepare_rejects_unsigned_public_native_artifacts(tmp_path):
    _stage(tmp_path, signed=False)
    with pytest.raises(ValueError, match="Windows installer signature"):
        prepare(tmp_path, "v1.2.3", "a" * 40)


@pytest.mark.parametrize(
    "flag",
    [
        "installed",
        "upgraded",
        "rollback_tested",
        "settings_preserved",
        "database_preserved",
        "credential_preserved",
    ],
)
@pytest.mark.parametrize("bad_value", [False, "false", 1, None])
def test_prepare_requires_literal_true_for_every_smoke_result(tmp_path, flag, bad_value):
    _stage(tmp_path)
    proof_path = tmp_path / "proof-linux-x86_64.json"
    proof = json.loads(proof_path.read_text())
    if bad_value is None:
        del proof[flag]
    else:
        proof[flag] = bad_value
    proof_path.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match=flag):
        prepare(tmp_path, "v1.2.3", "a" * 40)


@pytest.mark.parametrize("field", ["commit", "asset_sha256"])
def test_prepare_rejects_proof_for_different_commit_or_asset(tmp_path, field):
    _stage(tmp_path)
    proof_path = tmp_path / "proof-linux-x86_64.json"
    proof = json.loads(proof_path.read_text())
    proof[field] = "b" * 40 if field == "commit" else "0" * 64
    proof_path.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="bound to commit and artifact"):
        prepare(tmp_path, "v1.2.3", "a" * 40)


@pytest.mark.parametrize("target", ["macos-arm64", "macos-x64"])
def test_prepare_rejects_unnotarized_mac(tmp_path, target):
    _stage(tmp_path)
    proof_path = tmp_path / f"proof-{target}.json"
    proof = json.loads(proof_path.read_text())
    proof["notarized"] = False
    proof_path.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="notarization"):
        prepare(tmp_path, "v1.2.3", "a" * 40)


@pytest.mark.parametrize("prior", ["v1.2.3", "v1.2.4", "garbage"])
def test_prepare_rejects_non_older_previous_release(tmp_path, prior):
    _stage(tmp_path)
    proof_path = tmp_path / "proof-linux-x86_64.json"
    proof = json.loads(proof_path.read_text())
    proof["previous_tag"] = prior
    proof_path.write_text(json.dumps(proof))
    with pytest.raises(ValueError, match="prior version"):
        prepare(tmp_path, "v1.2.3", "a" * 40)
