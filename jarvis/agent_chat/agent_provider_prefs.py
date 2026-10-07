"""Which providers and models the assistant's own agents may run on.

The API Keys page turns providers on and off for the agents (any number at
once — every connected seat is one more an agent can sit on) and hides
models a person does not want offered. Three facts, one small JSON file in
the user data folder:

- ``disabled`` — catalog provider ids turned off for the agents. Absent
  means on: connecting a provider is enough to offer it.
- ``api_only`` — dual rows (one id for a subscription CLI AND an API key,
  today ``claude-api``) the person set to run on the key instead of the
  subscription.
- ``hidden_models`` — per provider id, model ids left out of the pickers.

``disabled`` and ``api_only`` narrow the agents' surface only
(``surface="society"``): the coding panes and the front page's chat keep
every seat they had. ``hidden_models`` narrows every model picker — the
agents', the threads', the coding panes' and the front page's — because a
model switched off on the API Keys page is one the person does not want to
scroll past anywhere. A hidden model still runs when a session already sits
on it; hiding only takes it out of the lists.
"""

from __future__ import annotations

import json
import os
import threading
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from loguru import logger

#: The agents' surface; the only one ``disabled`` and ``api_only`` narrow.
AGENT_SURFACE = "society"

_MAX_IDS = 256
_MAX_ID_LEN = 200


@dataclass(frozen=True)
class AgentProviderPrefs:
    disabled: frozenset[str] = frozenset()
    api_only: frozenset[str] = frozenset()
    hidden_models: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def enabled(self, provider_id: str) -> bool:
        return provider_id not in self.disabled

    def hidden(self, provider_id: str) -> tuple[str, ...]:
        return self.hidden_models.get(provider_id, ())

    def to_dict(self) -> dict[str, Any]:
        return {
            "disabled": sorted(self.disabled),
            "api_only": sorted(self.api_only),
            "hidden_models": {k: list(v) for k, v in sorted(self.hidden_models.items()) if v},
        }


_lock = threading.Lock()
_cache: tuple[float, AgentProviderPrefs] | None = None


def _path() -> Path:
    from jarvis.core.paths import user_data_dir

    return user_data_dir() / "agent_chat" / "agent_providers.json"


def _ids(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [v for v in value if isinstance(v, str) and v and len(v) <= _MAX_ID_LEN][:_MAX_IDS]


def parse(data: Any) -> AgentProviderPrefs:
    """Tolerant of a hand-edited or older file: unknown shapes read as empty."""
    if not isinstance(data, dict):
        return AgentProviderPrefs()
    raw_hidden = data.get("hidden_models")
    hidden: dict[str, tuple[str, ...]] = {}
    if isinstance(raw_hidden, dict):
        for key, models in list(raw_hidden.items())[:_MAX_IDS]:
            if isinstance(key, str) and key and len(key) <= _MAX_ID_LEN:
                kept = tuple(dict.fromkeys(_ids(models)))
                if kept:
                    hidden[key] = kept
    return AgentProviderPrefs(
        disabled=frozenset(_ids(data.get("disabled"))),
        api_only=frozenset(_ids(data.get("api_only"))),
        hidden_models=hidden,
    )


def load() -> AgentProviderPrefs:
    """The saved preferences; re-read only when the file changed."""
    global _cache
    path = _path()
    try:
        mtime = path.stat().st_mtime
    except FileNotFoundError:  # never saved: every provider on, nothing hidden
        return AgentProviderPrefs()
    except OSError as exc:
        logger.warning("agent providers: cannot stat {}: {}", path, exc)
        return AgentProviderPrefs()
    with _lock:
        if _cache is not None and _cache[0] == mtime:
            return _cache[1]
    try:
        prefs = parse(json.loads(path.read_text(encoding="utf-8")))
    except (OSError, ValueError) as exc:
        logger.warning("agent providers: unreadable {}: {}", path, exc)
        return AgentProviderPrefs()
    with _lock:
        _cache = (mtime, prefs)
    return prefs


def save(prefs: AgentProviderPrefs) -> AgentProviderPrefs:
    """Write atomically (tempfile + replace) and return what was stored."""
    global _cache
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(prefs.to_dict(), indent=2), encoding="utf-8")
    os.replace(tmp, path)
    with _lock:
        _cache = None
    return prefs


def offered_models(provider_id: str, models: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``models`` minus the ones hidden for ``provider_id`` on the API Keys page."""
    hidden = set(load().hidden(provider_id))
    return [m for m in models if m.get("id") not in hidden] if hidden else models


def forces_api(provider_id: str, surface: str) -> bool:
    """Whether a dual row must run on its API key on ``surface``."""
    return surface == AGENT_SURFACE and provider_id in load().api_only
