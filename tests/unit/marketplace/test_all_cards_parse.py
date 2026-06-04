from jarvis.marketplace.catalog_data import load_catalog
from jarvis.marketplace.usage_cards.loader import load_usage_card


def test_every_live_callable_plugin_has_a_parsable_card():
    """A plugin with an MCP-capable transport (http/stdio — the live-callable
    ones) must ship a usage card with keywords + body. rest_wrapper / no-mcp
    plugins are not live-callable, so they are exempt."""
    missing = []
    for p in load_catalog().plugins:
        mcp = p.mcp_server or {}
        if str(mcp.get("transport", "")).lower() not in ("http", "stdio"):
            continue
        card = load_usage_card(p.id)
        if card is None or not card.keywords or not card.body:
            missing.append(p.id)
    assert missing == [], f"live-callable plugins missing a usable card: {missing}"
