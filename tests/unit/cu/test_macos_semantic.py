"""Tests for the macOS semantic Accessibility action seam."""
from __future__ import annotations

from typing import Any

import pytest

from jarvis.cu import macos_semantic


def _reader(element: dict[str, Any], attribute: str) -> Any:
    return element.get(attribute)


def test_press_matches_named_parent_and_uses_axpress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    parent = {
        "AXTitle": "Save",
        "AXRole": "AXButton",
        "AXIdentifier": "save-button",
        "AXParent": None,
    }
    child = {
        "AXTitle": "",
        "AXRole": "AXStaticText",
        "AXParent": parent,
    }
    calls: list[tuple[object, str]] = []

    result = macos_semantic.try_press_at(
        60,
        40,
        expected_name="save",
        expected_role="Button",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: child,
        read_attr=_reader,
        action_names=lambda _element: ("AXPress",),
        perform_action=lambda element, action: calls.append((element, action)) or True,
    )

    assert result.status == "performed"
    assert calls == [(parent, "AXPress")]


def test_focus_edit_sets_axfocused(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {
        "AXTitle": "Search",
        "AXRole": "AXTextField",
        "AXIdentifier": "search-field",
        "AXParent": None,
    }
    writes: list[tuple[object, str, object]] = []

    result = macos_semantic.try_focus_at(
        10,
        10,
        expected_name="search",
        expected_role="Edit",
        expected_automation_id="search-field",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=_reader,
        attribute_settable=lambda _element, attr: attr == "AXFocused",
        set_attr=lambda target, attr, value: writes.append((target, attr, value)) or True,
    )

    assert result.status == "performed"
    assert writes == [(element, "AXFocused", True)]


def test_focus_non_settable_edit_allows_verified_pointer_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Search", "AXRole": "AXTextField", "AXParent": None}

    result = macos_semantic.try_focus_at(
        10,
        10,
        expected_name="search",
        expected_role="Edit",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=_reader,
        attribute_settable=lambda _element, _attr: False,
        set_attr=lambda *_args: pytest.fail("non-settable focus must not be written"),
    )

    assert result.status == "unsupported"


def test_focus_probe_error_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Search", "AXRole": "AXTextField", "AXParent": None}

    result = macos_semantic.try_focus_at(
        10,
        10,
        expected_name="search",
        expected_role="Edit",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=_reader,
        attribute_settable=lambda _element, _attr: None,
        set_attr=lambda *_args: pytest.fail("indeterminate focus must not be written"),
    )

    assert result.status == "unavailable"


def test_focus_write_failure_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Search", "AXRole": "AXTextField", "AXParent": None}

    result = macos_semantic.try_focus_at(
        10,
        10,
        expected_name="search",
        expected_role="Edit",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=_reader,
        attribute_settable=lambda _element, _attr: True,
        set_attr=lambda _element, _attr, _value: False,
    )

    assert result.status == "unavailable"


def test_focus_non_edit_is_not_used(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")

    result = macos_semantic.try_focus_at(
        10,
        10,
        expected_name="save",
        expected_role="Button",
        permission_check=lambda: pytest.fail("non-edit focus must stop before probing macOS"),
    )

    assert result.status == "unsupported"


def test_unsupported_action_is_the_only_pointer_fallback_status(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Search", "AXRole": "AXTextField", "AXParent": None}

    result = macos_semantic.try_press_at(
        10,
        10,
        expected_name="search",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=_reader,
        action_names=lambda _element: ("AXShowMenu",),
        perform_action=lambda _element, _action: pytest.fail("must not perform AXPress"),
    )

    assert result.status == "unsupported"


def test_identity_mismatch_fails_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Delete", "AXRole": "AXButton", "AXParent": None}

    result = macos_semantic.try_press_at(
        10,
        10,
        expected_name="save",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=_reader,
        action_names=lambda _element: ("AXPress",),
        perform_action=lambda _element, _action: pytest.fail("must not press a stale target"),
    )

    assert result.status == "mismatch"


def test_foreground_change_fails_before_action(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Save", "AXRole": "AXButton", "AXParent": None}

    result = macos_semantic.try_press_at(
        10,
        10,
        expected_name="save",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=_reader,
        action_names=lambda _element: ("AXPress",),
        perform_action=lambda _element, _action: pytest.fail("must not press after focus switch"),
        pre_action_check=lambda: False,
    )

    assert result.status == "mismatch"
    assert "foreground window changed" in result.detail


def test_missing_accessibility_permission_is_unavailable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")

    result = macos_semantic.try_press_at(
        10,
        10,
        expected_name="save",
        permission_check=lambda: False,
        element_at_point=lambda _x, _y: pytest.fail("must not resolve without permission"),
    )

    assert result.status == "unavailable"


def test_secure_text_field_never_reads_axvalue(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {
        "AXTitle": "Password",
        "AXRole": "AXSecureTextField",
        "AXValue": "secret-value",
        "AXParent": None,
    }
    reads: list[str] = []

    def reader(item: dict[str, Any], attribute: str) -> Any:
        reads.append(attribute)
        if attribute == "AXValue":
            pytest.fail("secure field value must not be read")
        return item.get(attribute)

    result = macos_semantic.try_press_at(
        10,
        10,
        expected_name="password",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=reader,
        action_names=lambda _element: (),
    )

    assert result.status == "unsupported"
    assert "AXValue" not in reads


def test_press_late_ownership_takeover_stops_before_axpress(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Save", "AXRole": "AXButton", "AXParent": None}

    class _Takeover(RuntimeError):
        pass

    def _takeover() -> None:
        raise _Takeover("user took input during AX lookup")

    with pytest.raises(_Takeover, match="user took input"):
        macos_semantic.try_press_at(
            10,
            10,
            expected_name="save",
            permission_check=lambda: True,
            element_at_point=lambda _x, _y: element,
            read_attr=_reader,
            action_names=lambda _element: ("AXPress",),
            ownership_check=_takeover,
            perform_action=lambda *_args: pytest.fail(
                "AXPress must not run after physical takeover"
            ),
        )


def test_focus_late_ownership_takeover_stops_before_axfocused(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Search", "AXRole": "AXTextField", "AXParent": None}

    class _Takeover(RuntimeError):
        pass

    def _takeover() -> None:
        raise _Takeover("user took input during AX focus probe")

    with pytest.raises(_Takeover, match="user took input"):
        macos_semantic.try_focus_at(
            10,
            10,
            expected_name="search",
            expected_role="Edit",
            permission_check=lambda: True,
            element_at_point=lambda _x, _y: element,
            read_attr=_reader,
            attribute_settable=lambda _element, _attr: True,
            ownership_check=_takeover,
            set_attr=lambda *_args: pytest.fail(
                "AXFocused must not be written after physical takeover"
            ),
        )


def test_focus_rechecks_foreground_after_settable_probe(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(macos_semantic.sys, "platform", "darwin")
    element = {"AXTitle": "Search", "AXRole": "AXTextField", "AXParent": None}
    checks = iter((True, False))

    result = macos_semantic.try_focus_at(
        10,
        10,
        expected_name="search",
        expected_role="Edit",
        permission_check=lambda: True,
        element_at_point=lambda _x, _y: element,
        read_attr=_reader,
        attribute_settable=lambda _element, _attr: True,
        pre_action_check=lambda: next(checks),
        set_attr=lambda *_args: pytest.fail(
            "AXFocused must not be written after the window changes"
        ),
    )

    assert result.status == "mismatch"
    assert "immediately before" in result.detail
