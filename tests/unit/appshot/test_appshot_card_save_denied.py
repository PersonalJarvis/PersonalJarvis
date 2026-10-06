"""A denied Downloads folder says so on the card instead of "That did not work"."""

from __future__ import annotations

import io
import sys
from types import SimpleNamespace

import pytest
from PIL import Image

from jarvis.appshot import card_actions
from jarvis.appshot.store import Appshot, AppshotStore


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (8, 6), (1, 2, 3)).save(out, "PNG")
    return out.getvalue()


@pytest.fixture
def held(monkeypatch: pytest.MonkeyPatch) -> None:
    from jarvis.appshot import store as store_module

    store = AppshotStore()
    store.remember(
        Appshot(
            id="d0d0d0d0",
            image=_png(),
            mime="image/png",
            width=8,
            height=6,
            label="active window",
            app_name="Editor",
            note="APPSHOT: evidence",
            ui_text="",
            trigger="hotkey",
            taken_at=1.0,
        ),
        keep_s=60,
    )
    monkeypatch.setattr(store_module, "_STORE", store)
    monkeypatch.setattr(
        "jarvis.core.config.load_config", lambda: SimpleNamespace(ui=SimpleNamespace(language="en"))
    )

    def denied(_shot):
        raise PermissionError(1, "Operation not permitted")

    monkeypatch.setattr(card_actions, "save_shot_to_downloads", denied)


@pytest.mark.parametrize(
    ("platform", "status"),
    [
        ("darwin", "Allow Downloads in Privacy & Security > Files and Folders"),
        ("linux", "No permission to save in Downloads"),
    ],
)
async def test_a_denied_save_names_the_permission(
    held, monkeypatch: pytest.MonkeyPatch, platform: str, status: str
) -> None:
    monkeypatch.setattr(sys, "platform", platform)
    assert await card_actions.run_card_action("save") == status
