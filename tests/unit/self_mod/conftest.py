"""Preload jarvis.core.config before any self_mod test module imports.

There is a pre-existing import cycle: importing ``jarvis.core.self_mod`` first
pulls ``pending → writer → jarvis.core.config``, and config (via its section
models AckBrainConfig etc.) pulls ``brain → voice → echo_confirmation`` which
imports back from ``jarvis.core.self_mod`` while it is still initializing. In a
full test run config happens to load early; an isolated ``pytest
tests/unit/self_mod/`` run collects test_audit.py first and trips the cycle.

pytest imports this conftest before collecting the sibling test modules, so a
single import here loads config fully and breaks the cycle for the whole folder.
"""
from __future__ import annotations

import jarvis.core.config  # noqa: F401  (preload to satisfy the import order)
