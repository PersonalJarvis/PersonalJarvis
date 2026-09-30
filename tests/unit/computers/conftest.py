"""Shared fixtures: an in-memory keyring and a sandboxed computer store."""

from __future__ import annotations

from collections.abc import Iterator

import pytest

from jarvis.computers import cloud, identity, service
from jarvis.computers.service import ComputerService
from jarvis.computers.store import ComputerStore


class SecretBox:
    """A dict standing in for the OS keyring."""

    def __init__(self) -> None:
        self.values: dict[str, str] = {}

    def get(self, key: str, env_fallback: str | None = None) -> str | None:
        return self.values.get(key)

    def set(self, key: str, value: str) -> bool:
        self.values[key] = value
        return True

    def delete(self, key: str) -> bool:
        self.values.pop(key, None)
        return True


@pytest.fixture
def secret_box(monkeypatch: pytest.MonkeyPatch) -> Iterator[SecretBox]:
    box = SecretBox()
    for module in (identity, service, cloud):
        if hasattr(module, "get_secret"):
            monkeypatch.setattr(module, "get_secret", box.get)
        if hasattr(module, "set_secret"):
            monkeypatch.setattr(module, "set_secret", box.set)
        if hasattr(module, "delete_secret"):
            monkeypatch.setattr(module, "delete_secret", box.delete)
    yield box


@pytest.fixture
def computer_service(tmp_path, secret_box: SecretBox) -> ComputerService:  # noqa: ANN001
    return ComputerService(ComputerStore(tmp_path / "computers.json"))
