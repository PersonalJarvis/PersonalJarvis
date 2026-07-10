"""``app-command`` — execute a Command-Registry command by voice/chat.

Router-tier. The ONE structured path for the LLM to drive app settings and
actions: the command id is enum-constrained to the curated registry
(``jarvis/commands/registry.py``), arguments are validated against the
command's JSON schema BEFORE anything is sent, and execution goes through the
SAME already-mounted REST endpoint the desktop UI uses (in-process ASGI
transport — full route validation, no TCP). A voice command therefore can
never behave differently from the UI button for the same action, and the
readback the brain speaks is composed from the SERVER RESPONSE (what actually
changed), never from the model's intent — echo-verify.

Dangerous commands (registry ``dangerous: true``) surface as risk tier
``ask`` via ``risk_tier_for_args``, which triggers the ToolExecutor's
two-turn spoken confirmation; everything else runs at ``monitor`` (audited,
reversible, no friction).

Security: the registry contains no raw-secret writes (AP-2) and no spawn
commands (mission dispatch stays with the dedicated spawn-worker tool,
AP-5/AP-14) — this tool must never enter a worker tool set.
"""
from __future__ import annotations

from typing import Any
from urllib.parse import quote

from jarvis.core.protocols import ToolResult

_TYPE_CHECKS: dict[str, type | tuple[type, ...]] = {
    "string": str,
    "boolean": bool,
    "number": (int, float),
    "integer": int,
    "object": dict,
}


def _validate_args(schema: dict[str, Any], args: dict[str, Any]) -> list[str]:
    """Minimal, dependency-free JSON-schema check: unknown keys, required,
    enum membership, primitive types, string length, numeric range."""
    props = schema.get("properties", {}) if schema else {}
    errors: list[str] = []
    for key in args:
        if key not in props:
            errors.append(f"unknown argument {key!r} (valid: {sorted(props)})")
    for req in schema.get("required", []) if schema else []:
        if req not in args:
            errors.append(f"missing required argument {req!r}")
    for key, value in args.items():
        spec = props.get(key)
        if spec is None:
            continue
        enum = spec.get("enum")
        if enum and value not in enum:
            errors.append(f"{key!r} must be one of {enum}, got {value!r}")
        expected = _TYPE_CHECKS.get(spec.get("type", ""))
        if expected is not None:
            ok = isinstance(value, expected)
            # bool is an int subclass — do not let True pass as a number.
            if spec.get("type") in ("number", "integer") and isinstance(value, bool):
                ok = False
            if not ok:
                errors.append(f"{key!r} must be a {spec['type']}, got {type(value).__name__}")
                continue
        if isinstance(value, str):
            if "minLength" in spec and len(value) < spec["minLength"]:
                errors.append(f"{key!r} is too short (min {spec['minLength']})")
            if "maxLength" in spec and len(value) > spec["maxLength"]:
                errors.append(f"{key!r} is too long (max {spec['maxLength']})")
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            if "minimum" in spec and value < spec["minimum"]:
                errors.append(f"{key!r} must be >= {spec['minimum']}")
            if "maximum" in spec and value > spec["maximum"]:
                errors.append(f"{key!r} must be <= {spec['maximum']}")
    return errors


def _summarize(title: str, data: Any) -> str:
    """One honest sentence from the server's actual response payload."""
    if isinstance(data, dict):
        # Provider-switch family: name the real old -> new transition.
        new = data.get("active") or data.get("new_provider")
        if new:
            old = data.get("old_provider")
            summary = (
                f"{title}: {old} -> {new}." if old and old != new
                else f"{title}: now {new}."
            )
            if data.get("requires_restart") or data.get("restart_required"):
                summary += " Takes effect after the next restart."
            elif data.get("persisted") is False:
                summary += " Applied live only (not persisted to disk)."
            return summary
        if data.get("requires_restart") or data.get("restart_required"):
            return f"{title} done — takes effect after the next restart."
    return f"{title} succeeded."


class AppCommandTool:
    """Execute a curated app command through its REST endpoint, in-process."""

    name: str = "app-command"
    risk_tier: str = "monitor"

    def __init__(
        self,
        app_resolver: Any | None = None,
        control_key_resolver: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        # Lazy resolvers (spawn-worker pattern): the FastAPI app is built after
        # the brain, so it must be looked up at execute time, not now.
        self._app_resolver = app_resolver
        self._control_key_resolver = control_key_resolver
        self._transport = transport
        from jarvis.commands.registry import get_registry

        commands = get_registry()
        lines = "\n".join(
            f"- {c.id}: {c.description}" + (" [requires confirmation]" if c.dangerous else "")
            for c in commands
        )
        self.description = (
            "Run ONE app command from the command registry — the structured, "
            "validated way to change app settings or query app state by "
            "voice/chat (provider switches, wake word, languages, volume, "
            "missions/tasks, restart). Pass the command id in 'command_id' and "
            "its arguments in 'args' (see each command's schema via GET "
            "/api/commands). Prefer this over composing raw CLI strings. "
            "Commands:\n" + lines
        )
        self.schema: dict[str, Any] = {
            "type": "object",
            "properties": {
                "command_id": {
                    "type": "string",
                    "enum": [c.id for c in commands],
                    "description": "The registry command to execute.",
                },
                "args": {
                    "type": "object",
                    "description": (
                        "Arguments for the command, matching its params schema "
                        "(e.g. {\"provider\": \"claude-api\"} for brain-switch)."
                    ),
                },
            },
            "required": ["command_id"],
        }

    # ------------------------------------------------------------------
    # Risk refinement: dangerous registry commands need the two-turn confirm.
    # ------------------------------------------------------------------
    def risk_tier_for_args(self, args: dict[str, Any]) -> str:
        from jarvis.commands.registry import get_command

        cmd = get_command(str(args.get("command_id", "")))
        if cmd is None:
            return self.risk_tier
        return "ask" if cmd.dangerous else self.risk_tier

    # ------------------------------------------------------------------
    def _resolve_transport(self) -> Any | None:
        if self._transport is not None:
            return self._transport
        from jarvis.core import runtime_refs

        app = (
            self._app_resolver() if self._app_resolver is not None
            else runtime_refs.get_web_app()
        )
        if app is None:
            return None
        import httpx

        return httpx.ASGITransport(app=app)

    def _control_key(self) -> str | None:
        if self._control_key_resolver is not None:
            return self._control_key_resolver()
        try:
            from jarvis.core import control_key

            return control_key.get_control_key()
        except Exception:  # noqa: BLE001 - most routes need no auth; degrade
            return None

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        from jarvis.commands.registry import get_command, get_registry

        command_id = str(args.get("command_id", ""))
        cmd = get_command(command_id)
        if cmd is None:
            return ToolResult(
                success=False,
                output={"requested": command_id},
                error=(
                    f"Unknown command id {command_id!r}. Valid ids: "
                    + ", ".join(c.id for c in get_registry())
                ),
            )

        cmd_args = args.get("args") or {}
        if not isinstance(cmd_args, dict):
            return ToolResult(
                success=False, output=None,
                error="'args' must be an object of command arguments.",
            )
        problems = _validate_args(cmd.params, cmd_args)
        if problems:
            return ToolResult(
                success=False,
                output={"command_id": cmd.id, "args": cmd_args},
                error=(
                    f"Invalid arguments for {cmd.id}: " + "; ".join(problems)
                    + ". Nothing was executed."
                ),
            )

        url_path = cmd.path
        payload = dict(cmd_args)
        for p in cmd.path_params:
            url_path = url_path.replace(
                "{" + p + "}", quote(str(payload.pop(p)), safe="")
            )

        transport = self._resolve_transport()
        if transport is None:
            return ToolResult(
                success=False, output=None,
                error=(
                    "The app server is not available in this runtime — use "
                    "the desktop UI or the jarvis CLI instead."
                ),
            )

        import httpx

        headers = {}
        key = self._control_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(
                transport=transport, base_url="http://jarvis.internal",
                headers=headers, timeout=30.0,
            ) as client:
                if cmd.method.upper() == "GET":
                    resp = await client.get(url_path, params=payload or None)
                else:
                    resp = await client.request(
                        cmd.method.upper(), url_path, json=payload or {}
                    )
        except httpx.HTTPError as exc:
            return ToolResult(
                success=False, output={"command_id": cmd.id},
                error=f"{cmd.title} failed: transport error: {exc}",
            )

        try:
            data = resp.json() if resp.content else None
        except ValueError:
            data = resp.text

        if resp.status_code >= 400:
            detail = data.get("detail", data) if isinstance(data, dict) else data
            return ToolResult(
                success=False,
                output={"command_id": cmd.id, "status": resp.status_code},
                error=f"{cmd.title} failed: HTTP {resp.status_code}: {detail}",
            )

        return ToolResult(
            success=True,
            output={
                "command_id": cmd.id,
                "summary": _summarize(cmd.title, data),
                "response": data,
            },
        )
