"""User-owned HTTP integrations, kept separate from the shipped marketplace.

Definitions contain no credentials. Keys use the normal secret store; edits
are atomic and serialized across desktop processes. Saving never calls a
provider, so defining an action cannot accidentally spend a user's credits.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import tempfile
from copy import deepcopy
from pathlib import Path
from typing import Any, Literal
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from filelock import FileLock
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from jarvis.core.paths import user_data_dir
from jarvis.marketplace.token_store import Tokens, TokenStore

log = logging.getLogger(__name__)
_ID = re.compile(r"^[a-f0-9]{32}$")
_PARAM = re.compile(r"\{([a-zA-Z_][a-zA-Z0-9_.\[\]-]*)\}")


def validate_path(value: str) -> str:
    """Allow a relative API path, never an alternate origin or traversal."""
    decoded = value
    for _ in range(4):
        decoded = unquote(decoded)
    if (
        not decoded.startswith("/")
        or decoded.startswith("//")
        or any(c in decoded for c in "\\?#")
        or any(ord(c) < 32 for c in decoded)
        or any(part in (".", "..") for part in decoded.split("/"))
    ):
        raise ValueError("Use an API path starting with /, without a query or traversal")
    return value


class ApiAuth(BaseModel):
    model_config = ConfigDict(extra="forbid")
    mode: Literal["none", "bearer", "header", "query"] = "bearer"
    header_name: str = Field(default="X-API-Key", pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,127}$")

    @model_validator(mode="after")
    def allowed_header(self) -> ApiAuth:
        if self.mode == "header" and self.header_name.lower() in {
            "host",
            "cookie",
            "content-length",
            "transfer-encoding",
            "connection",
            "proxy-authorization",
            "content-type",
            "accept-encoding",
        }:
            raise ValueError("This header cannot be used for API authentication")
        return self


class ApiParameter(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(pattern=r"^[a-zA-Z_][a-zA-Z0-9_.\[\]-]{0,127}$")
    location: Literal["path", "query", "header"] = "query"
    type: Literal["string", "integer", "number", "boolean", "array", "object"] = "string"
    required: bool = False
    description: str = Field(default="", max_length=500)
    value_schema: dict[str, Any] | None = None


class ApiAction(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(pattern=r"^[a-z][a-z0-9_]{0,23}$")
    description: str = Field(min_length=1, max_length=1500)
    method: Literal["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"] = "GET"
    path: str = Field(max_length=2000)
    parameters: list[ApiParameter] = Field(default_factory=list, max_length=64)
    body_schema: dict[str, Any] | None = None
    body_required: bool = True
    body_encoding: Literal["json", "multipart", "form", "binary"] = "json"
    file_fields: list[str] = Field(default_factory=list)
    content_type: str = "application/json"
    risk_tier: Literal["monitor", "ask", "block"] = "monitor"
    response: Literal["auto", "json", "text", "file"] = "auto"

    _path = field_validator("path")(validate_path)

    @field_validator("body_schema")
    @classmethod
    def valid_schema(cls, value: dict[str, Any] | None) -> dict[str, Any] | None:
        if value is None:
            return value
        from jsonschema import Draft202012Validator
        from jsonschema.exceptions import SchemaError

        raw = json.dumps(value)
        if len(raw) > 128_000:
            raise ValueError("Request schema is too large")

        # Reference resolution must never make hidden network requests.
        def reject_refs(node: Any) -> None:
            if isinstance(node, dict):
                if "$ref" in node or "$dynamicRef" in node:
                    raise ValueError("Inline request schemas; references are not supported")
                for child in node.values():
                    reject_refs(child)
            elif isinstance(node, list):
                for child in node:
                    reject_refs(child)

        reject_refs(value)
        try:
            Draft202012Validator.check_schema(value)
        except SchemaError:
            raise ValueError("Invalid JSON request schema") from None
        return value

    @model_validator(mode="after")
    def consistent_parameters(self) -> ApiAction:
        keys = [(p.location, p.name) for p in self.parameters]
        if len(keys) != len(set(keys)):
            raise ValueError("Parameter names must be unique within each location")
        expected = set(_PARAM.findall(self.path))
        if "{" in _PARAM.sub("", self.path) or "}" in _PARAM.sub("", self.path):
            raise ValueError("Invalid path parameter placeholder")
        if expected != {p.name for p in self.parameters if p.location == "path"}:
            raise ValueError("Every {path_parameter} must have a matching path parameter")
        if self.method == "GET" and self.body_schema is not None:
            raise ValueError("GET actions use path and query parameters, not a body")
        for parameter in self.parameters:
            self.valid_schema(parameter.value_schema)
        return self

    def input_schema(self) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        required = []
        for location in ("path", "query", "header"):
            params = [p for p in self.parameters if p.location == location]
            if not params:
                continue
            needed = [p.name for p in params if p.required or location == "path"]
            properties[location] = {
                "type": "object",
                "additionalProperties": False,
                "properties": {
                    p.name: deepcopy(
                        p.value_schema or {"type": p.type, "description": p.description}
                    )
                    for p in params
                },
                "required": needed,
            }
            if needed:
                required.append(location)
        if self.body_schema is not None:
            properties["body"] = deepcopy(self.body_schema)
            if self.body_required:
                required.append("body")
        return {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": False,
        }


class ApiConnectionInfo(BaseModel):
    """Public provenance and presentation for an automatically configured service."""

    model_config = ConfigDict(extra="forbid")
    service_id: str
    website: str
    spec_url: str
    brand_id: str = ""
    logo_data: str = Field(default="", max_length=100_000)
    categories: list[str] = Field(default_factory=list)
    status: Literal["configured", "verified", "limited"] = "configured"
    omitted_operations: int = 0

    @field_validator("logo_data")
    @classmethod
    def safe_logo(cls, value: str) -> str:
        if value and not re.fullmatch(r"data:image/png;base64,[A-Za-z0-9+/=]+", value):
            raise ValueError("Connection artwork must be a cached PNG")
        return value


class ApiDefinition(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: str = Field(default_factory=lambda: uuid4().hex, pattern=_ID.pattern)
    name: str = Field(min_length=1, max_length=100)
    description: str = Field(default="", max_length=2000)
    base_url: str = Field(max_length=2000)
    auth: ApiAuth = Field(default_factory=ApiAuth)
    enabled: bool = True
    actions: list[ApiAction] = Field(min_length=1, max_length=4096)
    connection: ApiConnectionInfo | None = None

    @field_validator("base_url")
    @classmethod
    def valid_url(cls, value: str) -> str:
        value = value.strip().rstrip("/")
        parts = urlsplit(value)
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username
            or parts.password
            or parts.query
            or parts.fragment
            or "{" in value
            or "}" in value
        ):
            raise ValueError("Use an HTTPS service URL without credentials, query or fragment")
        validate_path(parts.path or "/")
        return value

    @model_validator(mode="after")
    def unique_actions(self) -> ApiDefinition:
        if len({a.id for a in self.actions}) != len(self.actions):
            raise ValueError("Action IDs must be unique")
        return self


class CustomApiStore:
    def __init__(self, directory: Path | None = None, tokens: TokenStore | None = None) -> None:
        data_root = (
            Path(os.environ["JARVIS_DATA_DIR"])
            if os.environ.get("JARVIS_DATA_DIR")
            else user_data_dir()
        )
        self.directory = directory if directory is not None else data_root / "custom-apis"
        self.tokens = tokens if tokens is not None else TokenStore()
        self._cache: dict[str, tuple[int, int, ApiDefinition]] = {}

    def _path(self, api_id: str) -> Path:
        if not _ID.fullmatch(api_id):
            raise ValueError("Invalid API ID")
        return self.directory / f"{api_id}.json"

    @staticmethod
    def credential_id(api_id: str) -> str:
        return f"custom-api-{api_id}"

    def get(self, api_id: str) -> ApiDefinition | None:
        path = self._path(api_id)
        try:
            stat = path.stat()
            cached = self._cache.get(api_id)
            if cached and cached[:2] == (stat.st_mtime_ns, stat.st_size):
                return cached[2].model_copy(deep=True)
            definition = ApiDefinition.model_validate_json(path.read_text(encoding="utf-8"))
            self._cache[api_id] = (stat.st_mtime_ns, stat.st_size, definition)
            return definition.model_copy(deep=True)
        except FileNotFoundError:
            self._cache.pop(api_id, None)
            log.debug("Custom API %s has no saved definition", api_id)
            return None

    def list(self) -> list[ApiDefinition]:
        result = []
        for path in sorted(self.directory.glob("*.json")):
            try:
                definition = self.get(path.stem)
                if definition is not None:
                    result.append(definition)
            except (ValueError, OSError):
                log.warning("Ignoring an unreadable custom API definition: %s", path.name)
        return result

    def credential(self, api_id: str) -> str | None:
        definition = self.get(api_id)
        if definition is None:
            return None
        token = self.tokens.load(self.credential_id(api_id))
        if (
            token is not None
            and not token.needs_reauth
            and token.extra.get("api_scope") == self._credential_scope(definition)
        ):
            return token.access
        return None

    @staticmethod
    def _credential_scope(definition: ApiDefinition) -> str:
        scope = definition.base_url + "\n" + definition.auth.model_dump_json()
        return hashlib.sha256(scope.encode()).hexdigest()

    def resolve(self, expected: ApiDefinition) -> str | None:
        """Read definition and key under the same writer lock, before sending."""
        with FileLock(self.directory / ".lock", timeout=5):
            current = self.get(expected.id)
            if current != expected or not current.enabled:
                raise ValueError("API disabled, removed or changed; reload its tools")
            key = self.credential(expected.id) if current.auth.mode != "none" else None
            if current.auth.mode != "none" and not key:
                raise ValueError("API key missing or no longer matches this service")
            return key

    def save(self, definition: ApiDefinition, credential: str | None = None) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with FileLock(self.directory / ".lock", timeout=5):
            previous = self.get(definition.id)
            if credential is not None and (
                not credential.strip() or any(ord(c) < 32 for c in credential)
            ):
                raise ValueError("Enter a nonempty API key without control characters")
            if (
                previous is not None
                and previous.auth.mode != "none"
                and definition.auth.mode != "none"
                and (previous.base_url, previous.auth) != (definition.base_url, definition.auth)
                and credential is None
            ):
                raise ValueError(
                    "Re-enter the API key when changing the service URL or authentication"
                )
            key = credential or self.credential(definition.id)
            if definition.enabled and definition.auth.mode != "none" and not key:
                raise ValueError("Enter an API key before enabling this service")
            # A copied key must not leak into a shareable definition.
            if key and key in definition.model_dump_json():
                raise ValueError("Keep the API key only in the protected credential field")
            if credential is not None and definition.auth.mode != "none":
                self.tokens.save(
                    self.credential_id(definition.id),
                    Tokens(
                        access=credential,
                        extra={"api_scope": self._credential_scope(definition)},
                    ),
                )
            fd, temporary = tempfile.mkstemp(dir=self.directory, suffix=".tmp")
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as stream:
                    stream.write(definition.model_dump_json(indent=2))
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self._path(definition.id))
            finally:
                Path(temporary).unlink(missing_ok=True)
            if (
                definition.auth.mode == "none"
                and previous is not None
                and previous.auth.mode != "none"
            ):
                self.tokens.delete(self.credential_id(definition.id))

    def delete(self, api_id: str) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        with FileLock(self.directory / ".lock", timeout=5):
            # Removing the definition first revokes retained tool references even
            # if the credential store is temporarily locked during deletion.
            self._path(api_id).unlink(missing_ok=True)
            self.tokens.delete(self.credential_id(api_id))
