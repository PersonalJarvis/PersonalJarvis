"""Anthropic tool-use definitions for the self-mod pipeline (Phase 7.3).

Three tools for the main-Jarvis tier:
- `list_mutable_settings`: What am I allowed to change?
- `get_config_value`: What value is currently set?
- `set_config_value`: Proposes a mutation (pending) — does NOT write itself,
  but either creates a pending entry in the store or confirms immediately
  if the setting is SAFE-tier.

Plan-§AD-9: all schemas with `strict: true`, `additionalProperties: false`,
`required` covers all properties, plus `input_examples` for better tool triggering.

Plan-§AD-2: self-mod tools are exclusively router/main-Jarvis tools.
The Jarvis-Agent worker has NO access (Wave 4: SUB_TOOLS deleted; worker sandbox without self-mod tools).

Defense-in-depth against "trust the model output" (Plan-AP-1):
Every handler re-validates its input against type and schema before
delegating to the pipeline.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any, ClassVar, Final, Literal

from jarvis.core.protocols import ExecutionContext, ToolResult
from jarvis.core.self_mod import (
    AllowlistViolationError,
    AtomicConfigWriter,
    AuditActor,
    AuditSource,
    BackupError,
    MutationRequest,
    PendingMutationStore,
    PreValidateError,
    ProviderSwitchLockedError,
    ReloadError,
    RollbackError,
    SecretAccessError,
    SelfModRegistry,
)

_LOG = logging.getLogger(__name__)

SELF_MOD_TOOL_NAMES: Final[tuple[str, ...]] = (
    "list_mutable_settings",
    "get_config_value",
    "set_config_value",
)

_REDACTED: Final[str] = "***"


# ----------------------------------------------------------------------
# Common Helpers
# ----------------------------------------------------------------------


def _maybe_redact(path: str, value: Any) -> Any:
    """Redacts values for sensitive paths (Plan-§AP-2 defense-in-depth)."""
    if SelfModRegistry.is_forbidden(path):
        return _REDACTED
    return value


def _as_typed_value(value: Any) -> Any:
    """Ensures tomlkit wrappers are unpacked to native Python values —
    otherwise a `tomlkit.items.String` object ends up in the tool output
    that is not JSON-serialisable.
    """
    if hasattr(value, "unwrap"):
        try:
            return value.unwrap()
        except (TypeError, AttributeError):
            pass
    return value


# ----------------------------------------------------------------------
# Tool 1: list_mutable_settings
# ----------------------------------------------------------------------


class ListMutableSettingsTool:
    """Plan-§7.3 Tool 1.

    Lists all mutable paths plus their current value. Read-only;
    does not write an audit entry (Plan-§AD-6 requires audit only for
    `set_config_value` and Phase-7.5 skill authoring).
    """

    name: ClassVar[str] = "list_mutable_settings"
    risk_tier: ClassVar[str] = "safe"
    description: ClassVar[str] = (
        "Use this whenever the user asks what they can change, what is "
        "configurable, or before suggesting any configuration change."
    )
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {},
        "required": [],
        "additionalProperties": False,
        "strict": True,
        "input_examples": [{}],
    }

    def __init__(self, *, writer: AtomicConfigWriter) -> None:
        self._writer = writer

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:  # noqa: ARG002 — ctx is required by the tool protocol
        # Defense-in-depth: schema re-validate (no trust in model output)
        if not isinstance(args, dict) or args:
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: list_mutable_settings accepts no parameters",
            )

        # Wave 2: enrich each entry with the value type + constraints so the
        # brain can map a natural-language phrase ("talk slower") onto a concrete
        # (path, value) — e.g. it sees tts.speed is a float at 1.0 and lowers it,
        # or that ui.theme is an enum and picks an allowed value.
        from jarvis.core.self_mod.schema_introspect import describe_field

        results: list[dict[str, Any]] = []
        for spec in SelfModRegistry.list_all():
            try:
                current = self._writer.read_value(spec.path)
            except BackupError as exc:
                _LOG.warning("read_value(%s) failed: %s", spec.path, exc)
                current = None
            entry: dict[str, Any] = {
                "path": spec.path,
                "current_value": _maybe_redact(spec.path, _as_typed_value(current)),
                "description": spec.description,
                "risk_tier": spec.risk_tier,
                "needs_restart": spec.needs_restart,
            }
            entry.update(describe_field(spec.path))  # value_type [+ enum/range]
            results.append(entry)
        return ToolResult(success=True, output=results)


# ----------------------------------------------------------------------
# Tool 2: get_config_value
# ----------------------------------------------------------------------


class GetConfigValueTool:
    """Plan-§7.3 Tool 2.

    Denies `security.*` paths even for reads (Plan-§AP-9 hash-leak protection).
    """

    name: ClassVar[str] = "get_config_value"
    risk_tier: ClassVar[str] = "safe"
    description: ClassVar[str] = (
        "Use this when the user asks about a current setting value, before "
        "proposing a change, or to confirm a setting after a change."
    )
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Dotted-Pfad in jarvis.toml, z.B. 'tts.provider'.",
            },
        },
        "required": ["path"],
        "additionalProperties": False,
        "strict": True,
        "input_examples": [
            {"path": "tts.provider"},
            {"path": "brain.primary"},
        ],
    }

    def __init__(self, *, writer: AtomicConfigWriter) -> None:
        self._writer = writer

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:  # noqa: ARG002
        if not isinstance(args, dict):
            return ToolResult(
                success=False, output=None, error="invalid_input: args must be a dict"
            )
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: 'path' (non-empty string) required",
            )

        # Plan-§AP-9: forbidden path → tool error, do not read the value.
        if SelfModRegistry.is_forbidden(path):
            return ToolResult(
                success=False,
                output={
                    "path": path,
                    "value": _REDACTED,
                    "in_allowlist": False,
                    "description": None,
                    "error_kind": "forbidden_path",
                },
                error="forbidden_path: secret/privileged paths are not readable",
            )

        spec = SelfModRegistry.get_spec(path)
        if spec is None:
            return ToolResult(
                success=True,
                output={
                    "path": path,
                    "value": None,
                    "in_allowlist": False,
                    "description": None,
                },
            )

        try:
            current = self._writer.read_value(path)
        except BackupError as exc:
            return ToolResult(
                success=False,
                output=None,
                error=f"read_failed: {exc}",
            )
        return ToolResult(
            success=True,
            output={
                "path": path,
                "value": _as_typed_value(current),
                "in_allowlist": True,
                "description": spec.description,
            },
        )


# ----------------------------------------------------------------------
# Tool 3: set_config_value
# ----------------------------------------------------------------------


class SetConfigValueTool:
    """Plan-§7.3 Tool 3.

    Does not write directly: creates a `PendingMutation` entry in the
    `PendingMutationStore`. SAFE-tier is confirmed immediately by the store
    (Plan-§AD-10); ASK-tier waits for voice confirmation (Phase 7.4).
    """

    name: ClassVar[str] = "set_config_value"
    risk_tier: ClassVar[str] = "ask"
    description: ClassVar[str] = (
        "Use this to PROPOSE a setting change. Does NOT write to disk — "
        "returns a pending mutation that the user must confirm. SAFE-tier "
        "settings (e.g. tts.speed, ui.theme) auto-apply without confirmation."
    )
    schema: ClassVar[dict[str, Any]] = {
        "type": "object",
        "properties": {
            "path": {
                "type": "string",
                "minLength": 1,
                "description": "Dotted path in jarvis.toml. Must be on the allowlist.",
            },
            "new_value": {
                "anyOf": [
                    {"type": "string"},
                    {"type": "number"},
                    {"type": "integer"},
                    {"type": "boolean"},
                ],
                "description": (
                    "New value. Its type is checked against the Pydantic "
                    "model by the pre-validate step."
                ),
            },
            "reason": {
                "type": "string",
                "description": (
                    "Kurzer human-readable Grund. Leerer String erlaubt, aber "
                    "Pflicht-Feld (strict-Mode)."
                ),
            },
        },
        "required": ["path", "new_value", "reason"],
        "additionalProperties": False,
        "strict": True,
        "input_examples": [
            {
                "path": "tts.speed",
                "new_value": 1.25,
                "reason": "User wants slower TTS",
            },
            {
                "path": "tts.provider",
                "new_value": "elevenlabs",
                "reason": "",
            },
        ],
    }

    def __init__(
        self,
        *,
        pending_store: PendingMutationStore,
        actor: AuditActor = AuditActor.HAUPTJARVIS,
        source: AuditSource = AuditSource.VOICE,
    ) -> None:
        self._pending = pending_store
        self._actor = actor
        self._source = source

    async def execute(
        self, args: dict[str, Any], ctx: ExecutionContext
    ) -> ToolResult:  # noqa: ARG002
        # Defense-in-depth schema re-validate
        if not isinstance(args, dict):
            return ToolResult(
                success=False, output=None, error="invalid_input: args must be a dict"
            )
        path = args.get("path")
        if not isinstance(path, str) or not path:
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: 'path' (non-empty string) required",
            )
        if "new_value" not in args:
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: 'new_value' required",
            )
        new_value = args["new_value"]
        if not isinstance(new_value, (str, int, float, bool)) or isinstance(
            new_value, bool
        ) and not isinstance(new_value, bool):
            # bool is a subtype of int — order must check bool specifically first
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: 'new_value' must be string|number|boolean",
            )
        # Strict type check: reject dict/list.
        if isinstance(new_value, (dict, list, tuple)):
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: 'new_value' must be a primitive (string|number|boolean)",
            )
        reason = args.get("reason", "")
        if not isinstance(reason, str):
            return ToolResult(
                success=False,
                output=None,
                error="invalid_input: 'reason' must be a string",
            )

        request = MutationRequest(
            path=path,
            new_value=new_value,
            actor=self._actor,
            source=self._source,
            reason=reason or None,
        )

        try:
            pending = self._pending.create(request)
        except ProviderSwitchLockedError as exc:
            # The active brain provider is the user's hard choice — Jarvis may
            # not change it by voice/chat, only the user via the CLI or the
            # manual provider switch in the desktop app.
            return ToolResult(
                success=False,
                output={"error_kind": "provider_switch_locked", "path": path},
                error=f"provider_switch_locked: {exc}",
            )
        except SecretAccessError as exc:
            return ToolResult(
                success=False,
                output={"error_kind": "forbidden_path", "path": path},
                error=f"forbidden_path: {exc}",
            )
        except AllowlistViolationError as exc:
            return ToolResult(
                success=False,
                output={"error_kind": "path_not_allowed", "path": path},
                error=f"path_not_allowed: {exc}",
            )
        except PreValidateError as exc:
            # Auto-confirm for SAFE-tier triggered pre-validate; for
            # ASK-tier this lands in the pending-store without pre-validate.
            return ToolResult(
                success=False,
                output={"error_kind": "validate_failed", "path": path},
                error=f"validate_failed: {exc}",
            )
        except (ReloadError, RollbackError) as exc:
            # Auto-confirm failed mid-pipeline.
            return ToolResult(
                success=False,
                output={
                    "error_kind": (
                        "reload_failed_rolled_back"
                        if isinstance(exc, ReloadError)
                        else "rollback_failed"
                    ),
                    "path": path,
                },
                error=f"{type(exc).__name__}: {exc}",
            )

        return ToolResult(success=True, output=pending.model_dump(mode="json"))


# ----------------------------------------------------------------------
# Factory
# ----------------------------------------------------------------------


def build_self_mod_tools(
    *,
    config_path: Path | str | None = None,
    writer: AtomicConfigWriter | None = None,
    pending_store: PendingMutationStore | None = None,
    auto_confirm_safe: bool = True,
    auto_apply: Literal["safe_only", "all"] = "safe_only",
    actor: AuditActor = AuditActor.HAUPTJARVIS,
    source: AuditSource = AuditSource.VOICE,
    writer_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Creates the three self-mod tools with a shared writer + store.

    Convenience factory for `jarvis/brain/factory.py` wiring. Tests can
    inject `writer` and/or `pending_store` directly. ``auto_apply="all"`` is the
    voice "never ask, always now" policy (Wave 1.3); the default ``"safe_only"``
    keeps the SAFE-auto / ASK-confirm split for REST/CLI.
    """
    if writer is None:
        kwargs = dict(writer_kwargs or {})
        if config_path is not None:
            kwargs.setdefault("config_path", config_path)
        writer = AtomicConfigWriter(**kwargs)
    if pending_store is None:
        pending_store = PendingMutationStore(
            writer=writer, auto_confirm_safe=auto_confirm_safe, auto_apply=auto_apply
        )
    return {
        ListMutableSettingsTool.name: ListMutableSettingsTool(writer=writer),
        GetConfigValueTool.name: GetConfigValueTool(writer=writer),
        SetConfigValueTool.name: SetConfigValueTool(
            pending_store=pending_store, actor=actor, source=source
        ),
    }


# Suppress lint: Callable is not used directly but is useful as a type hint.
_ = Callable
