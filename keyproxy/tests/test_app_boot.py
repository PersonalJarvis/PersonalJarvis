"""Boot-time HTTPS posture + ENV-driven app construction."""

from __future__ import annotations

import pytest

from keyproxy.app import (
    InsecureStartupError,
    _https_posture_ok,
    build_app_from_env,
)
from keyproxy.config import load_config


def _base_env() -> dict[str, str]:
    return {
        "KEYPROXY_ADMIN_KEY": "admin",
        "KEYPROXY_OPENAI_KEY": "sk-real",
        "KEYPROXY_DB_PATH": ":memory:",
    }


def test_refuses_insecure_boot_without_optout() -> None:
    env = _base_env()  # no TLS_TERMINATED, no ALLOW_INSECURE
    with pytest.raises(InsecureStartupError):
        build_app_from_env(env)


def test_allows_boot_with_tls_terminated() -> None:
    env = _base_env() | {"KEYPROXY_TLS_TERMINATED": "1"}
    app = build_app_from_env(env)
    assert app is not None


def test_allows_boot_with_allow_insecure() -> None:
    env = _base_env() | {"KEYPROXY_ALLOW_INSECURE": "1"}
    app = build_app_from_env(env)
    assert app is not None


def test_https_posture_helper() -> None:
    cfg = load_config(_base_env())
    assert _https_posture_ok(cfg, _base_env()) is False
    assert _https_posture_ok(cfg, {"KEYPROXY_TLS_TERMINATED": "true"}) is True

    insecure_cfg = load_config(_base_env() | {"KEYPROXY_ALLOW_INSECURE": "1"})
    assert _https_posture_ok(insecure_cfg, {}) is True
