"""Regression guard: the boot-critical path must stay dependency-light.

The desktop/headless serve-first boot only stays fast because the
``FastBootstrap`` can bind the port and serve the UI shell BEFORE the heavy
imports (``fastapi``, the ``jarvis.brain`` graph, ``jarvis.core.config`` with
its brain/awareness pulls) are paid. If a future change makes
``jarvis.ui.web.fast_bootstrap`` transitively import any of those, the bind
moves back behind the heavy work and the boot silently regresses — the exact
"it keeps getting slower as I add features" rot this guards against.

This test imports the module in a FRESH interpreter (so it is deterministic and
machine-independent — no timing) and asserts the heavy modules are absent from
``sys.modules`` afterwards.
"""

from __future__ import annotations

import subprocess
import sys

# Modules that must NOT be pulled in merely by importing the bootstrap. Each is a
# multi-hundred-ms import that the serve-first boot deliberately defers.
_FORBIDDEN = ("fastapi", "jarvis.brain", "jarvis.core.config", "jarvis.awareness")


def test_fast_bootstrap_import_stays_light() -> None:
    code = (
        "import sys\n"
        "import jarvis.ui.web.fast_bootstrap  # noqa: F401\n"
        "bad = [m for m in %r if m in sys.modules]\n"
        "print('LEAKED=' + ','.join(bad))\n"
        "sys.exit(1 if bad else 0)\n"
    ) % (_FORBIDDEN,)
    proc = subprocess.run(
        [sys.executable, "-c", code],
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert proc.returncode == 0, (
        "The boot-critical bootstrap import pulled in heavy modules — this "
        "regresses cold-boot speed (serve-first relies on binding BEFORE these "
        f"are imported). {proc.stdout.strip()}\n{proc.stderr.strip()}"
    )
