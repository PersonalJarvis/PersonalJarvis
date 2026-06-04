"""connect_pat must validate the pasted token per auth_scheme:
  bearer        -> Authorization: Bearer <token>
  bot           -> Authorization: Bot <token>      (Discord)
  telegram_path -> token in the URL {token}, no header, body ok==true
"""

import httpx
import pytest

from jarvis.marketplace.catalog import PatPasteAuth, PluginSpec
from jarvis.ui.web import marketplace_routes as mr


def _spec(scheme: str, validation_endpoint: str) -> PluginSpec:
    return PluginSpec(
        id="x",
        display_name="X",
        description="d",
        category="Communication",
        logo_slug="x",
        auth=PatPasteAuth(
            mode="pat_paste",
            token_creation_url="https://x",
            token_prefix="",
            validation_endpoint=validation_endpoint,
            instruction_md="md",
            auth_scheme=scheme,
        ),
    )


def _capture_transport(captured, *, body=None, status=200):
    async def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["auth"] = request.headers.get("authorization")
        return httpx.Response(status, json=body if body is not None else {"ok": True})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_bearer_scheme_uses_bearer_header():
    captured = {}
    validate = mr._make_validator(_capture_transport(captured))
    ok, status = await validate(_spec("bearer", "https://api.example/me").auth, "tok123")
    assert ok is True and status == 200
    assert captured["auth"] == "Bearer tok123"
    assert captured["url"] == "https://api.example/me"


@pytest.mark.asyncio
async def test_bot_scheme_uses_bot_header():
    captured = {}
    validate = mr._make_validator(_capture_transport(captured))
    ok, _ = await validate(
        _spec("bot", "https://discord.com/api/v10/users/@me").auth, "abc.def.ghi"
    )
    assert ok is True
    assert captured["auth"] == "Bot abc.def.ghi"


@pytest.mark.asyncio
async def test_telegram_path_splices_token_and_sends_no_header():
    captured = {}
    validate = mr._make_validator(
        _capture_transport(captured, body={"ok": True})
    )
    ok, _ = await validate(
        _spec("telegram_path", "https://api.telegram.org/bot{token}/getMe").auth,
        "123:ABC",
    )
    assert ok is True
    assert "123:ABC" in captured["url"]
    assert captured["auth"] is None


@pytest.mark.asyncio
async def test_telegram_path_rejects_ok_false_even_on_200():
    captured = {}
    validate = mr._make_validator(
        _capture_transport(captured, body={"ok": False, "error_code": 401})
    )
    ok, _ = await validate(
        _spec("telegram_path", "https://api.telegram.org/bot{token}/getMe").auth,
        "bad",
    )
    assert ok is False
