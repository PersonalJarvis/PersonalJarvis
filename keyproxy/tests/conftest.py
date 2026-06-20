"""Make the repo root importable so ``import keyproxy`` works without install.

keyproxy is a standalone package run directly from the source tree (and from
its own Dockerfile via the working directory). When the tests are invoked as
``py -3.11 -m pytest keyproxy/`` from the repo root, the root is already on
``sys.path``; this conftest makes the suite robust to being run from anywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

_REPO_ROOT = Path(__file__).resolve().parents[2]
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))
