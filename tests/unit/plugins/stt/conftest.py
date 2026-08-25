"""Keep STT provider-selection tests independent of THIS machine's credentials.

Every test in this package substitutes ``get_secret_any`` to say exactly which
credential slots exist, which is what makes "one keyed family gives an honest
empty chain" a statement about the resolver rather than about the developer's
keyring.

One credential escapes that seam: Vertex authenticates with Application Default
Credentials, so ``_STT_KEYLESS_CREDENTIAL_PROBES`` asks the machine instead of
the keyring. On a box with ``gcloud`` ADC configured — or a Vertex service
account, the documented production setup — ``vertex-stt`` therefore appears as a
keyed family no test asked for, and ten assertions about the cross-family chain
fail on a real machine while staying green in CI. That is the shape AP-28 warns
about: a check whose verdict depends on something the test never pinned.

So the probe is off by default here and a test that wants the Vertex-ADC host
turns it on explicitly with the ``vertex_adc`` fixture.
"""
from __future__ import annotations

import pytest

import jarvis.core.config as cfg


@pytest.fixture(autouse=True)
def _no_ambient_vertex_adc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the keyless Vertex probe to "not configured" unless a test says otherwise."""
    monkeypatch.setattr(cfg, "vertex_credential_configured", lambda: False, raising=False)


@pytest.fixture
def vertex_adc(monkeypatch: pytest.MonkeyPatch) -> None:
    """Opt back IN to a host that authenticates Vertex through ADC."""
    monkeypatch.setattr(cfg, "vertex_credential_configured", lambda: True, raising=False)
