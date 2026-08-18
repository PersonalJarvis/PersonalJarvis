"""The drop-path bridge: what the shell knows about a drop reaches the page.

The desktop WebView is told the real path of a dropped file or folder by
pywebview (``pywebviewFullPath``); this bridge forwards it to the page as one
DOM event. It must never raise — a window without the DOM API (or a stub in
tests) simply keeps the browser fallback — and it must not report files whose
path the shell could not resolve.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from typing import Any

from jarvis.ui import native_drop


def test_paths_and_names_come_from_the_resolved_files_only() -> None:
    event = {
        "type": "drop",
        "dataTransfer": {
            "files": [
                {"name": "shop", "size": 0, "pywebviewFullPath": "C:\\Users\\x\\Desktop\\shop"},
                {"name": "unresolved.txt", "size": 3},
                "not-a-file",
                {"name": "notes", "pywebviewFullPath": "/home/x/notes"},
            ]
        },
    }

    paths, names = native_drop.dropped_paths(event)

    assert paths == ["C:\\Users\\x\\Desktop\\shop", "/home/x/notes"]
    assert names == ["shop", "notes"]


def test_an_event_without_files_yields_nothing() -> None:
    assert native_drop.dropped_paths({"type": "drop"}) == ([], [])
    assert native_drop.dropped_paths({"dataTransfer": "??"}) == ([], [])


def test_the_announcement_is_one_dom_event_with_the_paths_as_detail() -> None:
    script = native_drop.announce_script(["C:\\a b\\shop"], ["shop"])

    assert script.startswith("window.dispatchEvent(new CustomEvent(")
    assert json.dumps(native_drop.EVENT_NAME) in script
    # Backslashes and spaces survive because the detail is JSON, never string glue.
    assert json.dumps({"paths": ["C:\\a b\\shop"], "names": ["shop"]}) in script


class _FakeDomWindow:
    def __init__(self) -> None:
        self.handlers: dict[str, Any] = {}

    def on(self, event: str, handler: Any) -> None:
        self.handlers[event] = handler


class _FakeWindow:
    def __init__(self) -> None:
        self.dom = SimpleNamespace(window=_FakeDomWindow())
        self.evaluated: list[str] = []

    def evaluate_js(self, script: str) -> None:
        self.evaluated.append(script)


def test_registering_wires_a_drop_handler_that_tells_the_page() -> None:
    window = _FakeWindow()

    assert native_drop.register_native_drop(window) is True
    handler = window.dom.window.handlers["drop"]
    handler({"dataTransfer": {"files": [{"name": "shop", "pywebviewFullPath": "/p/shop"}]}})

    assert len(window.evaluated) == 1
    assert "/p/shop" in window.evaluated[0]


def test_a_drop_the_shell_could_not_resolve_stays_silent() -> None:
    """No paths, no event: the page's browser fallback (searching by name)
    must not be pre-empted by an announcement that says nothing."""
    window = _FakeWindow()
    native_drop.register_native_drop(window)

    window.dom.window.handlers["drop"]({"dataTransfer": {"files": [{"name": "shop"}]}})

    assert window.evaluated == []


def test_a_window_without_a_dom_bridge_is_a_quiet_no() -> None:
    assert native_drop.register_native_drop(SimpleNamespace()) is False


def test_a_failing_registration_never_raises() -> None:
    class _Broken:
        window = SimpleNamespace(on=lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("no js")))

    assert native_drop.register_native_drop(SimpleNamespace(dom=_Broken())) is False
