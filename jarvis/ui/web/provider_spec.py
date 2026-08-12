"""Declarative description of every configurable provider for the desktop app.

Single source of truth for the UI: which providers exist, what auth method
do they need, which credential-manager slot stores their key, which login
CLI needs to be spawned? Deliberately NO model names — models change too
often, and the default comes from jarvis.toml. The UI renders a generic
widget per provider based on auth_mode.

Tiers: brain, tts, stt, realtime — and ``dictation``, the optional transcript
polish pass. The dictation cards are NOT hand-written here: they are derived
from ``jarvis.dictation.polish_client.POLISH_FAMILIES``, which owns the family
list, so a new family is one row there plus one block of card text below.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from jarvis.dictation.polish_client import POLISH_FAMILIES

AuthMode = Literal["api_key", "codex", "antigravity", "claude_cli", "none"]
Tier = Literal["brain", "tts", "stt", "realtime", "dictation"]
# How using a provider is billed. Derived from auth_mode (never branched on a
# provider name — see provider_billing): an API key bills per token on an API
# account; a subscription provider runs over an existing plan login; codex can
# do either; a local provider needs no credential at all.
Billing = Literal["api", "subscription", "subscription_or_api", "local"]


@dataclass(frozen=True, slots=True)
class AltCredential:
    """An alternative credential path for the SAME provider.

    Gemini is the motivating case (2026-06-22 forensic): the identical model is
    reachable either via a Google AI Studio API key (pay-as-you-go) OR a Google
    Cloud Vertex AI service account (a *separate* billing project). A user who
    tops up one account while Jarvis is wired to the other gets a silent
    "credits depleted" failure. The UI surfaces BOTH paths so the choice — and
    its billing — is explicit. ``None`` (the default on ProviderSpec) means the
    provider has a single credential path.
    """

    label: str
    billing: Billing
    credential_help: str
    dashboard_url: str | None = None
    credential_path_hint: str | None = None


@dataclass(frozen=True, slots=True)
class ProviderSpec:
    id: str
    label: str
    tier: Tier
    auth_mode: AuthMode
    secret_keys: tuple[str, ...]
    dashboard_url: str | None
    login_cli: tuple[str, ...] | None = None
    install_hint: str | None = None
    credential_path_hint: str | None = None
    brain_switchable: bool = True
    # Plain-English "which key / subscription, and what it is for", shown under
    # the provider's credential widget so the user does not have to guess.
    credential_help: str | None = None
    # Where to sign up for the account/subscription this provider bills against
    # (distinct from dashboard_url, which is where you GENERATE the key). Mainly
    # useful for the subscription providers; ``None`` for most API providers.
    signup_url: str | None = None
    # Alternative credential path (Gemini AI Studio vs Vertex). ``None`` =
    # single path.
    alt_credential: AltCredential | None = None
    # Maintainer-recommended pick for this tier (UI badge). Set on exactly the
    # provider the maintainer wants users to default to — currently the Gemini
    # brain (best real-world experience, 2026-06-22). This is a presentation
    # hint only: it never gates behavior, never branches a code path on a
    # provider name (AP-21), and only the *brain* tier carries it today.
    recommended: bool = False
    # The specific model the recommendation points at (e.g. ``gemini-3.5-flash``),
    # surfaced as a "recommended" marker in the model picker. ``None`` = the badge
    # stands for the provider as a whole with no model preference.
    recommended_model: str | None = None
    # The inverse of ``recommended``: a short caution shown as a "Not recommended"
    # badge (with this text as its tooltip) so the user is warned BEFORE picking a
    # provider that works but has a real drawback. Presentation-only, never gates
    # behavior (AP-21). Set on NVIDIA NIM: the free dev tier's 10-30s+ TTFB makes
    # it sluggish as a main/voice brain. ``None`` = no caution.
    caution: str | None = None
    # This provider powers a feature the install does NOT depend on: without a
    # key the feature simply does not run and everything else behaves exactly as
    # before. The health rollup reads it to stay SILENT instead of raising a
    # permanent amber "needs setup" dot (``_tier_section_health``), and the card
    # renders an "Optional" chip. Presentation + nag-suppression only — it never
    # gates behavior and no code path branches on a provider name because of it
    # (AP-21). Set on the whole ``dictation`` tier today.
    optional: bool = False
    # Local/self-hosted providers: the card exposes an editable server URL
    # (persisted as ``[brain.providers.<id>].base_url`` via
    # ``PUT /api/providers/{id}/base-url``). ``default_base_url`` is the
    # placeholder shown while no override is stored — ``None`` with
    # ``supports_base_url=True`` means the user MUST set one (local-openai:
    # no guessable default port).
    supports_base_url: bool = False
    default_base_url: str | None = None
    # The integration uses a provider protocol that is not yet stable. The UI
    # shows a clear badge and fallback note, while runtime selection remains
    # capability-driven. This is presentation only and never gates behavior.
    experimental: bool = False
    # Withdrawn from the public catalog: ``/api/providers`` does not serve the
    # card, so the settings UI offers no way to select the provider. This is
    # presentation-gating only (AP-21): all runtime plumbing — config
    # validation, status probes, an already-persisted selection, an explicit
    # API/CLI switch — keeps working. A release gate for transports that
    # shipped visibly before they were reliable, not a removal.
    hidden: bool = False


def provider_billing(spec: ProviderSpec) -> Billing:
    """How using *spec* is billed — capability-driven, never name-branched
    (multi-provider mandate, AP-21).

    * a subscription-login provider (``codex``/``antigravity``/``claude-cli``)
      that ALSO carries
      an API-key slot → ``"subscription_or_api"`` (a plan login OR per-token key);
      one with no key slot → ``"subscription"``. The distinction is the presence
      of ``secret_keys``, not the provider name — so adding a key slot to either
      flips it without touching this function.
    * ``none`` → ``"local"`` — no credential, runs on-device.
    * everything else (``api_key``) → ``"api"`` — pay per token on an API account.
    """
    if spec.auth_mode in ("antigravity", "codex", "claude_cli"):
        return "subscription_or_api" if spec.secret_keys else "subscription"
    if spec.auth_mode == "none":
        return "local"
    return "api"


# Gemini's alternative credential path: Google Cloud Vertex AI. A SEPARATE
# billing project from an AI Studio key — the 2026-06-22 forensic was a user
# topping up AI Studio while Jarvis was wired to Vertex. Shared by the Gemini
# brain + Gemini TTS specs so both surface the choice. The EASY Vertex path is
# an express-mode API key (AQ.) pasted into the normal key field — the routing
# resolver (jarvis/core/google_genai.py) detects it on every tier (brain, tool
# model, realtime, STT, TTS, dictation). The service-account JSON remains the
# TTS-tier production path via [tts].use_vertex.
_GEMINI_VERTEX = AltCredential(
    label="Vertex AI (express key or service account)",
    billing="api",
    credential_help=(
        "Bill Gemini through a Google Cloud Vertex AI project instead of an AI "
        "Studio account — a SEPARATE billing account. Easiest path: create a "
        "Vertex express-mode API key (starts with AQ.) and paste it into the "
        "normal key field above — Jarvis detects it and routes every tier "
        "through Vertex automatically. For TTS production quota there is also "
        "the service-account JSON path ([tts].use_vertex + the file below). "
        "Don't mix the accounts up: topping up AI Studio does nothing if "
        "Jarvis is on Vertex, and vice versa."
    ),
    dashboard_url="https://console.cloud.google.com/vertex-ai/studio",
    credential_path_hint="~/.config/jarvis/vertex-sa.json",
)


# ── Dictation polish: the cards are DERIVED, not declared ─────────────────────
# The family list itself lives in ``jarvis.dictation.polish_client`` and is the
# single source of truth for ids, labels, credential slots and default models.
# Re-typing it here would be a second declaration of the same fact — the exact
# shape that produced BUG-008 four times. This module contributes only the half
# polish_client has no opinion about: where you get the key and what it buys you.
#
# The spec id is the family id plus a suffix, because a bare "groq"/"openai"
# would collide with the brain cards and ``get_spec`` returns the first match.

#: Distinguishes a dictation-polish card from the brain/STT card of the same
#: vendor. Never parsed anywhere except the two helpers below.
_DICTATION_SPEC_SUFFIX = "-polish"


def dictation_spec_id(family_id: str) -> str:
    """ProviderSpec id for a polish family id (``"groq"`` -> ``"groq-polish"``)."""
    return f"{family_id}{_DICTATION_SPEC_SUFFIX}"


def dictation_family_id(spec_id: str) -> str:
    """Polish family id behind a dictation card id; ``""`` if it is not one."""
    return DICTATION_FAMILY_BY_SPEC_ID.get(spec_id, "")


@dataclass(frozen=True, slots=True)
class _DictationCardText:
    """The human-facing half of one dictation-polish card.

    Deliberately carries NO id, label, credential slot or model name: every one
    of those is read from the :data:`~jarvis.dictation.polish_client.POLISH_FAMILIES`
    row this text is keyed by.
    """

    dashboard_url: str | None
    credential_help: str
    signup_url: str | None = None
    #: May contain ``{model}``, filled from the family's own ``default_model``
    #: so a setup command can never advertise a model the pass does not run.
    install_hint: str | None = None
    recommended: bool = False


# One entry per family we can actually ACCEPT a key for. A family with no entry
# ships no card — see the Cerebras note below for the only case today.
_DICTATION_CARD_TEXT: dict[str, _DictationCardText] = {
    "groq": _DictationCardText(
        dashboard_url="https://console.groq.com/keys",
        credential_help=(
            "Groq API key (starts with gsk_) — the SAME key the Groq "
            "speech-to-text provider uses, so most installs already have it and "
            "need no new account. It lets a small, very fast model tidy up "
            "punctuation, capitalization and filler words in what you dictated, "
            "without changing your words. Optional: with no key here, dictation "
            "keeps working exactly as it did before."
        ),
        # Recommended because the key is usually already present (Groq is the
        # shipped speech-to-text default), so the feature turns itself on with
        # zero setup — and it is the cheapest of the cloud options per dictation.
        recommended=True,
    ),
    "gemini": _DictationCardText(
        dashboard_url="https://aistudio.google.com/app/apikey",
        credential_help=(
            "Same Google AI Studio key as the Gemini brain (starts with AIza or "
            "AQ.) — no second key needed. Tidies up punctuation and filler words "
            "in your dictation without changing your words, and handles languages "
            "outside German, English and Spanish best of the cloud options. "
            "Optional: with no key here, dictation keeps working as before."
        ),
    ),
    "openai": _DictationCardText(
        dashboard_url="https://platform.openai.com/api-keys",
        credential_help=(
            "Uses your OpenAI API key (shared with the GPT brain and Whisper "
            "speech-to-text). Tidies up punctuation and filler words in your "
            "dictation without changing your words. Optional: with no key here, "
            "dictation keeps working exactly as it did before."
        ),
    ),
    "openrouter": _DictationCardText(
        dashboard_url="https://openrouter.ai/keys",
        credential_help=(
            "Uses your OpenRouter API key (shared with the OpenRouter brain — no "
            "second key needed). One OpenRouter key reaches every model family "
            "this feature can use, so a single credential is enough on its own. "
            "Optional: with no key here, dictation keeps working as before."
        ),
    ),
    "ollama": _DictationCardText(
        dashboard_url=None,
        signup_url="https://ollama.com/download",
        install_hint="ollama pull {model}",
        credential_help=(
            "Tidies up your dictation FULLY LOCAL through an Ollama server — no "
            "API key, no cloud account, nothing leaves this machine. Install "
            "Ollama, pull a small instruct model, then choose Ollama as the "
            "dictation clean-up provider in the Voice settings. It is never "
            "picked automatically: dialling a local server that happens to not "
            "be running would spend the whole time budget on a refused "
            "connection, so this one is only ever used when you ask for it."
        ),
    ),
    # Cerebras is a POLISH_FAMILIES member with NO entry here on purpose. Its
    # credential slot ("cerebras_api_key") is not declared in the setup wizard,
    # and ALLOWED_SECRET_KEYS (provider_routes) is derived from that
    # declaration — so a Cerebras card would render a key field whose Save
    # button answers 404. A card that cannot accept a key is worse than no card.
    # Declare the wizard slot first, then add one row here; the guard test
    # ``test_dictation_provider_tier.py`` goes red the moment the slot exists.
}


def _dictation_specs() -> tuple[ProviderSpec, ...]:
    """Build one card per polish family we can store a credential for.

    ``auth_mode`` is derived from the family's own ``needs_key`` capability, not
    from its name (AP-21): a keyless local engine becomes a ``"none"`` card and
    therefore bills as ``"local"`` and never reports "no key set".
    """
    specs: list[ProviderSpec] = []
    for family in POLISH_FAMILIES:
        text = _DICTATION_CARD_TEXT.get(family.id)
        if text is None:
            continue
        specs.append(
            ProviderSpec(
                id=dictation_spec_id(family.id),
                # Qualified, never bare: the delete-a-shared-key warning lists
                # provider LABELS, and a second plain "Groq" there would not tell
                # the user which surface is about to lose its credential. A colon
                # rather than a parenthesis, because a family label may already
                # carry one ("Ollama (local)"); plain ASCII, because a label
                # travels into log lines that may land on a cp1252 console.
                label=f"{family.label}: dictation polish",
                tier="dictation",
                auth_mode="api_key" if family.needs_key else "none",
                # The card renders ONE field: the family's primary slot. The
                # remaining candidates are read-only fallbacks the runtime
                # resolves on its own, so offering them as extra input boxes
                # would ask for keys nobody needs to enter twice.
                secret_keys=family.secret_candidates[:1],
                dashboard_url=text.dashboard_url,
                install_hint=(
                    text.install_hint.format(model=family.default_model)
                    if text.install_hint
                    else None
                ),
                signup_url=text.signup_url,
                credential_help=text.credential_help,
                recommended=text.recommended,
                # No recommended_model: the model for each family already lives
                # in POLISH_FAMILIES[].default_model, and this tier has no model
                # picker to highlight it in.
                optional=True,
            )
        )
    return tuple(specs)


_DICTATION_PROVIDERS: tuple[ProviderSpec, ...] = _dictation_specs()

#: Polish family id -> the id of the card that configures it, and back. The
#: config key ``[dictation].polish_provider`` stores a FAMILY id while the UI
#: and the health rollup address a SPEC id; these two are the one translation.
DICTATION_SPEC_ID_BY_FAMILY: dict[str, str] = {
    family.id: dictation_spec_id(family.id)
    for family in POLISH_FAMILIES
    if family.id in _DICTATION_CARD_TEXT
}
DICTATION_FAMILY_BY_SPEC_ID: dict[str, str] = {
    spec_id: family_id for family_id, spec_id in DICTATION_SPEC_ID_BY_FAMILY.items()
}


PROVIDERS: tuple[ProviderSpec, ...] = (
    # ── Brain: providers (Brain-tab card render order) ────────────────────
    # The Brain tab renders switchable providers in this tuple order; Gemini
    # leads as the maintainer-recommended default, then OpenRouter, Claude,
    # OpenAI, NVIDIA. The OAuth-subscription siblings (antigravity/codex,
    # brain_switchable=False) sit next to their API-key kin and surface only in
    # the Jarvis-Agents section, never this tab.
    ProviderSpec(
        id="gemini",
        label="Google Gemini",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
        credential_help=(
            "Google AI Studio API key (starts with AIza or AQ.) or a Vertex AI "
            "express-mode key (also AQ.) — Jarvis detects which one it is and "
            "routes it to the right endpoint automatically. Mind the billing: "
            "AI Studio and Vertex are SEPARATE accounts."
        ),
        alt_credential=_GEMINI_VERTEX,
        # Maintainer-recommended brain (2026-06-22): best real-world experience.
        # Badge on the brain card; the model picker highlights gemini-3.5-flash.
        recommended=True,
        recommended_model="gemini-3.5-flash",
    ),
    # ── Brain: Google subscription via the official Antigravity/Gemini CLI ──
    # OAuth-only (no API-key slot): we drive the official ``agy``/``gemini`` CLI
    # as a subprocess over the user's "Sign in with Google" login — billed
    # against the Google subscription, no Gemini API key. Mirror of the Codex
    # ChatGPT-login path. The UI renders an OAuth connect widget (auth_mode).
    ProviderSpec(
        id="antigravity",
        label="Antigravity (Google subscription)",
        tier="brain",
        auth_mode="antigravity",
        # Dual billing, mirror of Codex: the Google subscription login OR a
        # Gemini API key (the Google Cloud credential). The key slot reuses the
        # shared ``gemini_api_key`` — the same key the Gemini provider uses — so
        # a user who already set it gets per-token Antigravity billing for free.
        # Carrying a secret_key flips provider_billing → subscription_or_api.
        secret_keys=("gemini_api_key",),
        dashboard_url="https://antigravity.google",
        # agy has NO `login` subcommand (verified 2026-06-21) — the bare binary
        # drops into the interactive "Sign in with Google" flow. The Connect
        # button drives POST /api/antigravity/login (start_login → bare run).
        login_cli=("agy",),
        install_hint="Install Antigravity (agy) or sign in with the Gemini CLI",
        credential_path_hint="~/.gemini/oauth_creds.json",
        brain_switchable=False,
        signup_url="https://antigravity.google",
        credential_help=(
            "Sign in with your Google account to run heavy subagent tasks over "
            "your Google subscription via the Antigravity/Gemini CLI — no API "
            "key, billed to your subscription. Or set a Gemini API key to bill "
            "per token instead."
        ),
    ),
    ProviderSpec(
        id="openrouter",
        label="OpenRouter",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("openrouter_api_key",),
        dashboard_url="https://openrouter.ai/keys",
        credential_help=(
            "OpenRouter API key (starts with sk-or-). One key reaches many "
            "models; billed per token on your OpenRouter account."
        ),
    ),
    ProviderSpec(
        id="grok",
        label="xAI Grok",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("grok_api_key",),
        dashboard_url="https://console.x.ai/",
        signup_url="https://grok.com/",
        credential_help=(
            "xAI API key (starts with xai-) for the Grok brain and Jarvis-Agent "
            "tool loops. Your grok.com and xAI API accounts share a login, but "
            "API usage is billed separately through the xAI Console. The same "
            "key also powers Grok Voice TTS."
        ),
    ),
    ProviderSpec(
        id="claude-api",
        label="Claude (API-Key)",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("anthropic_api_key",),
        dashboard_url="https://console.anthropic.com/settings/keys",
        credential_help=(
            "Anthropic API key (starts with sk-ant-). Billed per token on your "
            "Anthropic account. Powers the Claude brain."
        ),
    ),
    ProviderSpec(
        id="openai",
        label="OpenAI",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        credential_help=(
            "OpenAI API key (starts with sk-). Billed per token. Shared by the "
            "GPT brain, Whisper STT and OpenAI TTS."
        ),
    ),
    ProviderSpec(
        id="codex",
        label="OpenAI Codex",
        tier="brain",
        auth_mode="codex",
        secret_keys=("codex_openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        login_cli=("codex", "login"),
        install_hint="npm i -g @openai/codex",
        brain_switchable=False,
        signup_url="https://chatgpt.com",
        credential_help=(
            "Run heavy subagent tasks over your ChatGPT subscription via the "
            "Codex CLI — sign in below, no API key needed. Or paste an OpenAI "
            "API key to bill per token instead."
        ),
    ),
    # ── Brain: Anthropic subscription via the official Claude CLI ──
    # Sibling of the Codex/Antigravity subscription paths: spends a Claude plan
    # login instead of a per-token Anthropic key. No secret_keys slot — the
    # per-token path for this family already exists as ``claude-api``, and
    # duplicating it here would present the same key twice.
    #
    # ``brain_switchable=False`` is not a formality: process start-up alone is
    # ~10-12 s, which would destroy a spoken turn, so this must never become an
    # install's main conversational brain.
    ProviderSpec(
        id="claude-cli",
        label="Claude (Anthropic subscription)",
        tier="brain",
        auth_mode="claude_cli",
        secret_keys=(),
        dashboard_url=None,
        login_cli=("claude", "/login"),
        install_hint="curl -fsSL https://claude.ai/install.sh | bash",
        credential_path_hint="~/.claude/.credentials.json",
        brain_switchable=False,
        signup_url="https://claude.ai",
        credential_help=(
            "Write Agentic IDE task briefs over your Claude subscription via the "
            "Claude Code CLI — no API key needed. Sign in from the UltraWiki "
            "model panel, or run 'claude /login' in a terminal. Too slow to "
            "start for spoken replies, so it is not offered as a voice brain."
        ),
    ),
    ProviderSpec(
        id="nvidia",
        label="NVIDIA NIM",
        tier="brain",
        auth_mode="api_key",
        secret_keys=("nvidia_api_key",),
        dashboard_url="https://build.nvidia.com/settings/api-keys",
        signup_url="https://build.nvidia.com",
        credential_help=(
            "NVIDIA API key (starts with nvapi-) from build.nvidia.com — use the "
            "key from Settings > API Keys, NOT the NGC key. One key reaches many "
            "NVIDIA-hosted models (Nemotron, Llama, DeepSeek, Qwen); free dev tier "
            "available, then billed per token on your NVIDIA account."
        ),
        caution=(
            "The free NIM tier is slow — 10-30s to the first response. Fine for "
            "background tasks, sluggish as your main or voice brain."
        ),
    ),
    # Ollama re-added 2026-07-25 as a keyless LOCAL provider (auth_mode
    # "none" → billing "local"); the 2026-04-21 removal predates the
    # local-first mandate.
    ProviderSpec(
        id="ollama",
        label="Ollama (local)",
        tier="brain",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        install_hint="ollama pull qwen3.5",
        signup_url="https://ollama.com/download",
        supports_base_url=True,
        default_base_url="http://localhost:11434",
        credential_help=(
            "Runs models fully local through an Ollama server — no API key, "
            "no cloud account, nothing leaves this machine. Install Ollama, "
            "pull a tools-capable model (e.g. ollama pull qwen3.5), and "
            "Jarvis finds it at http://localhost:11434 automatically. Point "
            "the server URL at another machine to share one Ollama box."
        ),
    ),
    ProviderSpec(
        id="local-openai",
        label="Local server (OpenAI-compatible)",
        tier="brain",
        auth_mode="none",
        secret_keys=("local_openai_api_key",),
        dashboard_url=None,
        signup_url="https://huggingface.co/docs/transformers/main/serving",
        credential_help=(
            "Any self-hosted server that speaks the OpenAI chat format: "
            "HuggingFace transformers serve (http://localhost:8000), llama.cpp "
            "llama-server (http://localhost:8080), LM Studio "
            "(http://localhost:1234), vLLM, or an existing TGI. Set the server "
            "URL on this card — no cloud account, nothing leaves your network. "
            "The API key is optional; most local servers ignore it."
        ),
        supports_base_url=True,
    ),
    # ── TTS ───────────────────────────────────────────────────────────────
    # Voice-Output cards render in this tuple order. OpenRouter leads (one key
    # reaches many vetted speech models); Inworld sits last as a premium
    # low-latency option. Inworld is a pipeline TTS voice, NOT a full-duplex
    # realtime provider (that tier needs a RealtimeProvider adapter) — the label
    # says "voice", never "realtime", so the two are not confused.
    ProviderSpec(
        id="openrouter-tts",
        label="OpenRouter (TTS)",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("openrouter_api_key",),
        dashboard_url="https://openrouter.ai/keys",
        credential_help=(
            "Same OpenRouter API key as the OpenRouter brain (starts with "
            "sk-or-) — no extra key needed. One key reaches the vetted speech "
            "models (Gemini Flash TTS, Grok Voice, MAI-Voice, Voxtral). "
            "The model picker shows ONLY allowlisted speech models; each has its own "
            "voice list. Billed per token on your OpenRouter account."
        ),
    ),
    ProviderSpec(
        id="elevenlabs",
        label="ElevenLabs",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("elevenlabs_api_key",),
        dashboard_url="https://elevenlabs.io/app/settings/api-keys",
        credential_help=(
            "ElevenLabs API key for premium multilingual voices (DE+EN via "
            "eleven_flash_v2_5). Pick one of the curated Jarvis voices in the "
            "voice picker, or paste your own ElevenLabs voice ID (e.g. a cloned "
            "voice) into it. Billed per character on your ElevenLabs account."
        ),
    ),
    ProviderSpec(
        id="gemini-flash-tts",
        label="Gemini Flash TTS",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
        credential_help=(
            "Same Google key as the Gemini brain (AIza/AQ.; a Vertex express "
            "key is detected and routed automatically). Note: the TTS preview "
            "model is hard-capped on AI Studio — use a Vertex path for "
            "production quota."
        ),
        alt_credential=_GEMINI_VERTEX,
    ),
    ProviderSpec(
        id="grok-voice",
        label="xAI Text to Speech",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("grok_api_key",),
        dashboard_url="https://console.x.ai/",
        credential_help=(
            "xAI API key (starts with xai-) for text to speech. The voice picker "
            "contains the current public built-in roster and preserves Leo as "
            "the Jarvis default."
        ),
    ),
    ProviderSpec(
        id="cartesia",
        label="Cartesia Sonic 3.5",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("cartesia_api_key",),
        dashboard_url="https://play.cartesia.ai/keys",
        credential_help=(
            "Cartesia API key (starts with sk_car_). Billed per token; "
            "multilingual Sonic voices incl. German."
        ),
    ),
    ProviderSpec(
        id="inworld",
        label="Inworld (premium voice)",
        tier="tts",
        auth_mode="api_key",
        secret_keys=("inworld_api_key",),
        dashboard_url="https://platform.inworld.ai/",
        credential_help=(
            "Inworld API key — copy it VERBATIM from platform.inworld.ai; it is "
            "already base64, do not re-encode it. Premium multilingual "
            "low-latency voices (DE Josef/Johanna, EN Dennis/Ashley, ES "
            "Diego/Lupita). Billed per character on your Inworld account."
        ),
    ),
    # Local, keyless TTS. Same readiness contract as the local STT cards: the
    # payload carries a real on-disk probe, and "ready" means one voice per
    # SUPPORTED LANGUAGE is downloaded — a Piper voice speaks exactly one
    # language, so a German-only install would leave the assistant mute the
    # moment the conversation switches to English.
    ProviderSpec(
        id="piper-local",
        label="Piper (on this machine)",
        tier="tts",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        signup_url=None,
        credential_help=(
            "Speaks on this machine — no API key, no cloud account, nothing "
            "sent anywhere. Piper is the established offline voice engine: "
            "small neural voices that run faster than real time even without a "
            "graphics card. The download is about 200 MB and brings one voice "
            "each for German, English and Spanish. The voices sound good rather "
            "than indistinguishable from a person; a hosted provider is still "
            "the more natural-sounding option."
        ),
    ),
    # ── STT ───────────────────────────────────────────────────────────────
    ProviderSpec(
        id="openai-api",
        label="OpenAI Whisper STT",
        tier="stt",
        auth_mode="api_key",
        secret_keys=("openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        credential_help=(
            "Uses your OpenAI API key (shared with the GPT brain) for Whisper "
            "speech-to-text."
        ),
    ),
    ProviderSpec(
        id="openrouter-stt",
        label="OpenRouter (STT)",
        tier="stt",
        auth_mode="api_key",
        secret_keys=("openrouter_api_key",),
        dashboard_url="https://openrouter.ai/keys",
        credential_help=(
            "Uses your OpenRouter API key (shared with the OpenRouter brain — "
            "no second key needed) for cloud transcription. The model picker "
            "lists only transcription-capable models."
        ),
    ),
    # Local, keyless STT. This card was REMOVED once (2026-07-03, v1.0.1) and is
    # back under the condition that made the removal necessary: it may never
    # claim a readiness nobody verified. Both original defects are fixed at the
    # source rather than in the card — the payload carries a real
    # ``local_runtime`` probe (engine importable AND checkpoint downloaded, see
    # jarvis.speech.local_models), and there is no seven-checkpoint picker at
    # all: exactly ONE model is offered, the one the install actually fetches.
    # NOTE: the wake word is a separate path with its own small checkpoint
    # ([stt].wake_*, build_wake_whisper) and is unaffected by this card;
    # _build_local_fallback likewise stays the invisible key-free floor (AP-22).
    ProviderSpec(
        id="faster-whisper",
        label="Whisper (on this machine)",
        tier="stt",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        signup_url=None,
        credential_help=(
            "Transcribes your speech on this machine — no API key, no cloud "
            "account, no audio leaving the device. Runs Whisper large-v3, the "
            "full multilingual model. It needs a one-time download of about "
            "3 GB and is slower than a hosted provider on a machine without a "
            "graphics card, which is the trade for keeping everything local."
        ),
    ),
    ProviderSpec(
        id="nemotron-local",
        label="Nemotron (on this machine)",
        tier="stt",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        signup_url=None,
        credential_help=(
            "The faster on-device option: NVIDIA's Nemotron 3.5 streaming "
            "model, also fully local and keyless. It covers 40 languages "
            "including German, needs a ~690 MB download instead of 3 GB, and "
            "transcribes several times faster than real time on a plain CPU — "
            "no graphics card required. Pick Whisper instead if you want the "
            "highest accuracy and have the hardware for it."
        ),
    ),
    # Deliberately last in the Voice Input picker and last among cloud runtime
    # fallbacks. It remains available for users who only have a Groq key, but
    # live German dictation has been substantially less reliable than the other
    # hosted choices and must not be presented as a preferred takeover.
    ProviderSpec(
        id="groq-api",
        label="Groq STT (Whisper)",
        tier="stt",
        auth_mode="api_key",
        secret_keys=("groq_api_key",),
        dashboard_url="https://console.groq.com/keys",
        credential_help=(
            "Groq API key (starts with gsk_). Fast hosted Whisper "
            "speech-to-text, billed per token."
        ),
        caution=(
            "Not recommended for primary transcription: real-world dictation "
            "quality has been less reliable than the other hosted choices."
        ),
    ),
    # ── Dictation polish ──────────────────────────────────────────────────
    # Derived from POLISH_FAMILIES above, in that tuple's order (Groq first —
    # it is the recommended pick because its key is usually already there).
    # OPTIONAL by construction: this tier reads a key it never demands, and a
    # missing one leaves dictation behaving exactly as it did before.
    *_DICTATION_PROVIDERS,
    # ── Realtime ──────────────────────────────────────────────────────────
    # Realtime voice providers share the provider-neutral RealtimeProvider
    # contract. Subscription and API billing remain independently selectable.
    # The former "ChatGPT subscription (Codex)" realtime card was removed
    # 2026-08-10 together with its plugin: Codex's experimental realtime
    # surface never held a dependable call. Subscription voice continues as
    # the stable classic-pipeline composition (voice.profile =
    # "codex-subscription-voice") over the audited app-server text protocol.
    ProviderSpec(
        id="openai-realtime",
        label="OpenAI Realtime",
        tier="realtime",
        auth_mode="api_key",
        secret_keys=("realtime_openai_api_key",),
        dashboard_url="https://platform.openai.com/api-keys",
        credential_help=(
            "OpenAI API key dedicated to Realtime Voice. It is not reused by "
            "Brain, STT, or Jarvis-Agents. Existing shared OpenAI credentials "
            "remain a compatibility fallback until a dedicated key is saved."
        ),
    ),
    ProviderSpec(
        id="gemini-live",
        label="Gemini Live",
        tier="realtime",
        auth_mode="api_key",
        secret_keys=("realtime_gemini_api_key",),
        dashboard_url="https://aistudio.google.com/app/apikey",
        credential_help=(
            "Google AI Studio key dedicated to Gemini Live. It is not reused by "
            "the Gemini Brain or Jarvis-Agents. Existing shared Gemini credentials "
            "remain a compatibility fallback until a dedicated key is saved."
        ),
    ),
    # The local option this tier lacked entirely: every other realtime card
    # bills a hosted account, so an install running brain, recognizer and voice
    # on its own hardware still had to leave the machine for the low-latency
    # voice mode. Protocol-shaped, never product-shaped — it names no project
    # and bundles no server.
    ProviderSpec(
        id="local-realtime",
        label="Self-hosted realtime (OpenAI-compatible)",
        tier="realtime",
        auth_mode="none",
        secret_keys=(),
        dashboard_url=None,
        signup_url="https://platform.openai.com/docs/guides/realtime",
        supports_base_url=True,
        credential_help=(
            "Fully local voice calls, no cloud account. Easiest path: the "
            "install panel below checks this machine (12 GB of GPU/unified "
            "memory is the minimum for a good experience) and sets up the "
            "managed server with one click — download size and expected "
            "latency are shown before anything is fetched. Alternatively, "
            "paste the address of ANY server that speaks the OpenAI Realtime "
            "WebSocket protocol (ws://… works, so does http://localhost:8765). "
            "The model and voice come from the server itself; leave them on "
            "'auto' unless it serves several. If your server requires a "
            "token, export JARVIS_LOCAL_REALTIME_API_KEY — most need none."
        ),
        experimental=True,
    ),
    # grok-realtime was removed 2026-07-16 (BUG-064: the xAI realtime server
    # drops its session contract after any response cancel and goes deaf).
)


def get_spec(provider_id: str) -> ProviderSpec | None:
    """Look up a spec by ID. None if unknown."""
    for spec in PROVIDERS:
        if spec.id == provider_id:
            return spec
    return None


def all_secret_keys() -> set[str]:
    """Set of all secret keys referenced by the declared provider specs."""
    return {key for spec in PROVIDERS for key in spec.secret_keys}


def secret_slot_consumers(slot: str) -> list[str]:
    """Labels of every provider surface that can read ``slot`` at runtime.

    Union of (a) specs listing the slot literally in ``secret_keys`` and (b)
    provider families whose runtime fallback chain
    (``config.PROVIDER_SECRET_CANDIDATES``) contains it. Powers the
    shared-key delete warning in the API-Keys UI: deleting ``openai_api_key``
    from the STT card silently disabled the Brain and Tool Model too.
    """
    from jarvis.core.config import PROVIDER_SECRET_CANDIDATES

    labels: dict[str, None] = {}
    for spec in PROVIDERS:
        if slot in spec.secret_keys:
            labels.setdefault(spec.label)
    for family, candidates in PROVIDER_SECRET_CANDIDATES.items():
        if any(candidate_slot == slot for candidate_slot, _env in candidates):
            family_spec = get_spec(family)
            labels.setdefault(family_spec.label if family_spec else family)
    return list(labels)
