"""A keyless local provider must be selectable as the heavy-task worker.

Field report 2026-08-05: the Agents section rendered Ollama and the generic
local server as API-key cards — "Locked — save a dedicated ollama key on this
card", a key field for a key that does not exist, and a warning toast instead
of an activation. Nothing on those cards can ever be satisfied, so an install
with no cloud credential could pick NO worker at all: the one tier where the
local-first promise had to hold was the one that refused it.

The row is what the card renders from, so the fix belongs here: readiness for a
keyless card is "it exists", not "a key is stored". The switch itself was never
the problem — ``is_credential_present`` returns True for auth_mode "none" — so
these tests pin the payload that decides whether the user can even try.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from jarvis.core.bus import EventBus
from jarvis.core.config import load_config
from jarvis.ui.web.provider_spec import get_spec
from jarvis.ui.web.server import WebServer

#: Every keyless provider the Agents section offers. Derived from the same
#: mapping table the section renders, so a keyless provider added later is
#: covered without touching this file.
KEYLESS_AGENT_PROVIDERS: tuple[str, ...] = tuple(
    m.jarvis
    for m in __import__(
        "jarvis.missions.worker_runtime.provider_map", fromlist=["MAPPINGS"]
    ).MAPPINGS
    if (spec := get_spec(m.jarvis)) is not None and spec.auth_mode == "none"
)


def _rows() -> dict[str, dict[str, Any]]:
    ws = WebServer(bus=EventBus(), cfg=load_config())
    with TestClient(ws.app) as client:
        resp = client.get("/api/jarvis-agent/status")
    assert resp.status_code == 200
    return {row["jarvis"]: row for row in resp.json()["mapping"]}


def test_the_section_offers_at_least_one_keyless_worker() -> None:
    assert KEYLESS_AGENT_PROVIDERS, (
        "No keyless provider is offered as a heavy-task worker, so an install "
        "with no cloud credential cannot run missions at all."
    )


@pytest.mark.parametrize("provider_id", KEYLESS_AGENT_PROVIDERS)
def test_a_keyless_worker_is_ready_without_a_key(provider_id: str) -> None:
    """``key_set`` is the readiness decision the card gates activation on."""
    row = _rows()[provider_id]
    assert row["key_set"] is True
    assert row["keyless"] is True


@pytest.mark.parametrize("provider_id", KEYLESS_AGENT_PROVIDERS)
def test_a_keyless_worker_offers_no_key_field(provider_id: str) -> None:
    """A field for a credential that does not exist is an invitation to fail."""
    row = _rows()[provider_id]
    assert row["secret_key"] is None
    assert row["credential_source"] == "none"


@pytest.mark.parametrize("provider_id", KEYLESS_AGENT_PROVIDERS)
def test_a_keyless_worker_is_billed_as_local(provider_id: str) -> None:
    """The badge said "API key · billed per token" over a provider that bills
    nothing and needs no account."""
    row = _rows()[provider_id]
    assert row["billing"] == "local"


@pytest.mark.parametrize("provider_id", KEYLESS_AGENT_PROVIDERS)
def test_a_keyless_worker_shows_its_real_name(provider_id: str) -> None:
    """The picker showed raw ids ("local-openai") beside proper labels."""
    row = _rows()[provider_id]
    spec = get_spec(provider_id)
    assert spec is not None
    assert row["label"] == spec.label
    assert row["label"] != provider_id


def test_keyed_workers_keep_their_key_gate() -> None:
    """The fix must not hand every card a free pass: a provider that really
    does need a credential still reports honestly."""
    rows = _rows()
    keyed = [
        pid
        for pid, row in rows.items()
        if not row.get("keyless") and (spec := get_spec(pid)) and spec.auth_mode == "api_key"
    ]
    assert keyed, "no keyed provider left to compare against"
    for pid in keyed:
        row = rows[pid]
        assert row["billing"] != "local"
        # Readiness still follows the stored credential, whatever it is here.
        assert row["key_set"] == (
            row["api_key_set"] or row["oauth_connected"] or row.get("oauth_stale", False)
        )
