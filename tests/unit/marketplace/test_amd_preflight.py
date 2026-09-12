"""AMD availability must be honest before enabling a local connector."""

import json
import subprocess
from types import SimpleNamespace

import pytest

from jarvis.marketplace import amd_mcp


@pytest.mark.parametrize("platform", ["win32", "darwin"])
def test_unsupported_os_does_not_launch_cli(monkeypatch, platform):
    monkeypatch.setattr(amd_mcp.sys, "platform", platform)

    def forbidden(*args, **kwargs):
        pytest.fail("Unsupported OS must not invoke AMD tooling")

    monkeypatch.setattr(amd_mcp.shutil, "which", forbidden)
    monkeypatch.setattr(amd_mcp.subprocess, "run", forbidden)
    assert "operating system" in amd_mcp.amd_unavailable_reason()
    with pytest.raises(RuntimeError, match="operating system"):
        amd_mcp.read_amd_status()


@pytest.fixture
def linux_cli(monkeypatch, tmp_path):
    monkeypatch.setattr(amd_mcp.sys, "platform", "linux")
    monkeypatch.setattr(amd_mcp.shutil, "which", lambda _: "/opt/rocm/bin/amd-smi")
    pci = tmp_path / "pci"
    gpu = pci / "gpu-device"
    gpu.mkdir(parents=True)
    (gpu / "vendor").write_text("0x1002\n", encoding="ascii")
    (gpu / "class").write_text("0x030000\n", encoding="ascii")
    monkeypatch.setattr(amd_mcp, "_PCI_DEVICES", pci)
    monkeypatch.setattr(amd_mcp, "_DRM_DEVICES", tmp_path / "drm")
    return gpu


@pytest.mark.parametrize("vendor,device_class", [("0x10de", "0x030000"), ("0x1002", "0x060000")])
def test_installed_cli_without_amd_gpu_cannot_connect(monkeypatch, linux_cli, vendor, device_class):
    (linux_cli / "vendor").write_text(vendor, encoding="ascii")
    (linux_cli / "class").write_text(device_class, encoding="ascii")

    def forbidden(*args, **kwargs):
        pytest.fail("Missing AMD hardware must be detected before starting the CLI")

    monkeypatch.setattr(amd_mcp.subprocess, "run", forbidden)
    assert "No AMD GPU is visible" in amd_mcp.amd_unavailable_reason()
    with pytest.raises(RuntimeError, match="No AMD GPU is visible"):
        amd_mcp.read_amd_status()


@pytest.mark.parametrize("device_class", ["0x030000", "0x038000", "0x120000"])
def test_pci_amd_graphics_and_compute_devices_pass_preflight(linux_cli, device_class):
    (linux_cli / "class").write_text(device_class, encoding="ascii")
    assert amd_mcp.amd_unavailable_reason() is None


def test_drm_fallback_can_verify_host_without_pci_view(monkeypatch, linux_cli, tmp_path):
    monkeypatch.setattr(amd_mcp, "_PCI_DEVICES", tmp_path / "hidden-pci")
    device = tmp_path / "drm" / "card1" / "device"
    device.mkdir(parents=True)
    (device / "vendor").write_text("0x1002\n", encoding="ascii")
    assert amd_mcp.amd_unavailable_reason() is None


def test_missing_sysfs_is_unverifiable_not_false_hardware_absence(monkeypatch, linux_cli, tmp_path):
    monkeypatch.setattr(amd_mcp, "_PCI_DEVICES", tmp_path / "hidden-pci")
    reason = amd_mcp.amd_unavailable_reason()
    assert "could not be verified" in reason
    assert "container" in reason


def test_unreadable_device_metadata_has_safe_preflight_message(monkeypatch, linux_cli):
    original = amd_mcp.Path.read_text

    def read(path, *args, **kwargs):
        if path == linux_cli / "vendor":
            raise PermissionError("private hardware path")
        return original(path, *args, **kwargs)

    monkeypatch.setattr(amd_mcp.Path, "read_text", read)
    reason = amd_mcp.amd_unavailable_reason()
    assert "could not be verified" in reason
    assert "permissions" in reason
    assert "private" not in reason


@pytest.mark.parametrize(
    "payload",
    [
        {"gpu_data": []},
        {"error": "driver failed"},
        1,
        "error",
        [None],
        [{}],
        {"gpu_data": [{"error": "missing device"}]},
    ],
)
def test_empty_and_error_payloads_never_enable_connector(monkeypatch, linux_cli, payload):
    monkeypatch.setattr(
        amd_mcp.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload)),
    )
    with pytest.raises(RuntimeError, match="no GPU data"):
        amd_mcp.read_amd_status()


def test_unavailable_metrics_are_preserved_without_zero_defaults(monkeypatch, linux_cli):
    payload = {"gpu_data": [{"gpu": 0, "temperature": "N/A", "power": None}]}
    monkeypatch.setattr(
        amd_mcp.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=0, stdout=json.dumps(payload)),
    )
    assert amd_mcp.read_amd_status() == {"static": payload, "metric": payload}


@pytest.mark.parametrize(
    "error", [OSError("private path"), subprocess.TimeoutExpired("secret", 10)]
)
def test_command_failures_have_actionable_safe_messages(monkeypatch, linux_cli, error):
    def fail(*args, **kwargs):
        raise error

    monkeypatch.setattr(amd_mcp.subprocess, "run", fail)
    with pytest.raises(RuntimeError) as caught:
        amd_mcp.read_amd_status()
    assert "Check" in str(caught.value)
    assert "private" not in str(caught.value)
    assert "secret" not in str(caught.value)
    assert type(error).__name__ not in str(caught.value)
