"""Web-search secret slot: prepaid Apifare, not Tavily/Serper/Brave."""

from jarvis.setup.wizard import SECRETS
from jarvis.ui.web.provider_routes import ALLOWED_SECRET_KEYS


def test_search_secret_is_optional_apifare_not_tavily() -> None:
    keys = {spec.key for spec in SECRETS}
    assert "tavily_api_key" not in keys
    spec = next(item for item in SECRETS if item.key == "apifare_api_key")
    assert spec.env_fallback == "APIPAY_TOKEN"
    assert spec.optional is True
    assert spec.section == "tools"
    assert spec.help_url.startswith("https://apifare.com/")
    assert "apifare_api_key" in ALLOWED_SECRET_KEYS
    assert "tavily_api_key" not in ALLOWED_SECRET_KEYS
