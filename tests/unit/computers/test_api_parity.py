"""Every field of the computer records reaches the TypeScript client (AP-4).

The REST rows are ``model_dump`` of these models; ``lib/computersApi.ts``
declares the same shapes by hand. A field added on one side only is a value
the UI silently never sees (or reads as ``undefined``), so the two sides are
compared name for name.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from pydantic import BaseModel

from jarvis.computers.models import Computer, ComputerFacts, ComputerHealth, ComputerRoute

TS_FILE = Path(__file__).resolve().parents[3] / "jarvis/ui/web/frontend/src/lib/computersApi.ts"


def _ts_fields(interface: str) -> set[str]:
    text = TS_FILE.read_text(encoding="utf-8")
    match = re.search(rf"export interface {interface} \{{(.*?)\n\}}", text, re.S)
    assert match, f"interface {interface} not found in computersApi.ts"
    body = re.sub(r"/\*.*?\*/|//[^\n]*", "", match.group(1), flags=re.S)
    return set(re.findall(r"^\s*([a-z_][a-z0-9_]*)\??:", body, re.M))


@pytest.mark.parametrize(
    ("model", "interface"),
    [
        (Computer, "Computer"),
        (ComputerHealth, "ComputerHealth"),
        (ComputerFacts, "ComputerFacts"),
        (ComputerRoute, "ComputerRoute"),
    ],
)
def test_python_and_typescript_shapes_match(model: type[BaseModel], interface: str) -> None:
    python = set(model.model_fields)
    typescript = _ts_fields(interface)
    # Added by the route on top of the record (``_row``), not model fields.
    typescript -= {"busy", "provider_name"}

    assert python - typescript == set(), f"missing in TypeScript: {python - typescript}"
    assert typescript - python == set(), f"missing in Python: {typescript - python}"
