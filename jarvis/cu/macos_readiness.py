"""Side-effect-free readiness probe for native macOS Computer-Use.

This is the first layer of MacAgentBench: it answers whether the current host
has the permissions and native adapters required for reliable desktop work
without clicking, typing, opening applications or prompting for permissions.
Live action qualification remains a separate, explicit user-run step.
"""
from __future__ import annotations

import sys
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class ReadinessCheck:
    id: str
    ok: bool
    detail: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class MacOSReadinessReport:
    platform: str
    ready: bool
    checks: tuple[ReadinessCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "ready": self.ready,
            "checks": [check.to_dict() for check in self.checks],
        }


def _permission_check(port: Any, permission_id: Any, label: str) -> ReadinessCheck:
    try:
        allowed = bool(port.runtime_access_granted(permission_id))
    except Exception as exc:  # noqa: BLE001 - probe must not crash diagnostics
        return ReadinessCheck(
            id=f"permission:{permission_id}",
            ok=False,
            detail=f"{label} permission probe failed: {exc}",
        )
    if allowed:
        return ReadinessCheck(
            id=f"permission:{permission_id}",
            ok=True,
            detail=f"{label} is ready",
        )
    try:
        raw_state = port.state(permission_id)
        state = getattr(raw_state, "value", raw_state)
    except Exception:  # noqa: BLE001 - permission-state detail is diagnostic fallback only
        state = "not granted"
    return ReadinessCheck(
        id=f"permission:{permission_id}",
        ok=False,
        detail=f"{label} is {state}",
    )


async def probe_macos_readiness(
    *,
    permission_port: Any | None = None,
    tree_source: Any | None = None,
    actuator_factory: Callable[[], Any] | None = None,
    human_activity_probe: Callable[[], Any] | None = None,
    platform: str | None = None,
) -> MacOSReadinessReport:
    """Return a diagnostic report without performing any input action.

    Required checks mirror the existing ``computer_use`` permission contract:
    Screen Recording, Accessibility and event posting. When Accessibility is
    ready, one read-only accessibility observation proves that semantic UI
    discovery works. When event posting is ready, constructing the configured
    actuator proves an input backend is available; no input is sent. A final
    Quartz HID probe verifies that Jarvis can distinguish recent physical user
    input from its own synthetic events for human takeover.
    """
    platform_name = platform or sys.platform
    if platform_name != "darwin":
        return MacOSReadinessReport(
            platform=platform_name,
            ready=False,
            checks=(
                ReadinessCheck(
                    id="platform:macos",
                    ok=False,
                    detail="MacAgentBench native readiness is only applicable on macOS",
                ),
            ),
        )

    from jarvis.platform.permissions import (  # noqa: PLC0415
        PermissionId,
        get_system_permission_port,
    )

    port = permission_port or get_system_permission_port()
    checks: list[ReadinessCheck] = [
        ReadinessCheck("platform:macos", True, "running on macOS"),
        _permission_check(port, PermissionId.SCREEN_RECORDING, "Screen Recording"),
        _permission_check(port, PermissionId.ACCESSIBILITY, "Accessibility"),
        _permission_check(port, PermissionId.EVENT_POSTING, "Input Control"),
    ]

    accessibility_ready = next(
        check.ok for check in checks if check.id == f"permission:{PermissionId.ACCESSIBILITY}"
    )
    if accessibility_ready:
        try:
            if tree_source is None:
                from jarvis.vision.tree_factory import make_ui_tree_source  # noqa: PLC0415

                tree_source = make_ui_tree_source()
            observation = await tree_source.observe()
            node_count = len(getattr(observation, "nodes", ()) or ())
            checks.append(
                ReadinessCheck(
                    "semantic:ax-tree",
                    True,
                    f"Accessibility tree observation succeeded ({node_count} nodes)",
                )
            )
        except Exception as exc:  # noqa: BLE001 - readiness reports probe failure instead of raising
            checks.append(
                ReadinessCheck(
                    "semantic:ax-tree",
                    False,
                    f"Accessibility tree observation failed: {exc}",
                )
            )
    else:
        checks.append(
            ReadinessCheck(
                "semantic:ax-tree",
                False,
                "Accessibility tree was not probed because Accessibility is not ready",
            )
        )

    event_posting_ready = next(
        check.ok for check in checks if check.id == f"permission:{PermissionId.EVENT_POSTING}"
    )
    if event_posting_ready:
        try:
            if actuator_factory is None:
                # Readiness must test construction, not the runtime human-handoff
                # wrapper in jarvis.cu.actuate.get_actuator: recent physical
                # input means "yield now", not "backend missing".
                from jarvis.cu.actuate.base import get_actuator  # noqa: PLC0415

                actuator_factory = get_actuator
            actuator = actuator_factory()
            checks.append(
                ReadinessCheck(
                    "actuation:backend",
                    True,
                    f"input backend is available ({getattr(actuator, 'name', type(actuator).__name__)})",
                )
            )
        except Exception as exc:  # noqa: BLE001 - readiness records backend unavailability
            checks.append(
                ReadinessCheck(
                    "actuation:backend",
                    False,
                    f"input backend is unavailable: {exc}",
                )
            )
    else:
        checks.append(
            ReadinessCheck(
                "actuation:backend",
                False,
                "input backend was not probed because Input Control is not ready",
            )
        )

    try:
        if human_activity_probe is None:
            from jarvis.cu.human_activity import macos_human_activity  # noqa: PLC0415

            human_activity_probe = macos_human_activity
        activity = human_activity_probe()
        available = bool(getattr(activity, "available", False))
        recent = bool(getattr(activity, "recent", False))
        detail = str(getattr(activity, "detail", "") or "")
        checks.append(
            ReadinessCheck(
                "handoff:hardware-input",
                available,
                (
                    f"hardware-input handoff is available ({detail})"
                    + ("; user is active now" if available and recent else "")
                    if available
                    else f"hardware-input handoff is unavailable ({detail or 'no Quartz HID signal'})"
                ),
            )
        )
    except Exception as exc:  # noqa: BLE001 - readiness records handoff probe failure
        checks.append(
            ReadinessCheck(
                "handoff:hardware-input",
                False,
                f"hardware-input handoff probe failed: {exc}",
            )
        )

    ready = all(check.ok for check in checks)
    return MacOSReadinessReport(platform="darwin", ready=ready, checks=tuple(checks))


__all__ = ["MacOSReadinessReport", "ReadinessCheck", "probe_macos_readiness"]
