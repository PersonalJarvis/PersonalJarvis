"""Top-level pytest config: --seeds option, source path injection, shared fixtures."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

_ROOT = Path(__file__).parent
_SRC = _ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        "--seeds",
        action="store",
        default="1",
        help="Number of deterministic seed values to parametrize seed-aware tests over.",
    )


@pytest.fixture(scope="session")
def seed_count(request: pytest.FixtureRequest) -> int:
    raw = request.config.getoption("--seeds")
    value = int(raw)
    if value < 1:
        raise ValueError("--seeds must be >= 1")
    return value


def pytest_generate_tests(metafunc: pytest.Metafunc) -> None:
    if "seed" in metafunc.fixturenames:
        raw = metafunc.config.getoption("--seeds")
        count = max(1, int(raw))
        metafunc.parametrize("seed", list(range(count)), ids=lambda v: f"seed{v}")
