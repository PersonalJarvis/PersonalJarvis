"""Resolve which Jarvis to talk to and how to authenticate.

A 'profile' is a (base_url, control_key) pair. Resolution is per-field:
  base_url:     JARVISCTL_BASE_URL -> config.json -> default loopback:47821
  control_key:  JARVISCTL_CONTROL_KEY -> config.json -> jarvis.core.control_key
The local control_key fallback only helps when the CLI runs on the same
machine/venv as the server (desktop). For a remote VPS, `auth login` writes
the remote key into config.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass

from jarvis.cli_ctl import paths

DEFAULT_BASE_URL = "http://127.0.0.1:47821"


@dataclass(frozen=True)
class Profile:
    base_url: str
    control_key: str | None


def _load_file() -> dict[str, str]:
    p = paths.config_file()
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (ValueError, OSError):
        return {}


def _local_control_key() -> str | None:
    # Imported lazily so the CLI still works in an environment where the
    # server package internals are unavailable.
    try:
        from jarvis.core import control_key

        return control_key.get_control_key()
    except Exception:  # pragma: no cover - defensive
        return None


def resolve_profile() -> Profile:
    data = _load_file()
    base_url = (
        os.environ.get("JARVISCTL_BASE_URL")
        or data.get("base_url")
        or DEFAULT_BASE_URL
    )
    resolved_key = (
        os.environ.get("JARVISCTL_CONTROL_KEY")
        or data.get("control_key")
        or _local_control_key()
    )
    return Profile(base_url=base_url, control_key=resolved_key)


def save_login(base_url: str, control_key: str) -> None:
    p = paths.config_file()
    p.write_text(
        json.dumps({"base_url": base_url, "control_key": control_key}),
        encoding="utf-8",
    )
    if os.name != "nt":  # POSIX: lock down the key file; Windows uses profile ACL
        os.chmod(p, 0o600)


def clear_login() -> None:
    p = paths.config_file()
    if p.exists():
        p.unlink()
