from __future__ import annotations

import importlib.util
from pathlib import Path


def _load_gate_module():
    root = Path(__file__).resolve().parents[3]
    script = root / "scripts" / "ci" / "privacy_pre_push.py"
    spec = importlib.util.spec_from_file_location("privacy_pre_push_fallback", script)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_public_fallback_scanner_blocks_generic_secret(tmp_path: Path) -> None:
    gate = _load_gate_module()
    compiled, forbidden, allowlist = gate.load_secret_scanner(tmp_path)
    token = "ghp_" + ("a" * 36)

    findings = gate.scan_text_for_secrets("example.txt", token, compiled, allowlist)

    assert [finding["pattern"] for finding in findings] == ["github_token"]
    assert gate.forbidden_file(".env", forbidden)


def test_public_fallback_scanner_does_not_skip_clean_text(tmp_path: Path) -> None:
    gate = _load_gate_module()
    compiled, _, allowlist = gate.load_secret_scanner(tmp_path)

    assert gate.scan_text_for_secrets("README.md", "safe example", compiled, allowlist) == []
