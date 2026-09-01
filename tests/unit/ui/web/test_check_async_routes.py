"""The blocking-route ratchet: what counts, what does not, how it ratchets."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

_GATE = Path(__file__).resolve().parents[4] / "scripts" / "ci" / "check_async_routes.py"
_spec = importlib.util.spec_from_file_location("check_async_routes", _GATE)
assert _spec is not None and _spec.loader is not None
gate = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("check_async_routes", gate)
_spec.loader.exec_module(gate)


def _routes(tmp_path: Path, source: str) -> list[tuple[int, str]]:
    path = tmp_path / "x_routes.py"
    path.write_text(source, encoding="utf-8")
    return gate.blocking_routes(path)


def test_an_async_route_with_no_await_is_counted(tmp_path: Path) -> None:
    found = _routes(
        tmp_path,
        "@router.get('/a')\nasync def a():\n    return open('f').read()\n",
    )
    assert [name for _line, name in found] == ["a"]


def test_awaiting_async_iterating_and_streaming_routes_pass(tmp_path: Path) -> None:
    source = (
        "@router.get('/a')\nasync def a():\n    await x()\n"
        "@router.websocket('/w')\nasync def w(ws):\n    async for m in ws:\n        pass\n"
        "@router.get('/s')\nasync def s():\n    yield b''\n"
        "@router.post('/c')\nasync def c():\n    async with x():\n        pass\n"
    )
    assert _routes(tmp_path, source) == []


def test_a_plain_def_route_is_not_this_gates_business(tmp_path: Path) -> None:
    assert _routes(tmp_path, "@router.get('/a')\ndef a():\n    return 1\n") == []


def test_an_undecorated_async_helper_is_not_counted(tmp_path: Path) -> None:
    assert _routes(tmp_path, "async def helper():\n    return 1\n") == []


def test_an_await_inside_a_nested_helper_does_not_excuse_the_handler(
    tmp_path: Path,
) -> None:
    source = (
        "@router.get('/a')\n"
        "async def a():\n"
        "    async def inner():\n"
        "        await x()\n"
        "    return 1\n"
    )
    assert [name for _line, name in _routes(tmp_path, source)] == ["a"]


def test_the_ratchet_flags_growth_and_tolerates_shrinkage() -> None:
    baseline = {"a.py": 3, "b.py": 1}
    current = {"a.py": 4, "b.py": 0, "c.py": 1}
    assert gate.regressions(current, baseline) == {"a.py": (3, 4), "c.py": (0, 1)}
    assert gate.regressions({"a.py": 2}, baseline) == {}


def test_the_live_tree_is_within_its_baseline() -> None:
    """The real gate, run as a test: no file may exceed its recorded count."""
    assert gate.regressions(gate.scan(), gate.load_baseline()) == {}
