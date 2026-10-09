"""The plain-text secret request check behind the credential guard."""

from __future__ import annotations

import pytest

from jarvis.agent_chat.secret_requests import asks_for_secret, claims_stored_secret

ASKS = [
    # Replies seen from real agents before the guard existed.
    "Bitte melde dich im Discord-Tab meines Browsers an; Passwörter oder Bot-Tokens gehören nicht in den Chat.",  # noqa: E501  i18n-allow
    "Bitte gib den Token ausschließlich in ein dafür vorgesehenes sicheres Eingabefeld ein, niemals hier im Chat.",  # noqa: E501  i18n-allow
    "Please paste your Discord bot token here so I can connect the bot.",
    "I need your OpenAI API key to finish the app.",
    "Do you have a GitHub personal access token?",
    "Schick mir bitte deinen API-Schlüssel.",  # i18n-allow
    "Por favor, envíame la contraseña de la base de datos.",  # i18n-allow
    "请提供你的 API 密钥。",  # i18n-allow
]

QUIET = [
    "The bot reads DISCORD_BOT_TOKEN from the environment and is running.",
    "Ich habe die App gebaut; sie liest den Schlüssel aus OPENAI_API_KEY.",  # i18n-allow
    "Done: the summary is in report.md.",
    "A bot token identifies your Discord application.",
    "Der Discord-Bot-Token ist gespeichert und Discord hat ihn akzeptiert.",  # i18n-allow
]


@pytest.mark.parametrize("text", ASKS)
def test_a_plain_text_request_for_a_secret_is_spotted(text: str) -> None:
    assert asks_for_secret(text)


@pytest.mark.parametrize("text", QUIET)
def test_using_or_explaining_a_secret_is_not_a_request(text: str) -> None:
    assert not asks_for_secret(text)


def test_a_claim_that_a_secret_is_stored_is_spotted() -> None:
    assert claims_stored_secret("Der Bot-Token ist bereits sicher gespeichert.")  # i18n-allow
    assert claims_stored_secret("Your API key is already stored.")
    assert not claims_stored_secret("The bot is online.")
