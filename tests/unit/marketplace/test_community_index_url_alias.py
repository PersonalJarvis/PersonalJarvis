"""A saved address of the registry's former Pages host reads the current one."""

from __future__ import annotations

from types import SimpleNamespace

import jarvis.core.config as config
from jarvis.core.config import MarketplaceConfig
from jarvis.marketplace import community_source


def _configured(monkeypatch, url: str) -> None:
    loaded = SimpleNamespace(marketplace=SimpleNamespace(community_index_url=url))
    monkeypatch.setattr(config, "load_config", lambda: loaded)


def test_the_retired_pages_address_reads_the_current_deployment(monkeypatch) -> None:
    _configured(monkeypatch, "https://personaljarvis.github.io/marketplace/index.json")
    assert community_source.index_url() == MarketplaceConfig().community_index_url


def test_a_custom_mirror_and_an_empty_value_are_kept(monkeypatch) -> None:
    _configured(monkeypatch, " https://mirror.example/index.json ")
    assert community_source.index_url() == "https://mirror.example/index.json"
    _configured(monkeypatch, "")
    assert community_source.index_url() == ""
