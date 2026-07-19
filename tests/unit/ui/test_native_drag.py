from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from jarvis.ui import native_drag


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (
            [
                "jarvis-file-drag",
                {"files": ["/Downloads/one.txt", "/Downloads/two.txt"]},
            ],
            ["/Downloads/one.txt", "/Downloads/two.txt"],
        ),
        (
            '["jarvis-file-drag", {"files": ["/Downloads/one.txt"]}]',
            ["/Downloads/one.txt"],
        ),
        (["jarvis-file-drag", {"files": "not-a-list"}], []),
        (["another-message", {"files": ["/Downloads/one.txt"]}], None),
        ("not-json", None),
    ],
)
def test_drag_files_from_message(
    message: object,
    expected: list[str] | None,
) -> None:
    assert native_drag._drag_files_from_message(message) == expected  # noqa: SLF001


def test_validate_paths_keeps_only_regular_files_inside_allowed_bases(
    tmp_path: Path,
) -> None:
    downloads = tmp_path / "Downloads"
    downloads.mkdir()
    allowed = downloads / "session.txt"
    allowed.write_text("transcript", encoding="utf-8")
    outside = tmp_path / "private.txt"
    outside.write_text("private", encoding="utf-8")

    paths = native_drag._validate_paths(  # noqa: SLF001
        [str(allowed), str(outside), str(downloads / "missing.txt")],
        native_drag._resolve_bases([downloads]),  # noqa: SLF001
    )

    assert paths == [str(allowed.resolve())]


def test_install_routes_macos_to_the_appkit_backend(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    seen: list[list[Path] | None] = []
    monkeypatch.setattr(native_drag.sys, "platform", "darwin")
    monkeypatch.setattr(
        native_drag,
        "_install_macos_drag",
        lambda bases: seen.append(list(bases) if bases is not None else None) or True,
    )

    assert native_drag.install_native_drag([tmp_path]) is True
    assert seen == [[tmp_path]]


def test_install_is_an_honest_noop_without_a_desktop_drag_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(native_drag.sys, "platform", "linux")

    assert native_drag.install_native_drag() is False


def test_begin_macos_drag_publishes_file_urls_with_copy_session() -> None:
    created_items: list[Any] = []

    class FakeDraggingItem:
        @classmethod
        def alloc(cls) -> FakeDraggingItem:
            item = cls()
            created_items.append(item)
            return item

        def initWithPasteboardWriter_(self, writer: object) -> FakeDraggingItem:
            self.writer = writer
            return self

        def setDraggingFrame_contents_(self, frame: object, contents: object) -> None:
            self.frame = frame
            self.contents = contents

    class FakeSession:
        def __init__(self) -> None:
            self.animates_back = False

        def setAnimatesToStartingPositionsOnCancelOrFail_(self, value: bool) -> None:
            self.animates_back = value

    class FakeWebView:
        def __init__(self) -> None:
            self.session = FakeSession()
            self.started: tuple[list[Any], object, object] | None = None

        def convertPoint_fromView_(self, point: object, source_view: object) -> Any:
            assert point == "window-point"
            assert source_view is None
            return SimpleNamespace(x=100.0, y=80.0)

        def beginDraggingSessionWithItems_event_source_(
            self,
            items: list[Any],
            event: object,
            source: object,
        ) -> FakeSession:
            self.started = (items, event, source)
            return self.session

    event = SimpleNamespace(locationInWindow=lambda: "window-point")
    source = object()
    webview = FakeWebView()
    appkit = SimpleNamespace(
        NSDraggingItem=FakeDraggingItem,
        NSWorkspace=SimpleNamespace(
            sharedWorkspace=lambda: SimpleNamespace(
                iconForFile_=lambda path: f"icon:{path}"
            )
        ),
        NSMakeRect=lambda x, y, width, height: (x, y, width, height),
    )
    foundation = SimpleNamespace(
        NSURL=SimpleNamespace(fileURLWithPath_=lambda path: f"file-url:{path}")
    )

    started = native_drag._begin_macos_drag(  # noqa: SLF001
        webview,
        ["/Downloads/one.txt", "/Downloads/two.txt"],
        event,
        source,
        appkit=appkit,
        foundation=foundation,
    )

    assert started is True
    assert [item.writer for item in created_items] == [
        "file-url:/Downloads/one.txt",
        "file-url:/Downloads/two.txt",
    ]
    assert created_items[0].frame == (84.0, 64.0, 32.0, 32.0)
    assert created_items[1].frame == (87.0, 61.0, 32.0, 32.0)
    assert webview.started == (created_items, event, source)
    assert webview.session.animates_back is True


def test_macos_bridge_intercepts_only_fresh_physical_drag_messages(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class FakeNSObject:
        def __init_subclass__(cls, **_kwargs: object) -> None:
            super().__init_subclass__()

        @classmethod
        def alloc(cls) -> FakeNSObject:
            return cls()

        def init(self) -> FakeNSObject:
            return self

    class FakeHost:
        def __init__(self) -> None:
            self.handlers: dict[str, object] = {}

        def mouseDown_(self, event: object) -> str:
            self.original_mouse_event = event
            return "mouse-down"

        def configuration(self) -> Any:
            return SimpleNamespace(
                userContentController=lambda: SimpleNamespace(
                    addScriptMessageHandler_name_=lambda handler, name: self.handlers.__setitem__(
                        name,
                        handler,
                    )
                )
            )

    class FakeBrowserView:
        WebKitHost = FakeHost

        def __init__(self, webview: FakeHost) -> None:
            self.webview = webview

    cocoa = ModuleType("webview.platforms.cocoa")
    cocoa.BrowserView = FakeBrowserView  # type: ignore[attr-defined]
    platforms = ModuleType("webview.platforms")
    platforms.__path__ = []  # type: ignore[attr-defined]
    platforms.cocoa = cocoa  # type: ignore[attr-defined]
    webview_module = ModuleType("webview")
    webview_module.__path__ = []  # type: ignore[attr-defined]
    webview_module.platforms = platforms  # type: ignore[attr-defined]
    appkit = ModuleType("AppKit")
    appkit.NSObject = FakeNSObject  # type: ignore[attr-defined]
    appkit.NSDragOperationCopy = 1  # type: ignore[attr-defined]
    appkit.NSEvent = SimpleNamespace(pressedMouseButtons=lambda: 1)  # type: ignore[attr-defined]
    foundation = ModuleType("Foundation")
    objc = ModuleType("objc")
    objc.protocolNamed = lambda _name: object()  # type: ignore[attr-defined]

    monkeypatch.setitem(sys.modules, "AppKit", appkit)
    monkeypatch.setitem(sys.modules, "Foundation", foundation)
    monkeypatch.setitem(sys.modules, "objc", objc)
    monkeypatch.setitem(sys.modules, "webview", webview_module)
    monkeypatch.setitem(sys.modules, "webview.platforms", platforms)
    monkeypatch.setitem(sys.modules, "webview.platforms.cocoa", cocoa)
    monkeypatch.setattr(native_drag.time, "monotonic", lambda: 10.0)

    saved_file = tmp_path / "session.txt"
    saved_file.write_text("transcript", encoding="utf-8")
    started: list[tuple[object, list[str], object, object]] = []
    monkeypatch.setattr(
        native_drag,
        "_begin_macos_drag",
        lambda webview, paths, event, source, **_kwargs: started.append(
            (webview, list(paths), event, source)
        )
        or True,
    )

    assert native_drag._install_macos_drag([tmp_path]) is True  # noqa: SLF001
    host = FakeHost()
    browser = FakeBrowserView(host)
    handler = host.handlers[native_drag.MACOS_MESSAGE_HANDLER]
    assert browser._jarvis_drag_message_handler is handler  # type: ignore[attr-defined]
    mouse_event = object()
    assert host.mouseDown_(mouse_event) == "mouse-down"

    class DragMessage:
        @staticmethod
        def body() -> list[object]:
            return [native_drag.MESSAGE_TAG, {"files": [str(saved_file)]}]

        @staticmethod
        def webView() -> FakeHost:  # noqa: N802 - WKScriptMessage-compatible fake
            return host

    assert (
        handler.userContentController_didReceiveScriptMessage_(None, DragMessage())
        is None
    )
    assert len(started) == 1
    assert started[0][:3] == (host, [str(saved_file.resolve())], mouse_event)

    unrelated = SimpleNamespace(body=lambda: "print")
    assert handler.userContentController_didReceiveScriptMessage_(None, unrelated) is None
    assert len(started) == 1
