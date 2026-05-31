"""Repo-Root-conftest fuer Pytest-Discovery.

Pytest laedt diese Datei VOR allen Test-Modulen und VOR `tests/conftest.py`.
Wir nutzen das, um den Repo-Root in `sys.path` aufzunehmen, damit Tests
Top-Level-Module wie `ui.orb.bus_bridge` importieren koennen.
"""
from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
