"""The [marketplace] config section (Wave 2, #2).

``public_callback_base_url`` switches OAuth redirect handlers from the loopback
callback server (desktop) to a hosted FastAPI callback (headless VPS). Empty =
loopback mode (the existing desktop behavior).
"""
from __future__ import annotations

from jarvis.core.config import JarvisConfig


def test_marketplace_section_defaults_to_empty() -> None:
    cfg = JarvisConfig()
    assert cfg.marketplace.public_callback_base_url == ""


def test_marketplace_public_callback_base_url_roundtrips() -> None:
    cfg = JarvisConfig.model_validate(
        {"marketplace": {"public_callback_base_url": "https://jarvis.example.com"}}
    )
    assert cfg.marketplace.public_callback_base_url == "https://jarvis.example.com"


def test_marketplace_section_allows_extra_keys() -> None:
    """AP-16: unknown keys must not break pre-validate after self-mod."""
    cfg = JarvisConfig.model_validate(
        {"marketplace": {"public_callback_base_url": "", "future_key": 1}}
    )
    assert cfg.marketplace.public_callback_base_url == ""
