"""Depth-free rescue evidence for click and type verification (2026-07-21).

The walked UIA tree is depth- and node-capped, so Chrome nests its omnibox
below what the walk can reach. Real clicks into an already-focused address
bar and real typed URLs were then declared misses ("no visible change" /
"did NOT land in any editable field"), which beheaded every click->type
batch on Windows browsers. These tests pin the two depth-free evidence
paths (native focused-element probe, popup-open detection) that rescue
those verdicts.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from jarvis.cu.engine import _new_popup_near_click
from jarvis.cu.verify import verify_click_focus_point, verify_typed_text


def _elem(
    *,
    role: str = "Edit",
    value: str = "",
    bounds: tuple[int, int, int, int] = (0, 0, 0, 0),
    focused: bool | None = True,
) -> SimpleNamespace:
    return SimpleNamespace(role=role, value=value, bounds=bounds, focused=focused)


class _EmptySource:
    async def observe(self):
        return SimpleNamespace(nodes=())


class _FailingSource:
    async def observe(self):
        raise RuntimeError("no tree on this surface")


def _wire(
    monkeypatch: pytest.MonkeyPatch,
    *,
    source: object | None = None,
    focused: SimpleNamespace | None = None,
    hit: SimpleNamespace | None = None,
) -> None:
    monkeypatch.setattr(
        "jarvis.cu.verify._get_ui_tree_source",
        lambda: source if source is not None else _FailingSource(),
    )
    monkeypatch.setattr(
        "jarvis.cu.verify._query_focused_element", lambda: focused,
    )
    monkeypatch.setattr(
        "jarvis.cu.verify._get_pointer_resolver",
        lambda: SimpleNamespace(at=lambda x, y: hit),
    )


class TestVerifyTypedTextFocusedRescue:
    @pytest.mark.asyncio
    async def test_focused_element_value_upgrades_unknown_to_true(self, monkeypatch):
        # Walk sees nothing (omnibox below depth); the native focused-element
        # probe reads the typed URL straight off the control.
        _wire(monkeypatch, source=_EmptySource(),
              focused=_elem(value="https://youtube.com/feed"))
        assert await verify_typed_text("youtube.com") is True

    @pytest.mark.asyncio
    async def test_focused_element_value_overrides_walked_false(self, monkeypatch):
        # The walk found SOME focused editable without the text (a web-page
        # Document), but the real focus target holds it — the click->type
        # batch must not be failed on the shallow read.
        class _WrongSurface:
            async def observe(self):
                node = SimpleNamespace(
                    role="Document", focused=True, value="unrelated page text",
                )
                return SimpleNamespace(nodes=(node,))

        _wire(monkeypatch, source=_WrongSurface(),
              focused=_elem(value="youtube.com"))
        assert await verify_typed_text("youtube.com") is True

    @pytest.mark.asyncio
    async def test_probe_never_manufactures_a_false(self, monkeypatch):
        # A non-matching focused value is NOT proof of a miss (an omnibox may
        # render the highlighted autocomplete suggestion): the walked verdict
        # stands.
        _wire(monkeypatch, source=_EmptySource(), focused=_elem(value="other"))
        assert await verify_typed_text("youtube.com") is None

    @pytest.mark.asyncio
    async def test_no_probe_keeps_walked_verdict(self, monkeypatch):
        _wire(monkeypatch, source=_EmptySource(), focused=None)
        assert await verify_typed_text("youtube.com") is None


class TestVerifyClickFocusPointFocusedRescue:
    @pytest.mark.asyncio
    async def test_container_hit_rescued_by_focused_bounds(self, monkeypatch):
        # ElementFromPoint degrades to a container (Chromium Pane) — but the
        # focused control's own bounds contain the click point: the click
        # landed in the already-focused target (BUG-038 regression guard).
        _wire(
            monkeypatch,
            hit=_elem(role="Pane", bounds=(0, 0, 1920, 1080)),
            focused=_elem(role="Edit", bounds=(100, 40, 800, 36)),
        )
        assert await verify_click_focus_point(
            400, 58, capture_area=1920 * 1080,
        ) is True

    @pytest.mark.asyncio
    async def test_focused_bounds_missing_point_is_no_rescue(self, monkeypatch):
        _wire(
            monkeypatch,
            hit=_elem(role="Pane", bounds=(0, 0, 1920, 1080)),
            focused=_elem(role="Edit", bounds=(100, 40, 800, 36)),
        )
        assert await verify_click_focus_point(
            400, 600, capture_area=1920 * 1080,
        ) is None

    @pytest.mark.asyncio
    async def test_focused_container_role_never_rescues(self, monkeypatch):
        _wire(
            monkeypatch,
            hit=None,
            focused=_elem(role="Document", bounds=(0, 0, 1920, 1080)),
        )
        assert await verify_click_focus_point(
            400, 58, capture_area=1920 * 1080,
        ) is None


class TestNewPopupNearClick:
    def test_new_popup_at_click_point_is_evidence(self):
        assert _new_popup_near_click(
            frozenset(), ((501, (410, 210, 260, 380)),), (400, 200),
        ) is True

    def test_preexisting_popup_never_counts(self):
        assert _new_popup_near_click(
            frozenset({501}), ((501, (410, 210, 260, 380)),), (400, 200),
        ) is False

    def test_far_away_popup_is_unrelated_churn(self):
        assert _new_popup_near_click(
            frozenset(), ((501, (3000, 1800, 260, 380)),), (100, 100),
        ) is False

    def test_no_popups_is_no_evidence(self):
        assert _new_popup_near_click(frozenset(), (), (400, 200)) is False
