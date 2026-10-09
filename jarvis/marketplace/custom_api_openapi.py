"""Compile published OpenAPI operations into native, credential-free API actions."""

from __future__ import annotations

import hashlib
import re
from collections import Counter
from typing import Any
from urllib.parse import urljoin, urlsplit

from jarvis.marketplace.custom_api import ApiAction, ApiAuth, ApiParameter

METHODS = frozenset({"get", "post", "put", "patch", "delete", "head", "options"})
_SCHEMA_KEYS = frozenset(
    {
        "type",
        "properties",
        "required",
        "items",
        "enum",
        "const",
        "anyOf",
        "oneOf",
        "allOf",
        "additionalProperties",
        "minimum",
        "maximum",
        "exclusiveMinimum",
        "exclusiveMaximum",
        "minLength",
        "maxLength",
        "minItems",
        "maxItems",
        "pattern",
        "format",
        "description",
        "uniqueItems",
        "minProperties",
        "maxProperties",
        "not",
    }
)


class OpenApiImportError(ValueError):
    """A document cannot safely describe a single-key HTTP service."""


def dereference(document: dict[str, Any], value: Any) -> dict[str, Any]:
    """Resolve local object references without ever fetching a reference URL."""
    seen: set[str] = set()
    while isinstance(value, dict) and "$ref" in value:
        ref = value["$ref"]
        if not isinstance(ref, str) or not ref.startswith("#/") or ref in seen:
            raise OpenApiImportError("The API description contains an unsupported reference")
        seen.add(ref)
        target: Any = document
        try:
            for part in ref[2:].split("/"):
                target = target[part.replace("~1", "/").replace("~0", "~")]
        except (KeyError, TypeError):
            raise OpenApiImportError("The API description contains a missing reference") from None
        value = target
    return value if isinstance(value, dict) else {}


def inline_schema(
    document: dict[str, Any],
    source: Any,
    *,
    depth: int = 0,
    seen: frozenset[str] = frozenset(),
    budget: list[int] | None = None,
) -> dict[str, Any]:
    """Bound recursive schemas, retain JSON constraints, omit documentation noise."""
    if budget is None:
        budget = [350]
    budget[0] -= 1
    if not isinstance(source, dict) or depth > 14 or budget[0] < 0:
        return {}
    ref = source.get("$ref")
    if ref:
        if ref in seen:
            return {
                "type": "object",
                "description": "Recursive object; use the service documentation",
            }
        seen = seen | {str(ref)}
        source = dereference(document, source)
    result: dict[str, Any] = {}
    for key, value in source.items():
        if key not in _SCHEMA_KEYS:
            continue
        if key == "properties":
            result[key] = {
                name: inline_schema(document, item, depth=depth + 1, seen=seen, budget=budget)
                for name, item in value.items()
                if not item.get("readOnly", False)
            }
        elif key in {"items", "not"} or (key == "additionalProperties" and isinstance(value, dict)):
            result[key] = inline_schema(document, value, depth=depth + 1, seen=seen, budget=budget)
        elif key in {"anyOf", "oneOf", "allOf"}:
            result[key] = [
                inline_schema(document, v, depth=depth + 1, seen=seen, budget=budget) for v in value
            ]
        elif key == "description":
            result[key] = re.sub(r"<[^>]+>", "", str(value))[:120]
        elif key in {"exclusiveMinimum", "exclusiveMaximum"} and isinstance(value, bool):
            if value:
                bound = "minimum" if key == "exclusiveMinimum" else "maximum"
                if bound in source:
                    result[key] = source[bound]
        else:
            result[key] = value
    if source.get("nullable") and isinstance(result.get("type"), str):
        result["type"] = [result["type"], "null"]
    if "properties" in result and "required" in result:
        result["required"] = [n for n in result["required"] if n in result["properties"]]
    return result


def api_base_url(document: dict[str, Any], source_url: str) -> str:
    servers = document.get("servers") or []
    if servers:
        server = servers[0]
        value = str(server.get("url", ""))
        for name, variable in server.get("variables", {}).items():
            value = value.replace("{" + name + "}", str(variable.get("default", "")))
        return urljoin(source_url, value).rstrip("/")
    if document.get("host"):
        if "https" not in document.get("schemes", ["https"]):
            raise OpenApiImportError("This API does not publish an HTTPS address")
        return "https://" + document["host"] + document.get("basePath", "").rstrip("/")
    parts = urlsplit(source_url)
    # FastAPI documents on the provider's API host commonly omit `servers`.
    if parts.hostname and parts.hostname.startswith("api.") and parts.hostname != "api.apis.guru":
        return f"https://{parts.netloc}"
    raise OpenApiImportError("The API description does not identify its service address")


def _scheme_auth(document: dict[str, Any], source: Any) -> ApiAuth | None:
    scheme = dereference(document, source)
    if scheme.get("type") == "apiKey" and scheme.get("in") in {"header", "query"}:
        return ApiAuth(mode=scheme["in"], header_name=scheme["name"])
    if (
        scheme.get("type") == "http" and str(scheme.get("scheme")).lower() == "bearer"
    ) or scheme.get("type") in {"oauth2", "openIdConnect"}:
        return ApiAuth(mode="bearer")
    return None


def api_auth(document: dict[str, Any]) -> ApiAuth:
    schemes = document.get("components", {}).get("securitySchemes", {}) or document.get(
        "securityDefinitions", {}
    )
    for source in schemes.values():
        auth = _scheme_auth(document, source)
        if auth is not None:
            return auth
    headers: Counter[str] = Counter()
    for item in document.get("paths", {}).values():
        for method, operation in item.items():
            if method not in METHODS:
                continue
            for value in [*item.get("parameters", []), *operation.get("parameters", [])]:
                parameter = dereference(document, value)
                name = str(parameter.get("name", ""))
                if parameter.get("in") == "header" and re.search(
                    r"api[-_]?key|authorization|access[-_]?token", name, re.I
                ):
                    headers[name] += 1
    if headers:
        name = headers.most_common(1)[0][0]
        return (
            ApiAuth(mode="bearer")
            if name.lower() == "authorization"
            else ApiAuth(mode="header", header_name=name)
        )
    raise OpenApiImportError("The published API does not describe how to send an API key")


def _accepts_auth(document: dict[str, Any], operation: dict[str, Any], auth: ApiAuth) -> bool:
    requirements = operation.get("security", document.get("security", []))
    schemes = document.get("components", {}).get("securitySchemes", {}) or document.get(
        "securityDefinitions", {}
    )
    if not requirements:
        return True
    return any(
        not requirement
        or (
            len(requirement) == 1
            and _scheme_auth(document, schemes.get(next(iter(requirement)), {})) == auth
        )
        for requirement in requirements
    )


def _action_id(method: str, path: str, operation: dict[str, Any]) -> str:
    label = re.sub(
        r"[^a-z0-9_]+", "_", str(operation.get("operationId") or f"{method}_{path}").lower()
    ).strip("_")
    if not label or not label[0].isalpha():
        label = "action_" + label
    digest = hashlib.sha256(f"{method} {path}".encode()).hexdigest()[:6]
    return label[:17] + "_" + digest


def compile_openapi(
    document: dict[str, Any], auth: ApiAuth, base_url: str
) -> tuple[list[ApiAction], list[str], int]:
    """Import HTTP actions; return explicit unsupported-operation coverage."""
    actions: list[ApiAction] = []
    categories: set[str] = set()
    omitted = 0
    for path, path_source in document.get("paths", {}).items():
        item = dereference(document, path_source)
        for method, operation in item.items():
            if method not in METHODS:
                continue
            server_source = operation if operation.get("servers") else item
            if (
                server_source.get("servers") and api_base_url(server_source, base_url) != base_url
            ) or not _accepts_auth(document, operation, auth):
                omitted += 1
                continue
            parameters: dict[tuple[str, str], ApiParameter] = {}
            body_schema = None
            body_required = False
            file_fields: list[str] = []
            encoding = "json"
            content_type = "application/json"
            form_properties: dict[str, Any] = {}
            form_required: list[str] = []
            for value in [*item.get("parameters", []), *operation.get("parameters", [])]:
                param = dereference(document, value)
                location, name = param.get("in"), param.get("name", "")
                if location == "header" and name.lower() in {
                    auth.header_name.lower(),
                    "authorization",
                    "content-type",
                    "accept",
                }:
                    continue
                if location == "query" and auth.mode == "query" and name == auth.header_name:
                    continue
                schema = inline_schema(document, param.get("schema", param))
                if location == "body":
                    body_schema = schema
                    body_required = bool(param.get("required"))
                elif location == "formData":
                    form_properties[name] = schema
                    if param.get("type") == "file":
                        form_properties[name] = {
                            "type": "string",
                            "description": "Absolute path of the file to upload",
                        }
                        file_fields.append(name)
                    if param.get("required"):
                        form_required.append(name)
                elif location in {"path", "query", "header"}:
                    kind = schema.get("type", "string")
                    if not isinstance(kind, str) or kind not in {
                        "string",
                        "integer",
                        "number",
                        "boolean",
                        "array",
                        "object",
                    }:
                        kind = "string"
                    parameters[(location, name)] = ApiParameter(
                        name=name,
                        location=location,
                        type=kind,
                        required=bool(param.get("required")),
                        value_schema=schema,
                        description=str(param.get("description", ""))[:500],
                    )
            body = dereference(document, operation.get("requestBody", {}))
            content = body.get("content", {})
            if content:
                body_required = bool(body.get("required"))
                content_type = next(
                    (
                        t
                        for t in [
                            "application/json",
                            "multipart/form-data",
                            "application/x-www-form-urlencoded",
                            "application/octet-stream",
                        ]
                        if t in content
                    ),
                    "",
                )
                if not content_type:
                    omitted += 1
                    continue
                body_schema = inline_schema(document, content[content_type].get("schema", {}))
                encoding = {
                    "multipart/form-data": "multipart",
                    "application/x-www-form-urlencoded": "form",
                    "application/octet-stream": "binary",
                }.get(content_type, "json")
                for name, prop in body_schema.get("properties", {}).items():
                    variants = [prop, *prop.get("anyOf", []), prop.get("items", {})]
                    if any(v.get("format") == "binary" for v in variants):
                        file_fields.append(name)
                        prop["description"] = (
                            "Absolute local file path to upload (or a list of paths)"
                        )
                if encoding == "binary":
                    body_schema = {
                        "type": "string",
                        "description": "Absolute local file path to upload",
                    }
            elif form_properties:
                body_required = bool(form_required)
                encoding = (
                    "multipart"
                    if file_fields
                    or "multipart/form-data"
                    in operation.get("consumes", document.get("consumes", []))
                    else "form"
                )
                content_type = (
                    "multipart/form-data"
                    if encoding == "multipart"
                    else "application/x-www-form-urlencoded"
                )
                body_schema = {
                    "type": "object",
                    "properties": form_properties,
                    "required": form_required,
                }
            if method == "get":
                body_schema = None
            actions.append(
                ApiAction(
                    id=_action_id(method, path, operation),
                    method=method.upper(),
                    path=path,
                    description=str(
                        operation.get("summary")
                        or operation.get("description")
                        or f"{method.upper()} {path}"
                    )[:1500],
                    parameters=list(parameters.values()),
                    body_schema=body_schema,
                    body_required=body_required,
                    body_encoding=encoding,
                    content_type=content_type,
                    file_fields=file_fields,
                    risk_tier="ask" if method == "delete" else "monitor",
                )
            )
            categories.update(str(tag) for tag in operation.get("tags", []) if tag)
    if not actions:
        raise OpenApiImportError("No usable HTTP operations were found in the API description")
    return actions, sorted(categories), omitted
