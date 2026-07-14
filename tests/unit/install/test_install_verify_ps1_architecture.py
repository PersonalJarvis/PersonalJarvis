"""Windows verifier architecture guard and pinned-binary regression tests."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[3]
INSTALL_VERIFY_PS1 = REPO / "install" / "install-verify.ps1"
BLOCK_BEGIN = "# --- verifier-architecture begin"
BLOCK_END = "# --- verifier-architecture end"
POWERSHELLS = tuple(
    dict.fromkeys(
        executable
        for name in ("pwsh", "powershell")
        if (executable := shutil.which(name)) is not None
    )
)


def _source() -> str:
    return INSTALL_VERIFY_PS1.read_text(encoding="utf-8")


def _architecture_block() -> str:
    source = _source()
    assert BLOCK_BEGIN in source and BLOCK_END in source
    return source[source.index(BLOCK_BEGIN) : source.index(BLOCK_END)]


def test_guard_allows_only_amd64_and_arm64() -> None:
    block = _architecture_block()
    assert "$supportedArchitectures = @('AMD64', 'ARM64')" in block
    assert "if ($arch -notin $supportedArchitectures)" in block
    assert "exit 1" in block


def test_arm64_keeps_the_sha_pinned_x64_verifier_assets() -> None:
    source = _source()
    block = _architecture_block()
    assert "built-in Windows 11 x64 emulation" in block
    assert "SHA-pinned cosign-windows-amd64.exe" in block
    assert "slsa-verifier-windows-amd64.exe" in block
    assert "cosign-windows-arm64" not in source
    assert "slsa-verifier-windows-arm64" not in source
    assert "$ActualSha -ne $COSIGN_SHA256_WINDOWS" in source
    assert "$ActualSlsaSha -ne $SLSA_VERIFIER_SHA256_WINDOWS" in source


@pytest.mark.parametrize("powershell", POWERSHELLS or (None,))
@pytest.mark.parametrize(
    ("architecture", "supported"),
    [
        ("AMD64", True),
        ("ARM64", True),
        ("x86", False),
        ("ARM", False),
        ("IA64", False),
        ("", False),
    ],
)
def test_architecture_block_fails_closed_at_runtime(
    tmp_path: Path,
    powershell: str | None,
    architecture: str,
    supported: bool,
) -> None:
    if powershell is None:
        pytest.skip("PowerShell is not available")
    driver = (
        "$ErrorActionPreference = 'Stop'\n"
        f"$env:PROCESSOR_ARCHITECTURE = '{architecture}'\n"
        + _architecture_block()
        + "\nWrite-Output 'CONTINUED'\n"
    )
    path = tmp_path / "architecture-guard.ps1"
    path.write_text(driver, encoding="utf-8")
    result = subprocess.run(
        [powershell, "-NoProfile", "-File", str(path)],
        capture_output=True,
        text=True,
        timeout=30,
    )

    if supported:
        assert result.returncode == 0, result.stderr or result.stdout
        assert "CONTINUED" in result.stdout
        assert "cosign-windows-amd64.exe" in result.stdout
        if architecture == "ARM64":
            assert "built-in Windows 11 x64 emulation" in result.stdout
    else:
        assert result.returncode == 1
        assert "CONTINUED" not in result.stdout
        assert "unsupported platform" in result.stdout
