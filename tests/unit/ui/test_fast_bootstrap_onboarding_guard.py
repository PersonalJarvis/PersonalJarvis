"""The warming onboarding fastpath carries its own minimum boundary.

Production binds ``FastBootstrap._asgi`` WITHOUT the outer SurfaceSecurity
(see ``_start_server``), so before this guard any web page could record the
user's consent to the Terms during every boot's warm-up: a DNS-rebound host
or a cross-site form POST reached ``/api/onboarding/accept-terms`` directly.
Requests the guard refuses are not answered here; they are held for the real
app, whose boundary judges them (the short hold timeout turns that into 503).
"""
from __future__ import annotations

import json

import pytest

from jarvis.setup import onboarding_fastpath as fp
from jarvis.setup import state as st
from jarvis.ui.web import surface_security
from jarvis.ui.web.fast_bootstrap import FastBootstrap

_OWN = "http://127.0.0.1:47821"


@pytest.fixture
def state_path(tmp_path, monkeypatch):
    path = tmp_path / "setup_state.json"
    monkeypatch.setattr(fp, "_STATE_PATH_OVERRIDE", path)
    return path


def _scope(
    path: str,
    method: str = "POST",
    *,
    host: str = "127.0.0.1:47821",
    origin: str | None = _OWN,
    site: str | None = None,
    peer: str = "127.0.0.1",
) -> dict:
    headers = [(b"host", host.encode("latin-1"))]
    if origin is not None:
        headers.append((b"origin", origin.encode("latin-1")))
    if site is not None:
        headers.append((b"sec-fetch-site", site.encode("latin-1")))
    return {
        "type": "http",
        "method": method,
        "path": path,
        "scheme": "http",
        "client": (peer, 50000),
        "headers": headers,
    }


async def _receive():
    return {"type": "http.request", "body": b"", "more_body": False}


async def _call(boot: FastBootstrap, scope: dict) -> list[dict]:
    sent: list[dict] = []

    async def send(message: dict) -> None:
        sent.append(message)

    await boot._asgi(scope, _receive, send)
    return sent


def _accepted(path) -> bool:
    return st.get_onboarding_state(path)["terms_accepted_at"] is not None


def _boot(tmp_path) -> FastBootstrap:
    return FastBootstrap(hold_timeout=0.05, dist_dir=tmp_path / "no-dist")


@pytest.mark.parametrize(
    ("label", "overrides"),
    [
        ("dns-rebound host", {"host": "attacker.example:47821", "origin": "http://attacker.example:47821"}),
        ("foreign origin", {"origin": "https://attacker.example"}),
        ("no origin", {"origin": None}),
        ("remote peer", {"peer": "203.0.113.7"}),
    ],
)
async def test_warming_terms_consent_refuses_foreign_callers(
    tmp_path, state_path, label: str, overrides: dict
) -> None:
    surface_security.set_browser_login_required(False)
    boot = _boot(tmp_path)

    sent = await _call(boot, _scope("/api/onboarding/accept-terms", **overrides))

    assert sent[0]["status"] == 503, label  # held for the real app, not answered
    assert not _accepted(state_path), label


async def test_warming_terms_consent_needs_open_access_or_a_session(tmp_path, state_path) -> None:
    surface_security.set_browser_login_required(True)  # browser lock on, no cookie
    boot = _boot(tmp_path)

    sent = await _call(boot, _scope("/api/onboarding/accept-terms"))

    assert sent[0]["status"] == 503
    assert not _accepted(state_path)


async def test_warming_terms_consent_from_the_apps_own_page_still_works(
    tmp_path, state_path
) -> None:
    surface_security.set_browser_login_required(False)
    boot = _boot(tmp_path)

    sent = await _call(boot, _scope("/api/onboarding/accept-terms", site="same-origin"))

    assert sent[0]["status"] == 200
    assert _accepted(state_path)


async def test_warming_state_read_stays_available(tmp_path, state_path) -> None:
    boot = _boot(tmp_path)

    sent = await _call(boot, _scope("/api/onboarding/state", "GET", origin=None))

    assert sent[0]["status"] == 200
    assert json.loads(sent[1]["body"])["completed"] is False


@pytest.mark.parametrize(
    "overrides",
    [{"host": "attacker.example"}, {"site": "cross-site", "origin": None}],
)
async def test_warming_state_read_refuses_rebound_or_cross_site(
    tmp_path, state_path, overrides: dict
) -> None:
    boot = _boot(tmp_path)

    sent = await _call(boot, _scope("/api/onboarding/state", "GET", **overrides))

    assert sent[0]["status"] == 503
