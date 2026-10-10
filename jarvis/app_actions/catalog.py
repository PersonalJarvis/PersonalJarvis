"""The app-action catalog: one entry per operation of the app's REST surface."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Final

__all__ = [
    "ActionEntry",
    "build_catalog",
    "default_tier",
    "is_excluded",
    "live_catalog",
]

_METHODS: Final[tuple[str, ...]] = ("get", "post", "put", "patch", "delete")

#: Never offered to the brain: credentials and sign-ins (voice must never carry
#: a secret, AP-2), the raw control plane, webhooks, self-modification, the
#: action policy itself — Jarvis never edits its own permissions — the
#: main-brain switch, which only the person may flip (the provider lock),
#: consent to spend money: the missions' paid-API fallback switch and a
#: mission's paid-use approval (jarvis/missions/capacity.py), and consent to
#: send real messages out: the Ops core's live Telegram switch and its test
#: message (jarvis/ops/delivery.py).
_EXCLUDED: Final[re.Pattern[str]] = re.compile(
    r"(secret|api-?keys?|/keys?(/|$)|install-key|host-key|use-key|token|password|"
    r"credential|oauth|/auth(/|$)|login|pairing|/callback|/hooks/|openapi|/ws$|"
    r"^/api/control/|^/api/self-mod|^/api/app-actions|^/api/brain/switch$|"
    r"^/api/mission-billing|/capacity-decision$|"
    r"^/api/ops/notify/live$|^/api/ops/notify/telegram/test$)",
    re.IGNORECASE,
)

_SCHEMA_KEYS: Final[tuple[str, ...]] = ("type", "enum", "items", "properties", "required")

#: A field that carries a credential, wherever the URL does not say so: a
#: webhook route returned its token and a computer route took a password, and
#: both were offered to the model (AP-2). Matched on whole field names, so
#: usage counters such as ``tokens_in`` or ``max_tokens`` stay reachable.
_SECRET_FIELD: Final[re.Pattern[str]] = re.compile(
    r"^(?:\w+_)?(?:secret|secrets|token|password|passphrase|private_key|api_key|"
    r"client_secret|pat)$",
    re.IGNORECASE,
)

#: Fields that widen what an agent may do. Changing them on an existing agent
#: or session asks first, like a deletion (an approval route was able to
#: grant "allow always", a session PATCH could set bypassPermissions).
_PERMISSION_FIELD: Final[re.Pattern[str]] = re.compile(
    r"^(?:permission_mode|approval_mode|grant_mode|grants|ceiling|decision)$",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ActionEntry:
    """One app action. ``id`` is the stable OpenAPI operation id."""

    id: str
    method: str
    path: str
    area: str
    title: str
    description: str
    dangerous: bool
    path_params: tuple[str, ...] = ()
    query_params: tuple[str, ...] = ()
    has_body: bool = False
    parameters: dict[str, Any] = field(default_factory=dict)

    @property
    def mutating(self) -> bool:
        return self.method != "GET"

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "method": self.method,
            "path": self.path,
            "area": self.area,
            "title": self.title,
            "description": self.description,
            "dangerous": self.dangerous,
            "default_tier": default_tier(self),
        }


def default_tier(entry: ActionEntry) -> str:
    """Reading is safe, changing is logged, a dangerous change asks first."""
    if entry.dangerous:
        return "ask"
    return "monitor" if entry.mutating else "safe"


def is_excluded(path: str) -> bool:
    return bool(_EXCLUDED.search(path))


def _field_names(schema: Any, components: dict[str, Any], depth: int = 0) -> set[str]:
    """Every property name a schema can carry, through refs, unions and arrays."""
    if not isinstance(schema, dict) or depth > 6:
        return set()
    ref = schema.get("$ref")
    if isinstance(ref, str):
        schema = components.get(ref.rsplit("/", 1)[-1], {})
    names: set[str] = set()
    for key in ("anyOf", "oneOf", "allOf"):
        for option in schema.get(key) or ():
            names |= _field_names(option, components, depth + 1)
    for key in ("items", "additionalProperties"):
        names |= _field_names(schema.get(key), components, depth + 1)
    for name, value in (schema.get("properties") or {}).items():
        names.add(str(name))
        names |= _field_names(value, components, depth + 1)
    return names


def _json_schemas(operation: dict[str, Any]) -> tuple[Any, list[Any]]:
    """The JSON request body schema and every JSON response schema."""
    body = ((operation.get("requestBody") or {}).get("content") or {}).get("application/json")
    responses = [
        ((response or {}).get("content") or {}).get("application/json", {}).get("schema")
        for response in (operation.get("responses") or {}).values()
        if isinstance(response, dict)
    ]
    return (body or {}).get("schema"), responses


def _carries_secret(operation: dict[str, Any], components: dict[str, Any]) -> bool:
    body, responses = _json_schemas(operation)
    fields = _field_names(body, components)
    for schema in responses:
        fields |= _field_names(schema, components)
    return any(_SECRET_FIELD.match(name) for name in fields)


def _dangerous(
    method: str, path: str, operation: dict[str, Any], components: dict[str, Any] | None = None
) -> bool:
    if operation.get("x-jarvis-dangerous"):
        return True
    if method in {"PUT", "PATCH"} or (method == "POST" and "/approvals/" in path):
        body, _ = _json_schemas(operation)
        if any(_PERMISSION_FIELD.match(n) for n in _field_names(body, components or {})):
            return True
    try:
        from jarvis.cli_ctl.safety import is_dangerous
    except Exception:  # noqa: BLE001 — without the CLI extras, DELETE alone is dangerous
        return method == "DELETE"
    return is_dangerous(method, path)


def _resolve(schema: Any, components: dict[str, Any], depth: int = 0) -> Any:
    """Inline ``$ref``s and keep only what a model needs to fill arguments."""
    if not isinstance(schema, dict) or depth > 4:
        return {}
    ref = schema.get("$ref")
    if isinstance(ref, str):
        schema = components.get(ref.rsplit("/", 1)[-1], {})
    for key in ("anyOf", "oneOf", "allOf"):
        options = [o for o in schema.get(key) or () if o.get("type") != "null"]
        if options:
            merged = _resolve(options[0], components, depth + 1)
            return {**merged, **_short_description(schema)}
    out: dict[str, Any] = {k: schema[k] for k in _SCHEMA_KEYS if k in schema and k != "properties"}
    out.update(_short_description(schema))
    if "items" in out:
        out["items"] = _resolve(out["items"], components, depth + 1)
    props = schema.get("properties")
    if isinstance(props, dict):
        out["properties"] = {
            name: _resolve(value, components, depth + 1) for name, value in props.items()
        }
    return out


def _short_description(schema: dict[str, Any]) -> dict[str, str]:
    # Pydantic's auto title ("Agent Id") repeats the name; only a written
    # description tells the model something.
    text = str(schema.get("description") or "").strip()
    return {"description": text[:160]} if text else {}


def _entry(
    method: str, path: str, operation: dict[str, Any], components: dict[str, Any]
) -> ActionEntry | None:
    op_id = str(operation.get("operationId") or "")
    if (
        not op_id
        or is_excluded(path)
        or operation.get("x-jarvis-exclude")
        or _carries_secret(operation, components)
        # A file upload has no JSON body a model could fill; it only failed.
        or "multipart/form-data" in ((operation.get("requestBody") or {}).get("content") or {})
    ):
        return None
    properties: dict[str, Any] = {}
    required: list[str] = []
    path_params: list[str] = []
    query_params: list[str] = []
    for param in operation.get("parameters") or ():
        where = param.get("in")
        name = str(param.get("name") or "")
        if not name or where not in ("path", "query"):
            continue
        (path_params if where == "path" else query_params).append(name)
        properties[name] = _resolve(param.get("schema") or {}, components)
        if param.get("required"):
            required.append(name)
    body = (
        (operation.get("requestBody") or {}).get("content", {}).get("application/json", {})
    ).get("schema")
    if body:
        properties["body"] = _resolve(body, components)
        if (operation.get("requestBody") or {}).get("required"):
            required.append("body")
    parameters: dict[str, Any] = {"type": "object", "properties": properties}
    if required:
        parameters["required"] = required
    description = str(operation.get("description") or "").strip().split("\n\n", 1)[0]
    return ActionEntry(
        id=op_id,
        method=method.upper(),
        path=path,
        area=str((operation.get("tags") or ["app"])[0]),
        title=str(operation.get("summary") or op_id),
        description=" ".join(description.split())[:300],
        dangerous=_dangerous(method.upper(), path, operation, components),
        path_params=tuple(path_params),
        query_params=tuple(query_params),
        has_body=bool(body),
        parameters=parameters,
    )


def build_catalog(spec: dict[str, Any]) -> dict[str, ActionEntry]:
    """Every allowed operation of an OpenAPI spec, keyed by operation id."""
    components = (spec.get("components") or {}).get("schemas") or {}
    out: dict[str, ActionEntry] = {}
    for path, item in (spec.get("paths") or {}).items():
        for method in _METHODS:
            operation = (item or {}).get(method)
            if isinstance(operation, dict):
                entry = _entry(method, path, operation, components)
                if entry is not None:
                    out[entry.id] = entry
    return out


_cache: dict[int, dict[str, ActionEntry]] = {}


def live_catalog() -> dict[str, ActionEntry]:
    """The running app's catalog; empty before the web app exists (headless tools)."""
    from jarvis.core import runtime_refs

    app = runtime_refs.get_web_app()
    if app is None:
        return {}
    spec = app.openapi()
    key = id(spec)
    if key not in _cache:
        _cache.clear()
        _cache[key] = build_catalog(spec)
    return _cache[key]
