"""Routing cache in front of ``resolve_provider_endpoint``.

This is the second half of the 2026-07-28 freeze. The first half was the TOML
parse (``test_config_toml_cache.py``); this is the ``JarvisConfig(**data)``
rebuild behind it — 281 fresh objects on every provider client build, on the
event loop, several times per turn. Two independent sessions caught the backend
thread ``active+gil`` inside exactly that call, and while it was held the
Tk-drawn overlay stopped pumping and Windows swapped the frozen window for a
``Ghost`` (BUG-118).

Two things must never regress here, and they pull in opposite directions: the
routing decision has to be remembered, and the CREDENTIAL must not be — a key
the user repairs in the UI has to take effect on the next call, not the next
restart.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from jarvis.core import config as config_module
from jarvis.core.config import clear_config_cache, resolve_provider_endpoint


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    """Point the resolver at a config of our own, and start from cold."""
    target = tmp_path / "jarvis.toml"
    target.write_text('[brain]\nprimary = "gemini"\n', encoding="utf-8")
    monkeypatch.setattr(config_module, "resolve_config_path", lambda: target)
    clear_config_cache()
    yield target
    clear_config_cache()


def _write(path: Path, body: str, *, mtime_ns: int | None = None) -> None:
    path.write_text(body, encoding="utf-8")
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def test_the_config_model_is_not_rebuilt_on_a_repeat_call(monkeypatch):
    """The whole point: a second call must not go near ``load_config``."""
    resolve_provider_endpoint("claude-api")  # warm

    calls: list[int] = []
    real = config_module.load_config

    def counting(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(config_module, "load_config", counting)
    resolve_provider_endpoint("claude-api")
    assert calls == [], "a cached route must not rebuild the config model"


def test_the_credential_is_never_cached(monkeypatch):
    """A repaired key must work on the NEXT call, not the next restart."""
    seen: list[str] = []

    def fake_secret(provider_id: str) -> str | None:
        seen.append(provider_id)
        return f"key-{len(seen)}"

    monkeypatch.setattr(config_module, "get_provider_secret", fake_secret)

    first = resolve_provider_endpoint("claude-api")
    second = resolve_provider_endpoint("claude-api")

    assert first.credential == "key-1"
    assert second.credential == "key-2", "the credential was served from a cache"
    assert len(seen) == 2


def test_a_changed_base_url_takes_effect(_clean):
    """Editing the config must still change where traffic goes."""
    target = _clean
    assert resolve_provider_endpoint("openrouter").base_url is None

    _write(
        target,
        '[brain.providers.openrouter]\nbase_url = "https://proxy.invalid/v1"\n',
        mtime_ns=10_000_000_000,
    )

    assert (
        resolve_provider_endpoint("openrouter").base_url == "https://proxy.invalid/v1"
    )


def test_clear_drops_the_route_as_well(_clean):
    """``config_writer`` announces its writes — that must reach this cache too."""
    target = _clean
    assert resolve_provider_endpoint("openrouter").base_url is None

    # Same identity as far as the cache can tell: only clearing can save it.
    _write(target, '[brain.providers.openrouter]\nbase_url = "https://b.invalid"\n')
    os.utime(target, ns=(0, 0))
    clear_config_cache()

    assert resolve_provider_endpoint("openrouter").base_url == "https://b.invalid"


def test_the_vendor_default_is_part_of_the_key(_clean):
    """Two callers with different defaults must not be served each other's."""
    a = resolve_provider_endpoint("openrouter", vendor_default_base_url="https://a.invalid")
    b = resolve_provider_endpoint("openrouter", vendor_default_base_url="https://b.invalid")
    assert a.base_url == "https://a.invalid"
    assert b.base_url == "https://b.invalid"


def test_providers_do_not_share_an_entry(_clean):
    """Keyed per provider — one override must not leak onto another."""
    target = _clean
    _write(
        target,
        '[brain.providers.openrouter]\nbase_url = "https://only-openrouter.invalid"\n',
        mtime_ns=10_000_000_000,
    )
    assert (
        resolve_provider_endpoint("openrouter").base_url
        == "https://only-openrouter.invalid"
    )
    assert resolve_provider_endpoint("gemini").base_url is None


def test_team_proxy_still_flips_every_provider(_clean, monkeypatch):
    """Team mode is config-derived too, so it must survive the cache."""
    target = _clean
    _write(
        target,
        "[team_proxy]\nenabled = true\nurl = \"https://team.invalid/\"\n"
        "local_providers = [\"ollama\"]\n",
        mtime_ns=10_000_000_000,
    )
    monkeypatch.setattr(config_module, "get_secret", lambda *a, **k: "team-token")

    routed = resolve_provider_endpoint("claude-api")
    assert routed.via_proxy is True
    assert routed.base_url == "https://team.invalid/p/claude-api"
    assert routed.credential == "team-token"

    # A local provider is exempt and must not be routed through the proxy.
    local = resolve_provider_endpoint("ollama")
    assert local.via_proxy is False


def test_an_explicit_config_bypasses_the_cache(_clean):
    """The test seam still answers from what it was handed, not from the file."""
    from jarvis.core.config import JarvisConfig

    resolve_provider_endpoint("openrouter")  # populate the cache from the file

    supplied = JarvisConfig(
        **{"brain": {"providers": {"openrouter": {"base_url": "https://given.invalid"}}}}
    )
    assert (
        resolve_provider_endpoint("openrouter", config=supplied).base_url
        == "https://given.invalid"
    )
    # ...and doing so must not have poisoned the cached answer for everyone else.
    assert resolve_provider_endpoint("openrouter").base_url is None
