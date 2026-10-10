"""Frozen notices follow installed file ownership without bundling build tools."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from scripts.packaging import native_notices
from scripts.packaging.native_notices import collect_native_notices
from tests.fakes.fake_distributions import installed_distribution


def _inventory(output: Path) -> dict:
    return json.loads((output / "components.json").read_text(encoding="utf-8"))


def test_exact_notice_bytes_follow_namespace_file_ownership(tmp_path: Path) -> None:
    site = tmp_path / "site"
    runtime = installed_distribution(site, "runtime", "1.2", {
        "shared/runtime.py": b"",
        "runtime-1.2.dist-info/licenses/LICENSE": b"Copyright runtime\r\nPermission\r\n",
    })
    development = installed_distribution(site, "build-tool", "3.0", {
        "shared/build.py": b"",
        "build-tool-3.0.dist-info/licenses/LICENSE": b"Build tool notice",
    })
    output = tmp_path / "out"
    entries = collect_native_notices(
        [("shared.runtime", str(site / "shared/runtime.py"), "PYMODULE")], [], [], output,
        distributions=[development, runtime],
        module_distributions={"shared": ["runtime", "build-tool"]},
    )
    component, = _inventory(output)["components"]
    assert component["name"] == "runtime"
    notice, = component["license_files"]
    staged = Path(next(source for dest, source, _ in entries if dest == notice["file"]))
    assert staged.read_bytes() == b"Copyright runtime\r\nPermission\r\n"
    assert notice["sha256"] == hashlib.sha256(staged.read_bytes()).hexdigest()
    assert str(tmp_path) not in (output / "components.json").read_text(encoding="utf-8")


def test_wrapper_and_native_distribution_both_keep_their_notices(tmp_path: Path) -> None:
    site = tmp_path / "site"
    wrapper = installed_distribution(site, "speech", "1.0", {
        "speech/__init__.py": b"",
        "speech-1.0.dist-info/licenses/LICENSE": b"Wrapper terms",
    })
    core = installed_distribution(site, "speech-core", "1.0", {
        "speech/lib/core.pyd": b"Native bytes",
        "speech-core-1.0.dist-info/licenses/NOTICE": b"Native attribution",
    })
    output = tmp_path / "out"
    collect_native_notices(
        [("speech", str(site / "speech/__init__.py"), "PYMODULE")],
        [("speech/lib/core.pyd", str(site / "speech/lib/core.pyd"), "BINARY")], [], output,
        distributions=[wrapper, core], module_distributions={"speech": ["speech", "speech-core"]},
    )
    inventory = _inventory(output)
    assert {item["name"] for item in inventory["components"]} == {"speech", "speech-core"}
    native = next(item for item in inventory["components"] if item["name"] == "speech-core")
    assert native["native_files"] == ["speech/lib/core.pyd"]
    assert native["native_transitive_terms"] == "not_assessed"
    assert native["source_delivery_obligations"] == "not_assessed"
    assert inventory["compliance_status"] == "not_assessed"


def test_metadata_and_authors_code_are_not_a_license_bundle(tmp_path: Path) -> None:
    site = tmp_path / "site"
    distribution = installed_distribution(site, "media", "2.0", {
        "media/__init__.py": b"",
        "media-2.0.dist-info/licenses/LICENSE.txt": b"License text",
        "media-2.0.dist-info/licenses/AUTHORS.rst": b"Attribution",
        "media-2.0.dist-info/licenses/AUTHORS.py": b"print('not notice data')",
        "media-2.0.dist-info/licenses/__pycache__/AUTHORS.pyc": b"Cache",
        "media-2.0.dist-info/METADATA": b"Name: media\nVersion: 2.0\nLicense-File: AUTHORS.py\n",
    }, declared_licenses=("LICENSE.txt", "AUTHORS.rst", "AUTHORS.py"))
    output = tmp_path / "out"
    entries = collect_native_notices(
        [("media", str(site / "media/__init__.py"), "PYMODULE")], [], [], output,
        distributions=[distribution], module_distributions={},
    )
    destinations = [destination for destination, _, _ in entries]
    assert any(destination.endswith("/AUTHORS.rst") for destination in destinations)
    assert not any(
        destination.endswith((".py", ".pyc", "/METADATA")) for destination in destinations
    )
    assert not any("__pycache__" in destination for destination in destinations)


def test_missing_metadata_texts_and_unowned_native_files_are_explicit(tmp_path: Path) -> None:
    distribution = installed_distribution(tmp_path / "site", "editable", "0.4", {}, file_list=False)
    output = tmp_path / "out"
    collect_native_notices(
        [("editable.module", str(tmp_path / "source/module.py"), "PYMODULE")],
        [("vendor/codec.dll", str(tmp_path / "codec.dll"), "BINARY")], [], output,
        distributions=[distribution], module_distributions={"editable": ["editable"]},
    )
    inventory = _inventory(output)
    component, = inventory["components"]
    assert component["notice_status"] == "no_texts_found"
    assert component["file_list_available"] is False
    assert inventory["unmapped_native_files"] == ["vendor/codec.dll"]


def test_vendor_notice_text_and_utf16_survive_without_copying_binary_names(tmp_path: Path) -> None:
    site = tmp_path / "site"
    unicode_notice = "Copyright and permission\n".encode("utf-16")
    distribution = installed_distribution(site, "runtime", "1", {
        "runtime.py": b"",
        "runtime/ThirdPartyNotices.txt": unicode_notice,
        "runtime/LICENSE.png": b"\x89PNG\r\n\x1a\n\x00binary",
    })
    output = tmp_path / "out"
    entries = collect_native_notices(
        [("runtime", str(site / "runtime.py"), "PYMODULE")], [], [], output,
        distributions=[distribution], module_distributions={},
    )
    component, = _inventory(output)["components"]
    notice, = component["license_files"]
    assert notice["file"].endswith("/ThirdPartyNotices.txt")
    staged = Path(next(source for dest, source, _ in entries if dest == notice["file"]))
    assert staged.read_bytes() == unicode_notice
    assert component["collection_issues"] == [
        {"file": "runtime/LICENSE.png", "reason": "non_text_notice_excluded"},
    ]


def test_unsafe_and_missing_notice_records_are_not_copied(tmp_path: Path) -> None:
    site = tmp_path / "site"
    (tmp_path / "LICENSE").write_text("Outside distribution", encoding="utf-8")
    distribution = installed_distribution(site, "package", "1.0", {
        "package/__init__.py": b"",
    }, declared_licenses=("package/NOTICE",), extra_records=("../LICENSE", "package/NOTICE"))
    output = tmp_path / "out"
    entries = collect_native_notices(
        [("package", str(site / "package/__init__.py"), "PYMODULE")], [], [], output,
        distributions=[distribution], module_distributions={},
    )
    assert [destination for destination, _, _ in entries] == ["licenses/components.json"]
    component, = _inventory(output)["components"]
    reasons = {item["reason"] for item in component["collection_issues"]}
    assert "unsafe_notice_record" in reasons
    assert reasons & {"notice_unreadable", "declared_notice_missing"}


def test_notice_symlink_cannot_export_outside_distribution(tmp_path: Path) -> None:
    site = tmp_path / "site"
    distribution = installed_distribution(site, "package", "1.0", {
        "package/__init__.py": b"",
    }, extra_records=("package/LICENSE",))
    outside = tmp_path / "outside.txt"
    outside.write_text("Unrelated content", encoding="utf-8")
    try:
        (site / "package/LICENSE").symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"Symlink creation is unavailable: {exc}")
    output = tmp_path / "out"
    collect_native_notices(
        [("package", str(site / "package/__init__.py"), "PYMODULE")], [], [], output,
        distributions=[distribution], module_distributions={},
    )
    component, = _inventory(output)["components"]
    assert component["license_files"] == []
    assert component["collection_issues"] == [
        {"file": "package/LICENSE", "reason": "notice_outside_distribution"},
    ]


def test_inventory_is_deterministic_across_enumeration_order(tmp_path: Path) -> None:
    site = tmp_path / "site"
    a = installed_distribution(site, "a", "1", {"a.py": b"", "a/LICENSE": b"A"})
    b = installed_distribution(site, "b", "2", {"b.py": b"", "b/COPYING": b"B"})
    pure = [(name, str(site / f"{name}.py"), "PYMODULE") for name in ("a", "b")]
    first, second = tmp_path / "first", tmp_path / "second"
    collect_native_notices(pure, [], [], first, distributions=[a, b], module_distributions={})
    collect_native_notices(
        reversed(pure), [], [], second, distributions=[b, a], module_distributions={}
    )
    assert (first / "components.json").read_bytes() == (second / "components.json").read_bytes()


def test_ambiguous_modules_without_file_lists_remain_unresolved(tmp_path: Path) -> None:
    site = tmp_path / "site"
    runtime = installed_distribution(site, "runtime", "1", {}, file_list=False)
    development = installed_distribution(site, "build-tool", "1", {}, file_list=False)
    output = tmp_path / "out"
    collect_native_notices(
        [("shared.runtime", str(site / "shared/runtime.py"), "PYMODULE")], [], [], output,
        distributions=[runtime, development],
        module_distributions={"shared": ["runtime", "build-tool"]},
    )
    inventory = _inventory(output)
    assert inventory["components"] == []
    assert inventory["unmapped_module_roots"] == ["shared"]


def test_malformed_record_is_reported_without_claiming_notice_collection(tmp_path: Path) -> None:
    site = tmp_path / "site"
    distribution = installed_distribution(site, "runtime", "1", {})
    (site / "runtime-1.dist-info/RECORD").write_text("\n", encoding="utf-8")
    output = tmp_path / "out"
    collect_native_notices(
        [("runtime", str(site / "runtime.py"), "PYMODULE")], [], [], output,
        distributions=[distribution], module_distributions={"runtime": ["runtime"]},
    )
    component, = _inventory(output)["components"]
    assert component["notice_status"] == "no_texts_found"
    assert component["collection_issues"] == [{"reason": "distribution_file_list_unreadable"}]


def test_default_discovery_contains_malformed_used_and_unused_metadata(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    site = tmp_path / "site"
    runtime = installed_distribution(site, "runtime", "1", {
        "runtime.py": b"", "runtime/LICENSE": b"Runtime license",
    })
    missing = installed_distribution(site, "missing", "1", {}, declared_licenses=("LICENSE",))
    unused = installed_distribution(site, "build-tool", "1", {})
    for name, module in (("missing", "missing_pkg"), ("build-tool", "build_tool")):
        info = site / f"{name}-1.dist-info"
        (info / "RECORD").write_text("\n", encoding="utf-8")
        (info / "top_level.txt").write_text(module + "\n", encoding="utf-8")
    monkeypatch.setattr(
        native_notices.metadata, "distributions", lambda: [unused, missing, runtime]
    )
    output = tmp_path / "out"
    collect_native_notices([
        ("runtime", str(site / "runtime.py"), "PYMODULE"),
        ("missing_pkg", str(site / "missing_pkg.py"), "PYMODULE"),
    ], [], [], output)
    components = {item["name"]: item for item in _inventory(output)["components"]}
    assert set(components) == {"runtime", "missing"}
    assert components["runtime"]["notice_status"] == "texts_collected"
    assert components["missing"]["notice_status"] == "no_texts_found"
    assert {item["reason"] for item in components["missing"]["collection_issues"]} == {
        "distribution_file_list_unreadable", "declared_notice_missing",
    }
