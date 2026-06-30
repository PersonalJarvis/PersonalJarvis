"""Bring-your-own OAuth client secret slots.

The Google/Slack/Asana ``*_oauth_client_id`` / ``*_oauth_client_secret`` secrets
let a downloader run their OWN production OAuth app (the only durable fix for
provider-side refresh-token expiry — e.g. a Google "Testing" app drops its
refresh token after 7 days). They must be:

  * storable through the API (whitelisted via the wizard's ``SECRETS`` list), and
  * NOT asked in the interactive setup wizard (advanced + optional; entered only
    from the Plugins UI), so onboarding stays short.
"""
from __future__ import annotations

from unittest.mock import patch

from jarvis.setup.wizard import SECRETS, step_api_keys

_OAUTH_CLIENT_KEYS = frozenset(
    {
        "google_oauth_client_id",
        "google_oauth_client_secret",
        "slack_oauth_client_id",
        "slack_oauth_client_secret",
        "asana_oauth_client_id",
        "asana_oauth_client_secret",
    }
)


def test_oauth_client_slots_declared() -> None:
    keys = {s.key for s in SECRETS}
    assert _OAUTH_CLIENT_KEYS <= keys


def test_oauth_client_slots_are_optional_and_not_prompted() -> None:
    by_key = {s.key: s for s in SECRETS}
    for key in _OAUTH_CLIENT_KEYS:
        spec = by_key[key]
        assert spec.optional is True, f"{key} must be optional"
        assert spec.prompt is False, f"{key} must not be prompted in the wizard"


def test_oauth_client_keys_are_api_writable() -> None:
    # ALLOWED_SECRET_KEYS is derived from SECRETS, so the POST /api/secrets/{key}
    # route accepts the new keys (200) instead of rejecting them (404).
    from jarvis.ui.web.provider_routes import ALLOWED_SECRET_KEYS

    assert _OAUTH_CLIENT_KEYS <= ALLOWED_SECRET_KEYS


def test_step_api_keys_skips_non_prompt_slots() -> None:
    # The interactive wizard asks once per prompted secret and NEVER for a
    # prompt=False slot — proving the 6 advanced OAuth-client slots don't bloat
    # onboarding.
    asked: list[str] = []

    def _record(prompt: str, default: str | None = None) -> str:
        asked.append(prompt)
        return ""  # user skips every prompt

    with (
        patch("jarvis.setup.wizard._ask", side_effect=_record),
        patch("jarvis.setup.wizard.cfg.get_secret", return_value=None),
        patch("jarvis.setup.wizard._println"),
    ):
        step_api_keys()

    assert len(asked) == sum(1 for s in SECRETS if s.prompt)
