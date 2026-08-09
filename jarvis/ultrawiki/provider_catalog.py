"""Declarative catalog of every UltraWiki capability-slot provider.

The sibling of :mod:`jarvis.ui.web.provider_spec` for the four UltraWiki slots
(storage, embedding, distill, rerank). Same doctrine, same reason to exist: the
settings UI must not hardcode provider knowledge, and there must be exactly ONE
place that answers "which providers exist for this slot, what credential do
they need, and where does the user get it".

Why this module had to exist (2026-07-25 gap): the backend registries
(:mod:`jarvis.ultrawiki.embeddings`, :mod:`jarvis.ultrawiki.rerank`,
:class:`jarvis.ultrawiki.store.PostgresStore`) already spoke to Voyage, Mistral,
Cohere and any Postgres server — but the desktop app offered no field to enter
their credentials, and the settings cards pointed at an API-Keys view that has
no such card either. The capability was reachable only by hand-editing a config
file or exporting an environment variable, which the in-app-recoverable mandate
(§3 of the repo charter, AP-23) forbids. This catalog is what the UI renders
credential widgets from, so every listed provider is genuinely connectable.

Design rules kept here:

* **No behavior gating on a provider NAME (AP-21).** Everything in a spec is
  presentation + credential plumbing; the runtime still resolves backends
  through the registries. ``recommended``/``caution`` are badges only.
* **Credentials never live in this module** — only the *slot name* under which
  :func:`jarvis.core.config.get_secret` stores them (AP-12).
* **Storage presets are presentation over one functional value.** Supabase,
  Neon and "any Postgres" all resolve to ``db_backend="postgres"`` plus the
  ``ultrawiki_db_url`` connection string; the preset only decides which help
  text, dashboard link and connect flow the card shows. Keeping the functional
  enum at two values (``sqlite`` | ``postgres``) avoids a fresh multi-layer
  enum-drift surface (AP-4 / BUG-008).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

__all__ = [
    "SlotName",
    "UltraWikiAuthMode",
    "UltraWikiProviderSpec",
    "SUBSCRIPTION_AUTH_MODES",
    "SLOT_NAMES",
    "STORAGE_PROVIDERS",
    "EMBEDDING_PROVIDERS",
    "RERANK_PROVIDERS",
    "DISTILL_PROVIDERS",
    "catalog_for_slot",
    "get_provider_spec",
    "storage_backend_of",
    "all_secret_keys",
]

SlotName = Literal["storage", "embedding", "distill", "rerank"]

#: How a provider is connected.
#:
#: ``none``               — runs locally, no credential at all (SQLite, Ollama).
#: ``api_key``            — one secret slot holds a plain API key.
#: ``connection_string``  — one secret slot holds a full Postgres URI.
#: ``managed_link``       — a guided flow assembles the credential for the user
#:                          (Supabase: browser login → project pick → password).
UltraWikiAuthMode = Literal[
    "none",
    "api_key",
    "connection_string",
    "managed_link",
    "codex",
    "antigravity",
    "claude_cli",
]

# Subscription auth modes mirror the established API-Keys/Jarvis-Agent
# provider descriptors. They are capability labels: callers branch on the
# login mechanism, never on a vendor or model name (AP-21).
SUBSCRIPTION_AUTH_MODES: frozenset[str] = frozenset(
    {"codex", "antigravity", "claude_cli"}
)

SLOT_NAMES: tuple[SlotName, ...] = ("storage", "embedding", "distill", "rerank")


@dataclass(frozen=True, slots=True)
class UltraWikiProviderSpec:
    """One selectable provider inside one UltraWiki capability slot."""

    id: str
    slot: SlotName
    label: str
    auth_mode: UltraWikiAuthMode
    #: Secret-chain slot names this provider reads, in precedence order. Empty
    #: for keyless local providers. These MUST be present in the wizard secret
    #: allowlist, otherwise the UI can render a field the API refuses to save.
    secret_keys: tuple[str, ...]
    #: Where the user generates the credential (opened in their browser).
    dashboard_url: str | None
    #: Plain-English "which credential, and what it is for".
    credential_help: str
    #: Current sensible default model, surfaced as the input placeholder.
    default_model: str = ""
    #: True when the card exposes an editable server URL (local providers).
    supports_base_url: bool = False
    default_base_url: str | None = None
    #: Presentation-only badges — never gate a code path on these (AP-21).
    recommended: bool = False
    caution: str | None = None
    #: Storage only: the functional ``[ultrawiki] db_backend`` value this
    #: preset resolves to. ``None`` for every non-storage slot.
    db_backend: str | None = None
    #: Storage only: a one-line shape hint for the connection string.
    connection_hint: str | None = None


# ---------------------------------------------------------------------------
# Storage — where the knowledge store lives
# ---------------------------------------------------------------------------
#
# SQLite is the universal floor (§3: a headless python:3.11-slim VPS with no
# credential store must still boot into a working UltraWiki). Every cloud
# preset below is an OPTIONAL upgrade for multi-device access and degrades back
# to SQLite with an honest message when its connection string is missing or the
# server is unreachable (see UltraWikiService._open_store).

STORAGE_PROVIDERS: tuple[UltraWikiProviderSpec, ...] = (
    UltraWikiProviderSpec(
        id="sqlite",
        slot="storage",
        label="SQLite — local file",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        db_backend="sqlite",
        recommended=True,
        credential_help=(
            "A single file on this machine. Zero setup, works offline, "
            "nothing leaves the device. Pick a cloud store only if you want "
            "the same memory on several machines."
        ),
    ),
    UltraWikiProviderSpec(
        id="supabase",
        slot="storage",
        label="Supabase",
        auth_mode="managed_link",
        # The access token drives the guided link flow; the assembled URI is
        # what the store actually connects with.
        secret_keys=("ultrawiki_db_url", "supabase_access_token"),
        dashboard_url="https://supabase.com/dashboard/account/tokens",
        db_backend="postgres",
        connection_hint=(
            "postgresql://postgres.<project-ref>:<password>"
            "@<region>.pooler.supabase.com:6543/postgres"
        ),
        credential_help=(
            "Hosted Postgres with a generous free tier. Signing in lists "
            "your projects and builds the connection string for you — you "
            "only add the database password."
        ),
    ),
    UltraWikiProviderSpec(
        id="neon",
        slot="storage",
        label="Neon",
        auth_mode="connection_string",
        secret_keys=("ultrawiki_db_url",),
        dashboard_url="https://console.neon.tech/",
        db_backend="postgres",
        connection_hint=(
            "postgresql://<user>:<password>@<endpoint>.neon.tech/<db>?sslmode=require"
        ),
        credential_help=(
            "Serverless Postgres that scales to zero when idle. Paste the "
            "pooled connection string from the Neon console."
        ),
    ),
    UltraWikiProviderSpec(
        id="postgres",
        slot="storage",
        label="Any Postgres server",
        auth_mode="connection_string",
        secret_keys=("ultrawiki_db_url",),
        dashboard_url=None,
        db_backend="postgres",
        connection_hint="postgresql://<user>:<password>@<host>:5432/<database>",
        credential_help=(
            "Your own server, RDS, Cloud SQL, Azure, Railway — anything "
            "that speaks Postgres. Add pgvector for semantic search; without "
            "it keyword search still works."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Embedding — the vector space of the whole corpus
# ---------------------------------------------------------------------------
#
# Mirrors jarvis.ultrawiki.embeddings.EMBEDDING_BACKENDS one-to-one. Deliberate
# design rule D-3: this slot has NO cross-family fallback, because the
# (backend, model) pair pins the vector space — crossing families mid-ingest
# would mix incompatible vectors. A dead backend pauses the embed stage
# honestly instead.

EMBEDDING_PROVIDERS: tuple[UltraWikiProviderSpec, ...] = (
    UltraWikiProviderSpec(
        id="ollama",
        slot="embedding",
        label="Ollama (local)",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        default_model="qwen3-embedding:4b",
        supports_base_url=True,
        default_base_url="http://localhost:11434",
        recommended=True,
        credential_help=(
            "Runs on your own machine: no key, no per-item cost, and no "
            "text leaves your network. Needs Ollama installed with an "
            "embedding model pulled."
        ),
    ),
    UltraWikiProviderSpec(
        id="gemini",
        slot="embedding",
        label="Google Gemini",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
        default_model="gemini-embedding-001",
        credential_help=(
            "The same Google AI Studio key as the Gemini brain — no second "
            "key needed. Billed per token."
        ),
    ),
    UltraWikiProviderSpec(
        id="openai",
        slot="embedding",
        label="OpenAI",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        default_model="text-embedding-3-small",
        credential_help=(
            "The same OpenAI key as the GPT brain. The small embedding "
            "model is inexpensive and strong for this job."
        ),
    ),
    UltraWikiProviderSpec(
        id="voyage",
        slot="embedding",
        label="Voyage AI",
        auth_mode="api_key",
        secret_keys=("voyage_api_key",),
        dashboard_url="https://dash.voyageai.com/api-keys",
        default_model="voyage-3.5",
        credential_help=(
            "Specialised in retrieval — usually the best search quality per "
            "euro. One key also covers the Voyage reranker."
        ),
    ),
    UltraWikiProviderSpec(
        id="mistral",
        slot="embedding",
        label="Mistral",
        auth_mode="api_key",
        secret_keys=("mistral_api_key",),
        dashboard_url="https://console.mistral.ai/api-keys",
        default_model="mistral-embed",
        credential_help=(
            "European provider, data processed in the EU. Billed per token."
        ),
    ),
    UltraWikiProviderSpec(
        id="cohere",
        slot="embedding",
        label="Cohere",
        auth_mode="api_key",
        secret_keys=("cohere_api_key",),
        dashboard_url="https://dashboard.cohere.com/api-keys",
        default_model="embed-v4.0",
        credential_help=(
            "Strong multilingual embeddings — a good pick when your notes "
            "mix languages. One key also covers the Cohere reranker."
        ),
    ),
)


# ---------------------------------------------------------------------------
# Rerank — optional last retrieval stage
# ---------------------------------------------------------------------------
#
# Mirrors jarvis.ultrawiki.rerank.RERANK_BACKENDS. Skipping this stage is a
# first-class outcome, not a failure: without a reranker the fusion order
# stands and search keeps working.

RERANK_PROVIDERS: tuple[UltraWikiProviderSpec, ...] = (
    # Leads and carries the recommendation because it is the only rerank
    # option that works for an arbitrary downloader: it grades through the
    # key-aware cross-family chat chain, so a cloud key, a subscription CLI or
    # a purely local Ollama all satisfy it (§3, AP-22). The vendor
    # cross-encoders below are faster but each needs its own paid account.
    UltraWikiProviderSpec(
        id="llm",
        slot="rerank",
        label="Your chat providers (no extra account)",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        recommended=True,
        credential_help=(
            "Grades results with the chat providers you already have — a "
            "cloud key, a subscription CLI, or a local Ollama model. No "
            "extra account, no second key."
        ),
    ),
    UltraWikiProviderSpec(
        id="voyage",
        slot="rerank",
        label="Voyage AI",
        auth_mode="api_key",
        secret_keys=("voyage_api_key",),
        dashboard_url="https://dash.voyageai.com/api-keys",
        default_model="rerank-2.5",
        credential_help=(
            "A dedicated scoring model: faster than grading with a chat "
            "model, but needs a paid Voyage account. Same key as above."
        ),
    ),
    UltraWikiProviderSpec(
        id="cohere",
        slot="rerank",
        label="Cohere",
        auth_mode="api_key",
        secret_keys=("cohere_api_key",),
        dashboard_url="https://dashboard.cohere.com/api-keys",
        default_model="rerank-v3.5",
        credential_help=(
            "Cohere's scoring model, strong across languages. Same key as "
            "the embedding slot."
        ),
    ),
)

#: Rerank models are pinned by the backend, not chosen in the settings card —
#: except ``llm``, whose model comes from whichever chat provider the chain
#: reaches. Kept as an explicit statement so a future "model" input on this
#: slot has to justify itself rather than appear by copy-paste.
RERANK_MODEL_IS_USER_CHOSEN = False


# ---------------------------------------------------------------------------
# Distillation — the chat model that normalises each captured item
# ---------------------------------------------------------------------------
#
# Unlike embeddings, this slot DOES cross provider families (AP-22): it runs
# through the shared wiki provider chain, so a 429 or a dead key moves on to
# whatever other credential the user has. The catalog rows below exist so the
# card can offer the key field inline; leaving the slot empty ("Automatic")
# lets the chain pick, which is the honest default.

DISTILL_PROVIDERS: tuple[UltraWikiProviderSpec, ...] = (
    UltraWikiProviderSpec(
        id="codex",
        slot="distill",
        label="OpenAI Codex (ChatGPT subscription)",
        auth_mode="codex",
        secret_keys=(),
        dashboard_url="https://chatgpt.com/",
        credential_help=(
            "Uses your existing ChatGPT/Codex login through the Codex CLI. "
            "This card never needs an OpenAI API key; usage counts against "
            "your subscription limits."
        ),
    ),
    UltraWikiProviderSpec(
        id="antigravity",
        slot="distill",
        label="Antigravity (Google subscription)",
        auth_mode="antigravity",
        secret_keys=(),
        dashboard_url="https://antigravity.google/",
        credential_help=(
            "Uses your Google subscription through the Antigravity or Gemini "
            "CLI login. This card does not use the Gemini API key."
        ),
    ),
    UltraWikiProviderSpec(
        id="claude-cli",
        slot="distill",
        label="Claude (Anthropic subscription)",
        auth_mode="claude_cli",
        secret_keys=(),
        dashboard_url="https://claude.ai/",
        credential_help=(
            "Uses your Claude subscription through the Claude CLI login. "
            "This card does not use an Anthropic API key."
        ),
    ),
    UltraWikiProviderSpec(
        id="gemini",
        slot="distill",
        label="Google Gemini",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
        default_model="gemini-3.5-flash",
        recommended=True,
        credential_help=(
            "Google AI Studio key. A fast flash model fits well here: this "
            "is a high-volume background job, not a reasoning task."
        ),
    ),
    UltraWikiProviderSpec(
        id="openai",
        slot="distill",
        label="OpenAI",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        credential_help="Shared with the GPT brain — no second key needed.",
    ),
    UltraWikiProviderSpec(
        id="claude-api",
        slot="distill",
        label="Claude (API key)",
        auth_mode="api_key",
        secret_keys=("anthropic_api_key",),
        dashboard_url="https://console.anthropic.com/settings/keys",
        credential_help="Shared with the Claude brain — no second key needed.",
    ),
    UltraWikiProviderSpec(
        id="openrouter",
        slot="distill",
        label="OpenRouter",
        auth_mode="api_key",
        secret_keys=("openrouter_api_key",),
        dashboard_url="https://openrouter.ai/keys",
        credential_help=(
            "One key reaches many models — handy for picking a cheap one "
            "for this background job."
        ),
    ),
    UltraWikiProviderSpec(
        id="grok",
        slot="distill",
        label="xAI Grok",
        auth_mode="api_key",
        secret_keys=("grok_api_key",),
        dashboard_url="https://console.x.ai/",
        credential_help="Shared with the Grok brain — no second key needed.",
    ),
    UltraWikiProviderSpec(
        id="nvidia",
        slot="distill",
        label="NVIDIA NIM",
        auth_mode="api_key",
        secret_keys=("nvidia_api_key",),
        dashboard_url="https://build.nvidia.com/settings/api-keys",
        credential_help=(
            "Key from build.nvidia.com. The free tier is slow to start, "
            "which matters little for a background job."
        ),
    ),
    UltraWikiProviderSpec(
        id="ollama",
        slot="distill",
        label="Ollama (local)",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        supports_base_url=True,
        default_base_url="http://localhost:11434",
        credential_help=(
            "Runs on your own machine: no key, no cost, nothing leaves "
            "your network. Slower, but this runs in the background."
        ),
    ),
)


_BY_SLOT: dict[str, tuple[UltraWikiProviderSpec, ...]] = {
    "storage": STORAGE_PROVIDERS,
    "embedding": EMBEDDING_PROVIDERS,
    "distill": DISTILL_PROVIDERS,
    "rerank": RERANK_PROVIDERS,
}


def catalog_for_slot(slot: str) -> tuple[UltraWikiProviderSpec, ...]:
    """Every declared provider for ``slot`` (empty tuple when unknown)."""
    return _BY_SLOT.get(slot, ())


def get_provider_spec(slot: str, provider_id: str) -> UltraWikiProviderSpec | None:
    """One spec by (slot, id). ``None`` when either is unknown."""
    for spec in catalog_for_slot(slot):
        if spec.id == provider_id:
            return spec
    return None


def storage_backend_of(preset_id: str) -> str:
    """The functional ``db_backend`` a storage preset resolves to.

    Unknown presets fall back to ``"sqlite"`` — the universal floor is always
    a safe answer, and an unknown preset means an older or newer config than
    this build knows about, not a reason to refuse to open a store.
    """
    spec = get_provider_spec("storage", preset_id)
    return spec.db_backend or "sqlite" if spec else "sqlite"


def all_secret_keys() -> set[str]:
    """Every secret slot any UltraWiki provider reads.

    Used by the test that pins these against the wizard allowlist: a catalog
    entry the secrets API would refuse to save is a dead field in the UI, which
    is exactly the defect this module was written to end.
    """
    return {
        key
        for specs in _BY_SLOT.values()
        for spec in specs
        for key in spec.secret_keys
    }
