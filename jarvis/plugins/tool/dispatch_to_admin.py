"""dispatch_to_admin-Tool — Brain ruft Admin-Ops via UAC-elevatetem Helper auf.

Risk-Tier = ``ask``. Das bedeutet: jeder Brain-Call wird durch den
Approval-Workflow gereicht, **ausser** der User hat via
``[safety.whitelist]`` ein Pattern wie ``dispatch_to_admin *`` gesetzt.
Destruktive Ops (DESTRUCTIVE_OPS) bekommen on top einen zweiten Prompt,
auch bei globaler Whitelist — das ist Absicht.

Call-Flow:

1. Brain ruft ``{"op": {...AdminOperation JSON...}}`` auf.
2. Tool validiert das JSON gegen das Pydantic-Schema.
3. ``AdminClient.execute(op, destructive_approved=...)``.
4. Bei ``DestructiveRequiresApproval``:
   → Tool returnt ``ToolResult(success=False, error="destructive_requires_approval",
   output={"op_id": ..., "op_type": ...})``. Der Caller (Risk-Tier-Executor)
   zeigt dem User einen Prompt und ruft das Tool erneut mit
   ``destructive_approved=True``.
5. Bei Erfolg/Fehler: das ``AdminResponse``-Model wird als dict ins
   ``ToolResult.output`` gepackt.
"""
from __future__ import annotations

from typing import Any

from pydantic import TypeAdapter, ValidationError

from jarvis.admin.client import AdminClient, DestructiveRequiresApproval
from jarvis.admin.schema import DESTRUCTIVE_OPS, AdminOperation
from jarvis.core.bus import EventBus
from jarvis.core.protocols import ExecutionContext, ToolResult

_ADMIN_OP_ADAPTER: TypeAdapter[AdminOperation] = TypeAdapter(AdminOperation)


class DispatchToAdminTool:
    """Protocol-kompatibles Tool. Wird von Brain via Tool-Call aufgerufen."""

    name: str = "dispatch_to_admin"
    risk_tier: str = "ask"
    description: str = (
        "Schickt eine Admin-Operation (winget install, service start/stop, "
        "firewall, registry-write, scheduled task, write_protected_path) "
        "an den UAC-elevateten Admin-Helper. Destruktive Ops "
        "(uninstall, remove, write_registry_hklm, write_protected_path) "
        "brauchen explizite Zustimmung."
    )
    schema: dict[str, Any] = {
        "type": "object",
        "properties": {
            "op": {
                "type": "object",
                "description": (
                    "AdminOperation-JSON. Muss einen 'type'-Key haben "
                    "(z.B. 'install_winget') und die fuer den Op-Typ "
                    "noetigen Felder. Siehe jarvis.admin.schema."
                ),
            },
            "destructive_approved": {
                "type": "boolean",
                "description": (
                    "Wenn true, wird die Op auch bei destruktivem Typ "
                    "ausgefuehrt. Default false — der Risk-Tier-Executor "
                    "setzt das nach User-Zustimmung."
                ),
                "default": False,
            },
        },
        "required": ["op"],
    }

    def __init__(
        self,
        *,
        bus: EventBus | None = None,
        client_factory: Any = None,
    ) -> None:
        self._bus = bus
        # Optional-Injection fuer Tests — sonst lazy in execute.
        self._client_factory = client_factory

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:
        op_payload = args.get("op")
        destructive_approved = bool(args.get("destructive_approved", False))

        if not isinstance(op_payload, dict):
            return ToolResult(
                success=False, output=None,
                error="'op' muss ein JSON-Objekt sein.",
            )

        try:
            op = _ADMIN_OP_ADAPTER.validate_python(op_payload)
        except ValidationError as exc:
            return ToolResult(
                success=False, output=None,
                error=f"AdminOperation invalid: {exc.error_count()} error(s)",
            )

        client = self._build_client()
        try:
            resp = await client.execute(
                op, destructive_approved=destructive_approved
            )
        except DestructiveRequiresApproval as dra:
            return ToolResult(
                success=False,
                output={
                    "op_id": dra.op_id,
                    "op_type": dra.op_type,
                    "destructive": True,
                },
                error="destructive_requires_approval",
            )
        except Exception as exc:  # noqa: BLE001
            return ToolResult(
                success=False, output=None,
                error=f"{type(exc).__name__}: {exc}",
            )

        return ToolResult(
            success=resp.success,
            output={
                "op_id": str(resp.op_id),
                "op_type": op.type,
                "success": resp.success,
                "duration_ms": resp.duration_ms,
                "result": resp.result,
                "destructive": op.type in DESTRUCTIVE_OPS,
            },
            error=resp.error_message if not resp.success else None,
        )

    def _build_client(self) -> AdminClient:
        if self._client_factory is not None:
            return self._client_factory()
        return AdminClient(bus=self._bus)


__all__ = ["DispatchToAdminTool"]
