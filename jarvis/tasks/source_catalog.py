"""A capability catalogue derived from Jarvis' own source drivers."""

from importlib.util import find_spec

from .source_schema import SOURCE_KINDS

DEPENDENCIES = {
    "kafka": "aiokafka",
    "rabbitmq": "aio_pika",
    "mqtt": "paho.mqtt",
    "redis": "redis.asyncio",
}
PACKAGES = {
    "kafka": "aiokafka>=0.12,<1",
    "rabbitmq": "aio-pika>=9,<10",
    "mqtt": "paho-mqtt>=2.1,<3",
    "redis": "redis>=5,<9",
}
GROUPS = {
    "manual": "human",
    "chat": "human",
    "form": "human",
    "mcp": "api",
    "sse": "stream",
    "kafka": "stream",
    "rabbitmq": "stream",
    "mqtt": "stream",
    "redis": "stream",
    "file": "system",
    "workflow": "internal",
}


def available(kind: str) -> bool:
    module = DEPENDENCIES.get(kind)
    if module is None:
        return True
    try:
        return find_spec(module) is not None
    except (ModuleNotFoundError, ValueError):
        return False


def catalog() -> list[dict]:
    rows = [
        {
            "id": kind,
            "group": GROUPS[kind],
            "installed": available(kind),
            "dependency": DEPENDENCIES.get(kind),
        }
        for kind in SOURCE_KINDS
    ]
    rows.extend(
        {"id": kind, "group": "time", "installed": True}
        for kind in ("calendar", "every", "at_time", "after_delay", "cron")
    )
    rows.extend(
        {"id": kind, "group": "external", "installed": True}
        for kind in ("github", "linear", "gmail", "slack", "stripe")
    )
    rows.extend(
        [
            {"id": "webhook", "group": "api", "installed": True},
            {"id": "on_event", "group": "system", "installed": True},
            {"id": "event_hook", "group": "internal", "installed": True},
        ]
    )
    rows.extend(
        {
            "id": "workflow_" + phase,
            "group": "system",
            "installed": True,
            "source_kind": "workflow",
            "when": "activated" if phase == "activated" else "failed",
        }
        for phase in ("activated", "failed")
    )
    return rows
