"""GET /api/settings/assistant-name must never invent a name while warming up.

Under autostart the frontend can reach this route before ``app.state.config``
is populated. Resolving the name from a missing config would answer the
neutral "Assistant" fallback, which the frontend persists into its
localStorage name cache — freezing the wrong brand on every surface until a
successful re-fetch. The route answers 503 instead, so the seed hook retries.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from jarvis.ui.web.settings_routes import get_assistant_name


def _request_with_config(cfg: object) -> SimpleNamespace:
    return SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace(config=cfg, cfg=None)))


async def test_warmup_without_config_answers_503_not_a_fake_name() -> None:
    with pytest.raises(HTTPException) as excinfo:
        await get_assistant_name(_request_with_config(None))  # type: ignore[arg-type]
    assert excinfo.value.status_code == 503


async def test_resolves_name_from_wake_phrase_when_config_present() -> None:
    # Arbitrary pinned brand (never the host's live wake-word config).
    cfg = SimpleNamespace(
        trigger=SimpleNamespace(wake_word=SimpleNamespace(phrase="Hey Nova"))
    )
    payload = await get_assistant_name(_request_with_config(cfg))  # type: ignore[arg-type]
    assert payload["resolved"] == "Nova"
    assert payload["default"] == "Assistant"
