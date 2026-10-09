"""Dependency-free errors shared by the pairing API and its lazy service."""


class PairingError(Exception):
    def __init__(self, message: str, status: int = 400) -> None:
        super().__init__(message)
        self.status = status
