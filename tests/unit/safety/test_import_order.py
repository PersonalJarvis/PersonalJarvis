"""Safety modules remain importable in fresh processes in any public order."""
from __future__ import annotations

import subprocess
import sys

import pytest

from jarvis.core.process_utils import NO_WINDOW_CREATIONFLAGS


@pytest.mark.parametrize(
    "statement",
    (
        "import jarvis.safety.approval; import jarvis.safety.tool_executor",
        "import jarvis.core.config; from jarvis.safety import ToolExecutor",
        "from jarvis.safety import ApprovalWorkflow; import jarvis.core.config",
        "from jarvis.safety import ActionBlocked, RiskTierEvaluator, TierDecision",
    ),
)
def test_safety_public_imports_are_order_independent(statement: str) -> None:
    completed = subprocess.run(
        [sys.executable, "-X", "utf8", "-c", statement],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=20,
        check=False,
        creationflags=NO_WINDOW_CREATIONFLAGS,
    )

    assert completed.returncode == 0, completed.stderr
