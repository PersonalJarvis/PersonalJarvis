"""Write-only source credentials on the existing portable secret backend."""

from typing import Any

from jarvis.core.config import delete_secret, get_secret, set_secret

FIELDS = ("username", "password", "token")


def slot(row: dict[str, Any], field: str) -> str:
    if field not in FIELDS:
        raise ValueError("Unknown credential field")
    return f"routine_source_{row['id']}_{row['created_at_ns']}_{field}"


def read(row: dict[str, Any]) -> dict[str, str]:
    return {field: get_secret(slot(row, field)) or "" for field in FIELDS}


def save(row: dict[str, Any], values: dict[str, str]) -> None:
    for field, value in values.items():
        if not value:
            delete_secret(slot(row, field))
            if get_secret(slot(row, field)):
                raise RuntimeError("Credential could not be cleared")
            continue
        if not set_secret(slot(row, field), value):
            raise RuntimeError("Credential storage failed; retry in the connection panel")


def presence(row: dict[str, Any]) -> dict[str, bool]:
    return {field: bool(value) for field, value in read(row).items()}
