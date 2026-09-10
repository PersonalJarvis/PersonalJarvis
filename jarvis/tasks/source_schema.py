"""Jarvis-owned configuration for human, listener and workflow trigger sources."""

from __future__ import annotations

from typing import Literal
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

SourceKind = Literal[
    "manual", "chat", "form", "mcp", "sse", "kafka", "rabbitmq", "mqtt", "redis", "file", "workflow"
]
SOURCE_KINDS = (
    "manual",
    "chat",
    "form",
    "mcp",
    "sse",
    "kafka",
    "rabbitmq",
    "mqtt",
    "redis",
    "file",
    "workflow",
)
LISTENER_KINDS = frozenset({"sse", "kafka", "rabbitmq", "mqtt", "redis", "file"})


class FormField(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    label: str = Field(min_length=1, max_length=100)
    kind: Literal["text", "number", "boolean", "choice"] = "text"
    required: bool = True
    choices: tuple[str, ...] = Field(default=(), max_length=50)


class SourceSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    kind: SourceKind
    endpoint: str = Field(default="", max_length=2000)
    topic: str = Field(default="", max_length=250)
    group: str = Field(default="", max_length=100)
    start_position: Literal["latest", "earliest"] = "latest"
    path: str = Field(default="", max_length=2000)
    pattern: str = Field(default="*", max_length=200)
    recursive: bool = False
    poll_seconds: float = Field(default=5, ge=1, le=3600)
    file_events: tuple[Literal["created", "modified", "deleted"], ...] = (
        "created",
        "modified",
        "deleted",
    )
    form_fields: dict[str, FormField] = Field(default_factory=dict, max_length=30)
    upstream_id: str = ""
    upstream_kind: Literal["task", "workflow"] = "task"
    when: Literal["succeeded", "failed", "activated"] = "succeeded"

    @model_validator(mode="after")
    def validate_settings(self) -> SourceSettings:
        import re

        schemes = {
            "sse": {"http", "https"},
            "kafka": {"kafka", "kafkas"},
            "rabbitmq": {"amqp", "amqps"},
            "mqtt": {"mqtt", "mqtts"},
            "redis": {"redis", "rediss"},
        }
        if self.kind in schemes:
            parsed = urlsplit(self.endpoint)
            if parsed.scheme not in schemes[self.kind] or not parsed.hostname:
                raise ValueError(f"A valid {self.kind} endpoint is required")
            if parsed.username is not None or parsed.password is not None or parsed.fragment:
                raise ValueError(
                    "Enter credentials through the source connection panel, not the URL"
                )
            if parsed.query:
                raise ValueError(
                    "Source endpoints must not contain query credentials or parameters"
                )
            _ = parsed.port  # Validate the numeric port.
        if self.kind in {"kafka", "rabbitmq", "mqtt", "redis"} and not self.topic.strip():
            raise ValueError("A topic, queue or stream name is required")
        if self.kind == "file" and not self.path.strip():
            raise ValueError("A file or folder path is required")
        if self.kind == "workflow":
            UUID(self.upstream_id)
        for name, field in self.form_fields.items():
            if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]{0,63}", name):
                raise ValueError("Form field names must be simple identifiers")
            if field.kind == "choice" and not field.choices:
                raise ValueError("A choice field needs choices")
        return self


def validate_form(source: SourceSettings, payload: dict) -> None:
    import math

    if set(payload) - set(source.form_fields):
        raise ValueError("Unknown form fields")
    for name, field in source.form_fields.items():
        value = payload.get(name)
        if value is None or value == "":
            if field.required:
                raise ValueError(f"Required form field: {field.label}")
            continue
        valid = {
            "text": isinstance(value, str),
            "number": type(value) is int or (type(value) is float and math.isfinite(value)),
            "boolean": type(value) is bool,
            "choice": isinstance(value, str) and value in field.choices,
        }[field.kind]
        if not valid:
            raise ValueError(f"Invalid value for {field.label}")
