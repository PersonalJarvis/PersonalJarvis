"""Cross-platform per-user config/cache locations for jarvisctl.

Uses platformdirs so the same code yields the right directory on Windows
(%LOCALAPPDATA%), macOS (~/Library), and Linux (XDG). Test/CI overrides via
JARVISCTL_CONFIG_HOME / JARVISCTL_CACHE_HOME so tests never touch real dirs.
"""
from __future__ import annotations

import os
from pathlib import Path

import platformdirs

_APP = "jarvisctl"


def config_dir() -> Path:
    override = os.environ.get("JARVISCTL_CONFIG_HOME")
    base = Path(override) if override else Path(platformdirs.user_config_dir(_APP))
    base.mkdir(parents=True, exist_ok=True)
    return base


def cache_dir() -> Path:
    override = os.environ.get("JARVISCTL_CACHE_HOME")
    base = Path(override) if override else Path(platformdirs.user_cache_dir(_APP))
    base.mkdir(parents=True, exist_ok=True)
    return base


def config_file() -> Path:
    return config_dir() / "config.json"


def openapi_cache_file() -> Path:
    return cache_dir() / "openapi.json"


def openapi_meta_file() -> Path:
    return cache_dir() / "openapi.meta.json"
