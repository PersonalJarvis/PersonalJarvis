"""``respawn_mascot`` — voice-driven recovery for a lost / hidden mascot.

Mirrors :mod:`jarvis.plugins.tool.reset_orb_position` (ADR-0016 L2): when
the user says "Maskottchen wieder auftauchen", "respawn mascot", "der
Spawner" etc., the local-action gate dispatches this tool. It clears the
``OverlaySupervisor``'s cap-state and forces a fresh subprocess spawn so
the mascot reappears even when the prior subprocess was hidden, hung, or
cap-fired (BUG-012 class).

Risk tier: ``safe``. Only mutates the in-process overlay subprocess
lifecycle. Plausibility / approval surface is the local-action gate
regex layer (anchored ``^...$`` patterns against a tight phrase list).
"""
from __future__ import annotations

from typing import Any

from jarvis.core.protocols import ExecutionContext, ToolResult


class RespawnMascotTool:
    """Force a fresh OverlaySupervisor subprocess spawn."""

    name: str = "respawn_mascot"
    risk_tier: str = "safe"
    description: str = (
        "Bringt das Maskottchen / den Overlay-Spawner zurück, indem die "
        "OverlaySupervisor-Cap geleert und der Subprocess frisch gespawnt "
        "wird (Voice-Recovery für BUG-012-Klasse)."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {},
        "required": [],
    }

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        del args, ctx  # tool is parameterless
        try:
            from jarvis.overlay.integration import get_overlay_supervisor
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"overlay integration not importable: {exc!r}",
            )

        supervisor = get_overlay_supervisor()
        if supervisor is None:
            return ToolResult(
                success=False,
                output="Maskottchen-Overlay ist nicht aktiv.",
                error="overlay supervisor not initialised",
            )

        try:
            await supervisor.force_respawn()
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False,
                output=None,
                error=f"force_respawn failed: {exc!r}",
            )

        return ToolResult(
            success=True,
            output="Maskottchen ist wieder da.",
            error=None,
        )


__all__ = ["RespawnMascotTool"]
