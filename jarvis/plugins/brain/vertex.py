"""Google Cloud Vertex AI brain — the Gemini models on the enterprise endpoint.

Vertex serves the SAME model ids as Google AI Studio; what differs is the
endpoint, the credential, and — the part that actually bites users — the
ACCOUNT the tokens are billed to. Topping up AI Studio does nothing for a
Vertex project and vice versa (the 2026-06-22 forensic), so the two are
separate provider families here rather than one card with a hidden switch: an
install can hold both credentials at once and point each tier at the one it
means.

Everything about talking to Gemini — the tool-name sanitiser, the thinking
budget, the context cache, the streaming loop, the 400-recovery ladder — is
identical on both endpoints, so this class deliberately owns none of it. It
subclasses :class:`~jarvis.plugins.brain.gemini.GeminiBrain` and changes
exactly three facts: which credential slot it reads, that its route is PINNED
to Vertex, and that a missing key is not automatically a missing credential.

That last one is the substantive difference. Vertex has two authentication
shapes:

* **An API key** — an express-mode key (``AQ.``) or a Google Cloud API key
  restricted to ``aiplatform.googleapis.com`` (ordinary ``AIza`` shape).
  Because the ``AIza`` shape is indistinguishable from an AI Studio key, the
  routing probe in :mod:`jarvis.core.google_genai` would send it to the wrong
  host — which is precisely why this class pins the route instead of probing.
* **A Google Cloud project** — ``[google].vertex_project`` plus Application
  Default Credentials (a service account, a ``gcloud`` login, workload
  identity). There is no key at all on this path, so the inherited
  "no credential" refusal has to widen or the documented production setup
  would be rejected as unconfigured.
"""

from __future__ import annotations

from typing import Any

from .gemini import DEFAULT_MODEL, GeminiBrain

__all__ = ["DEFAULT_MODEL", "VertexBrain"]


class VertexBrain(GeminiBrain):
    """Gemini via Google Cloud Vertex AI (express key or Cloud project)."""

    name: str = "vertex"
    provider_id: str = "vertex"
    pinned_route: str | None = "vertex"
    missing_credential_hint: str = (
        "Vertex AI is not configured. Store a Vertex AI API key (VERTEX_API_KEY "
        "/ the Vertex AI card in the API-Keys view), or set "
        "[google].vertex_project for the Google Cloud project path."
    )

    def _credential_is_sufficient(self, endpoint: Any) -> bool:
        """A key OR a configured Cloud project counts as configured.

        Asked through :func:`jarvis.core.config.vertex_credential_configured`
        so the runtime answer and the one the provider card shows come from the
        same place — a card reading "ready" over a brain that refuses to build
        is the drift this shares a single source of truth to prevent.
        """
        if endpoint.credential:
            return True
        from jarvis.core.config import vertex_credential_configured

        return vertex_credential_configured()
