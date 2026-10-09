"""A dict-backed credential store, so tests never touch the real OS keyring."""

from __future__ import annotations


class FakeSecretStore:
    """The ``SecretStore`` shape of ``jarvis.society.credentials``."""

    def __init__(self, *, refuse: bool = False) -> None:
        self.values: dict[str, str] = {}
        self.refuse = refuse
        self.reads = 0

    def get(self, slot: str) -> str | None:
        self.reads += 1
        return self.values.get(slot)

    def set(self, slot: str, value: str) -> bool:
        if self.refuse:
            return False
        self.values[slot] = value
        return True

    def delete(self, slot: str) -> bool:
        self.values.pop(slot, None)
        return True
