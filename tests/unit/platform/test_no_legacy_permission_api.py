"""Ratchet: the legacy macOS permission wall must not come back.

The rebuild deleted the readiness aggregation, the stable-identity-gates-acting
path, the legacy ``snapshot()``/``request()``, the identity-reset marker file and
the Automation consent file. This module fails when any of them is reintroduced,
by name in the product code, the CI scripts and the workflows, and by shape on the
port itself. The only place allowed to name the two state files an earlier build
left behind is the one-time cleanup in ``jarvis/platform/permissions.py``.
"""

from __future__ import annotations

import inspect
import os
import re
from pathlib import Path

import pytest

from jarvis.platform import permissions
from jarvis.platform.permissions import SystemPermissionPort

_ROOT = Path(__file__).resolve().parents[3]
_SCANNED_DIRS = ("jarvis", "scripts", ".github")
_SCANNED_SUFFIXES = {".py", ".yml", ".yaml", ".sh", ".ps1", ".toml", ".spec"}
_SKIPPED_PARTS = {"node_modules", "dist", "__pycache__", ".venv", "wheels"}

# Names of deleted behaviour. Each is a whole identifier (``\b``), so ``reset_row`` and
# ``automation_consent_runner`` (the killable consent seam that stays) never match.
_REMOVED_NAMES = (
    "runtime_access_granted",
    "runtime_feature_ready",
    "FEATURE_REQUIREMENTS",
    "active_features",
    "identity_reset_marker_path",
    "identity_reset_pending",
    "record_identity_reset",
    "_clear_identity_reset",
    "_read_identity_reset",
    "automation_consent_path",
    "_read_automation_consent",
    "_write_automation_consent",
    "_clear_automation_consent",
    "screen_recording_granted",
    "PermissionStatus",
)
# Chained calls on the process-wide port or a fresh one: the legacy v1 entry points.
_REMOVED_CALLS = re.compile(
    r"(?:SystemPermissionPort\(\)|get_system_permission_port\(\))"
    r"\s*\.\s*(?:snapshot|request|open_settings|reset)\s*\("
)
_LEFTOVER_FILES = ("macos-tcc-reset.json", "macos-automation-consent.json")
_CLEANUP_HOME = "jarvis/platform/permissions.py"


def _scanned_files() -> list[Path]:
    files: list[Path] = []
    for directory in _SCANNED_DIRS:
        for current, subdirs, names in os.walk(_ROOT / directory):
            # Prune before descending: the frontend's node_modules is enormous.
            subdirs[:] = [name for name in subdirs if name not in _SKIPPED_PARTS]
            files.extend(
                Path(current) / name for name in names if Path(name).suffix in _SCANNED_SUFFIXES
            )
    return files


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


@pytest.fixture(scope="module")
def sources() -> dict[str, str]:
    return {path.relative_to(_ROOT).as_posix(): _read(path) for path in _scanned_files()}


def test_the_scan_sees_the_product_the_ci_scripts_and_the_workflows(
    sources: dict[str, str],
) -> None:
    assert _CLEANUP_HOME in sources
    assert "scripts/ci/check_frozen_browser.py" in sources
    assert ".github/workflows/macos-desktop.yml" in sources


@pytest.mark.parametrize("name", _REMOVED_NAMES)
def test_a_removed_name_does_not_appear_in_the_product_the_scripts_or_the_workflows(
    name: str, sources: dict[str, str]
) -> None:
    pattern = re.compile(rf"\b{re.escape(name)}\b")

    offenders = sorted(rel for rel, text in sources.items() if pattern.search(text))

    assert offenders == [], f"{name} was deleted with the legacy permission wall: {offenders}"


def test_no_chained_call_reaches_a_legacy_port_entry_point(sources: dict[str, str]) -> None:
    offenders = sorted(rel for rel, text in sources.items() if _REMOVED_CALLS.search(text))

    assert offenders == []


def test_only_the_one_time_cleanup_names_the_two_leftover_state_files(
    sources: dict[str, str],
) -> None:
    for name in _LEFTOVER_FILES:
        offenders = sorted(rel for rel, text in sources.items() if name in text)
        assert offenders == [_CLEANUP_HOME], (name, offenders)


def test_the_leftover_cleanup_is_called_only_on_the_darwin_install_path(
    sources: dict[str, str],
) -> None:
    callers = sorted(
        rel
        for rel, text in sources.items()
        if re.search(r"\bremove_leftover_state_files\s*\(", text) and rel != _CLEANUP_HOME
    )

    # Nothing initialises at import time or on the boot critical path (AP-26).
    assert callers == ["jarvis/setup/macos_app_bundle.py"]
    assert not re.search(r"^remove_leftover_state_files\(", sources[_CLEANUP_HOME], re.M)


def test_the_port_exposes_only_the_primitives_the_service_composes() -> None:
    public = {
        name
        for name, _member in inspect.getmembers(SystemPermissionPort)
        if not name.startswith("_")
    }

    assert public == {
        "has_desktop_session",
        "launched_as_bundle",
        "open_pane",
        "outside_installed_app",
        "platform",
        "request_native",
        "reset_row",
        "state",
        "usage_string_present",
    }


def test_the_port_keeps_no_restart_flag_and_no_recorded_answers() -> None:
    attributes = set(vars(SystemPermissionPort(platform_name="linux")))

    assert not [name for name in attributes if "restart" in name]
    # Injected seams, the static bundle identity and the last opened pane: nothing recorded.
    assert attributes == {
        "_platform_name",
        "_module_loader",
        "_iohid_check",
        "_screen_capture_live_check",
        "_credential_store_backend",
        "_credential_store_recover",
        "_automation_probe",
        "_iohid_request",
        "_automation_consent_runner",
        "_bundle_identity_cache",
        "_last_opened_url",
    }


def test_the_module_no_longer_exports_the_readiness_aggregation() -> None:
    for name in ("FEATURE_REQUIREMENTS", "active_features", "PermissionStatus"):
        assert not hasattr(permissions, name), name
        assert name not in permissions.__all__


def test_a_state_read_is_shallow_unless_a_deep_read_is_asked_for() -> None:
    parameters = inspect.signature(SystemPermissionPort.state).parameters

    assert parameters["deep"].default is False
    assert parameters["deep"].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters["target"].kind is inspect.Parameter.KEYWORD_ONLY


def test_the_app_identity_keeps_no_foreground_and_no_expected_id() -> None:
    fields = set(permissions.AppIdentity.__dataclass_fields__)

    assert fields == {"app_name", "bundle_id", "bundle_path", "launched_as_bundle", "stable"}


@pytest.mark.parametrize(
    "relative",
    [
        "scripts/ci/check_frozen_browser.py",
        "scripts/ci/check_frozen_macos_app.py",
        ".github/workflows/macos-desktop.yml",
        ".github/workflows/desktop-installers.yml",
    ],
)
def test_the_ci_probes_read_only_status_keys_that_survive(
    relative: str, sources: dict[str, str]
) -> None:
    """``/api/permissions/status`` lost its v1 keys; a probe that reads one would go blind."""
    text = sources[relative]

    for key in ("features", "wanted", "identity_reset", "restart_required", "expected_bundle_id"):
        assert not re.search(rf"""["']{key}["']""", text), (relative, key)
