"""No agent-reachable tool may ask, reset or open settings for a macOS permission (P9).

The permission service's asking surface (``open_settings``, ``request_native``,
``note_reset``, a ``tccutil`` reset) belongs to people: a user gesture, a route behind
the dangerous-action header, or a dedicated consumer entry point. A tool an LLM can
call must never reach it, or an agent could answer a system dialog's question for the
user by itself. This is an AST contract over the tool modules, so a later tool or
plugin that imports the service and calls one of these fails here.

* Tool modules (``jarvis/plugins/tool``, ``jarvis/brain/tools``) never import the
  service. They reach permissions only through a consumer seam (``get_actuator``,
  ``screen_access``, the ``window_state`` helpers) that has its own gesture rules.
* The actuator seam (``jarvis/cu/actuate``) may import it in ``base.py`` only, and
  only to ``ensure_all`` / ``check`` Accessibility.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

_ROOT = Path(__file__).resolve().parents[3] / "jarvis"
_FORBIDDEN_NAMES = frozenset(
    {"open_settings", "request_native", "note_reset", "refresh_episodes", "attach_bus"}
)
_SERVICE_MODULE = "jarvis.platform.permission_service"
_TOOL_DIRS = ("plugins/tool", "brain/tools")


def _modules(subdir: str) -> list[Path]:
    return sorted(p for p in (_ROOT / subdir).rglob("*.py") if "__pycache__" not in p.parts)


def _imports_service(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module == _SERVICE_MODULE:
            return True
        if isinstance(node, ast.ImportFrom) and node.module == "jarvis.platform":
            if any(alias.name == "permission_service" for alias in node.names):
                return True
        if isinstance(node, ast.Import) and any(a.name == _SERVICE_MODULE for a in node.names):
            return True
    return False


def _forbidden_uses(tree: ast.AST) -> list[str]:
    found: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Attribute) and node.attr in _FORBIDDEN_NAMES:
            found.append(f"line {node.lineno}: .{node.attr}")
        elif isinstance(node, ast.Name) and node.id in _FORBIDDEN_NAMES:
            found.append(f"line {node.lineno}: {node.id}")
        elif isinstance(node, ast.Constant) and isinstance(node.value, str):
            if "tccutil" in node.value:
                found.append(f"line {node.lineno}: a tccutil reset")
    return found


def _rel(path: Path) -> str:
    return path.relative_to(_ROOT).as_posix()


def test_the_scan_covers_real_tool_modules():
    # A guard that scans an empty directory proves nothing.
    assert len(_modules("plugins/tool")) > 10
    assert (_ROOT / "cu" / "actuate" / "base.py").is_file()


@pytest.mark.parametrize("subdir", _TOOL_DIRS)
def test_tool_modules_never_import_the_permission_service(subdir):
    offenders = [
        _rel(path)
        for path in _modules(subdir)
        if _imports_service(ast.parse(path.read_text(encoding="utf-8")))
    ]

    assert offenders == [], f"an agent-reachable tool imports the permission service: {offenders}"


@pytest.mark.parametrize("subdir", [*_TOOL_DIRS, "cu/actuate"])
def test_no_tool_or_actuator_module_asks_resets_or_opens_settings(subdir):
    offenders = {}
    for path in _modules(subdir):
        uses = _forbidden_uses(ast.parse(path.read_text(encoding="utf-8")))
        if uses:
            offenders[_rel(path)] = uses

    assert offenders == {}


def test_only_the_actuator_base_imports_the_service_there_and_asks_for_accessibility_only():
    importers = [
        _rel(path)
        for path in _modules("cu/actuate")
        if _imports_service(ast.parse(path.read_text(encoding="utf-8")))
    ]
    assert importers == ["cu/actuate/base.py"]

    tree = ast.parse((_ROOT / "cu" / "actuate" / "base.py").read_text(encoding="utf-8"))
    called = {
        node.func.attr
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }
    assert {"ensure_all"} <= called
    assert not called & (_FORBIDDEN_NAMES | {"ensure_async"})
