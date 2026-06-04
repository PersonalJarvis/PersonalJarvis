"""Harness layer: adapters for sub-agent frameworks (L5)."""
from __future__ import annotations

from .base import SubprocessHarness
from .manager import HarnessManager

__all__ = ["HarnessManager", "SubprocessHarness"]
