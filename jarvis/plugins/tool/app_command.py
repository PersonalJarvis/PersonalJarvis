"""Registry command tools — one flat tool per Command-Registry entry.

Virtual loader (the ``cli-tools`` pattern): the ``app-command`` entry point
expands into ONE tool per curated command in ``jarvis/commands/registry.py``
— ``brain-switch``, ``provider-test``, ``wake-word-set``, … — each with the
command's own flat JSON schema.

Why flat tools and not one umbrella tool with a nested ``command_id``+
``args`` interface: the umbrella design was tried first and failed live
(forensic 2026-07-11 11:38): the router LLM read the command list in the
umbrella's description and called ``provider-test`` AS A TOOL NAME —
"tool 'provider-test' not in the router tool set" — spoken error. Flash-class
routers handle many small flat schemas far better than one nested dispatch
schema, and the repo already loads CLI/MCP tools exactly this way.

Every tool still validates its arguments against the registry schema BEFORE
anything is sent, and executes through the SAME already-mounted REST endpoint
the desktop UI uses (in-process ASGI transport — full route validation, no
TCP). The readback is composed from the SERVER RESPONSE (echo-verify), never
from the model's intent. Dangerous registry commands carry risk tier ``ask``
(ToolExecutor two-turn voice confirmation); the rest run at ``monitor``.

Security: the registry contains no raw-secret writes (AP-2) and no spawn
commands (mission dispatch stays with spawn-worker, AP-5/AP-14). Only entries
with an explicit ``worker_allowed`` grant may enter a mission-scoped broker;
configuration mutation and dangerous commands remain supervisor-only.
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


def _with_defaults(schema: dict[str, Any], args: dict[str, Any]) -> dict[str, Any]:
    """``args`` plus every schema default the caller left out.

    A ``default`` in a tool schema is a promise made to the model. It reads
    "compose — rewrite the instruction into a briefed task, default true",
    leaves the argument out because that is what a default is FOR, and expects
    the documented behaviour. Nothing kept that promise: the arguments went to
    the endpoint exactly as the model sent them, and the endpoint then applied
    ITS own default — which is chosen for the surface that calls it directly,
    not for the model reading the registry.

    Measured live 2026-07-27 18:47: ``agentic-ide-prompt`` declares
    ``compose: true`` while ``PromptRequest.compose`` defaults to False for the
    typed prompt bar, which sends what someone wrote by hand and must not have
    it rewritten. So every spoken instruction the router forwarded was typed
    into the coding agent verbatim — a one-line paraphrase where a briefed task
    with the workspace's files attached was the entire point of that path. The
    same shape sits behind every other default in the registry, so this is
    fixed once, here, rather than per command.
    """
    props = (schema or {}).get("properties", {})
    if not isinstance(props, dict):
        return dict(args)
    filled = dict(args)
    for key, spec in props.items():
        if key in filled or not isinstance(spec, dict) or "default" not in spec:
            continue
        filled[key] = spec["default"]
    return filled


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


#: Response keys carrying a FULL app-state snapshot. Mutating routes return one
#: so an open UI can re-render without a second fetch — but a language model
#: needs what CHANGED, not the snapshot. Measured: one Agentic-IDE spawn answers
#: with ~25 000 characters of workspace state (every pane, its transcript, the
#: project's skills and subagents), so three calls in a turn bury the actual
#: result and the reply the model has to act on. A live session on 2026-07-27
#: spent 58 000 input tokens on one turn that way and lost track of the pane it
#: had just opened. Callers that genuinely want the snapshot have a dedicated
#: status command.
_SNAPSHOT_KEYS = frozenset({"state"})


def _without_snapshots(data: Any) -> Any:
    """The server's response minus any full-state snapshot it echoed back."""
    if not isinstance(data, dict):
        return data
    trimmed = {key: value for key, value in data.items() if key not in _SNAPSHOT_KEYS}
    if trimmed.keys() <= {"ok"}:
        # The snapshot WAS the answer; trimming it would return nothing at all.
        return data
    return trimmed


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


def _apply_visible_terminal_context(
    command_id: str, args: dict[str, Any], ctx: Any
) -> dict[str, Any]:
    """Correct a model's stale pane guess from the user's deictic wording.

    Realtime models can call registry tools directly, before the manager's
    deterministic Agentic-IDE path runs. The execution context still carries
    the exact user utterance, so "the terminal here" can be resolved from the
    UI's current chat stage even if prior conversation made the model pass T1.
    """
    expected_kind = {
        "agentic-ide-prompt": "prompt",
        "agentic-ide-terminal-report": "report",
    }.get(command_id)
    utterance = str(getattr(ctx, "user_utterance", "") or "")
    if expected_kind is None or not utterance:
        return args
    try:
        from jarvis.agentic_ide import intent as ide_intent
        from jarvis.agentic_ide.session import get_registry

        session = get_registry().session
        current = session.contextual_terminal() if session is not None else None
        if session is None or current is None:
            return args
        found = ide_intent.detect_visible(
            utterance,
            terminal=current.name,
            names=[term.name for term in session.terminals],
        )
        if found is None or found.kind != expected_kind:
            return args
        corrected = dict(args)
        corrected["name"] = current.name
        return corrected
    except Exception:  # noqa: BLE001 - context is a correction, never a blocker
        return args


class _Runtime:
    """Shared execution plumbing: resolve the live app + control key once."""

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

    def resolve_transport(self) -> Any | None:
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

    def control_key(self) -> str | None:
        if self._control_key_resolver is not None:
            return self._control_key_resolver()
        try:
            from jarvis.core import control_key

            return control_key.get_control_key()
        except Exception:  # noqa: BLE001 - most routes need no auth; degrade
            return None


class RegistryCommandTool:
    """One registry command as a flat, schema-validated brain tool."""

    def __init__(self, command: Any, runtime: _Runtime) -> None:
        self._cmd = command
        self._runtime = runtime
        self.name: str = command.id
        self.risk_tier: str = "ask" if command.dangerous else "monitor"
        note = (
            " Requires the user's spoken confirmation before it runs."
            if command.dangerous else ""
        )
        self.description: str = (
            f"{command.description}{note} Executes the app's own validated "
            f"endpoint ({command.method} {command.path}); report the result "
            "the tool returns, never your assumption."
        )
        self.schema: dict[str, Any] = command.params or {
            "type": "object", "properties": {},
        }

    async def execute(self, args: dict[str, Any], ctx: Any) -> ToolResult:
        cmd = self._cmd
        cmd_args = _with_defaults(cmd.params, args or {})
        cmd_args = _apply_visible_terminal_context(cmd.id, cmd_args, ctx)
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

        transport = self._runtime.resolve_transport()
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
        key = self._runtime.control_key()
        if key:
            headers["Authorization"] = f"Bearer {key}"
        try:
            async with httpx.AsyncClient(
                # The request stays in-process through ASGITransport. Use a
                # canonical loopback authority so the global Host guard applies
                # the same policy as every other trusted local control client.
                transport=transport, base_url="http://127.0.0.1",
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
                "response": _without_snapshots(data),
            },
        )


class AppCommandTool:
    """Virtual loader: expands the Command Registry into flat brain tools.

    Registered under the ``app-command`` entry point (gated by ROUTER_TOOLS);
    the factory calls :meth:`expand` and registers the returned per-command
    tools — the loader itself never appears in the LLM tool set.
    """

    name: str = "app-command"
    risk_tier: str = "monitor"
    is_virtual_loader: bool = True

    def __init__(
        self,
        app_resolver: Any | None = None,
        control_key_resolver: Any | None = None,
        transport: Any | None = None,
    ) -> None:
        self._runtime = _Runtime(app_resolver, control_key_resolver, transport)

    def expand(self) -> list[RegistryCommandTool]:
        from jarvis.commands.registry import get_registry

        return [RegistryCommandTool(cmd, self._runtime) for cmd in get_registry()]
