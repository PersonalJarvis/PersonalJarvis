"""Command Registry — ONE machine-readable catalog of user-facing app commands.

The registry is the single source of truth that lets every surface agree on
what a "command" is (the AP-4 anti-drift class):

- the brain's ``app-command`` router tool (pipeline voice/chat) exposes the
  catalog to the LLM as an enum-constrained schema,
- ``GET /api/commands`` serves it to the desktop UI (and, via the dynamic
  OpenAPI layer, to the ``jarvis`` CLI),
- ``scripts/ci/gen_commands_reference.py`` renders it into
  ``docs/commands-reference.md`` (drift-gated),
- Phase B wires the same catalog into the realtime engines' tool calling.

Every command maps to exactly ONE already-mounted, already-validated REST
endpoint — the registry never grows its own execution logic, so command
behavior can never drift from what the UI button for the same action does.
Parity tests (tests/unit/commands/) assert every entry's endpoint exists in
the live OpenAPI schema and every ``ui_section`` is a real sidebar section.

Latency & footprint: the catalog is plain in-process data built lazily on
first access (AP-26 — nothing here touches the boot critical path) and
measures a few KB.

Language note: ``voice_aliases`` values are speech-recognition INPUT
vocabulary (CLAUDE.md §1 closed list #3) and therefore may be non-English;
every other string in this module is English.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from typing import Any

# Kept static for zero-import-cost; parity-tested against
# jarvis.brain.manager.SUPPORTED_REPLY_LANGUAGES (the authoritative tuple).
REPLY_LANGUAGES: tuple[str, ...] = ("auto", "de", "en", "es")

VOICE_MODES: tuple[str, ...] = ("pipeline", "realtime")


@dataclass(frozen=True)
class AppCommand:
    """One user-facing app command, bound to exactly one REST endpoint."""

    id: str                    # stable kebab-case identifier
    title: str                 # short human-readable title (EN)
    description: str           # one-liner for the LLM schema + docs (EN)
    method: str                # HTTP method of the backing endpoint
    path: str                  # endpoint path, may contain {placeholders}
    params: dict[str, Any] = field(default_factory=dict)  # JSON schema (object)
    path_params: tuple[str, ...] = ()  # args substituted into the path
    dangerous: bool = False    # True → requires explicit confirmation
    worker_allowed: bool = False  # Explicit least-privilege Jarvis-Agent grant
    ui_section: str = "settings"  # sidebar section hosting the same action
    voice_aliases: dict[str, tuple[str, ...]] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "description": self.description,
            "method": self.method,
            "path": self.path,
            "params": self.params,
            "path_params": list(self.path_params),
            "dangerous": self.dangerous,
            "worker_allowed": self.worker_allowed,
            "ui_section": self.ui_section,
            "voice_aliases": {k: list(v) for k, v in self.voice_aliases.items()},
        }


def _provider_ids(tier: str, *, brain_switchable_only: bool = False) -> list[str]:
    """Provider ids for ``tier`` from the static catalog; [] when unavailable.

    Lazy import: the provider catalog is pure data, but this module must stay
    importable (docs generation, tests) without pulling the UI layer eagerly.
    """
    try:
        from jarvis.ui.web.provider_spec import PROVIDERS
    except Exception:  # pragma: no cover - defensive: registry must not crash
        return []
    ids = [
        p.id
        for p in PROVIDERS
        if p.tier == tier
        and (not brain_switchable_only or getattr(p, "brain_switchable", True))
    ]
    return sorted(ids)


def _coding_agent_ids() -> list[str]:
    """Coding CLIs a workspace pane can run, from the ONE agent registry.

    Never a literal list. A registered CLI that the voice schema does not know
    about is unreachable by voice while being fully supported everywhere else —
    the pane opens from the UI, resumes, accepts prompts, and is simply absent
    from the one surface that is supposed to drive it. Reading the registry is
    what lets a newly registered agent be spawned the day it lands, without a
    second table remembering to agree (the same rule §5 sets for providers:
    gate on the capability, never on a name someone typed twice).
    """
    try:
        from jarvis.workspace.agents import coding_agent_names
    except Exception:  # pragma: no cover - defensive: registry must not crash
        return []
    return sorted(coding_agent_names())


def _all_provider_ids() -> list[str]:
    try:
        from jarvis.ui.web.provider_spec import PROVIDERS
    except Exception:  # pragma: no cover - defensive
        return []
    return sorted(p.id for p in PROVIDERS)


def _str_param(description: str, *, enum: list[str] | None = None,
               min_length: int | None = None, max_length: int | None = None,
               ) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string", "description": description}
    if enum:
        schema["enum"] = enum
    if min_length is not None:
        schema["minLength"] = min_length
    if max_length is not None:
        schema["maxLength"] = max_length
    return schema


def _provider_switch_params(
    tier: str,
    *,
    brain_switchable_only: bool = False,
    allow_experimental_ack: bool = False,
) -> dict[str, Any]:
    enum = _provider_ids(tier, brain_switchable_only=brain_switchable_only)
    properties: dict[str, Any] = {
        "provider": _str_param(
            f"Target {tier} provider id.", enum=enum or None, min_length=1
        ),
        "persist": {
            "type": "boolean",
            "default": True,
            "description": "Persist the choice to jarvis.toml (survives restart).",
        },
    }
    if allow_experimental_ack:
        properties["accept_experimental"] = {
            "type": "boolean",
            "default": False,
            "description": "Explicitly acknowledge an experimental provider transport.",
        }
    return {
        "type": "object",
        "properties": properties,
        "required": ["provider"],
    }


def _build_registry() -> tuple[AppCommand, ...]:
    """Assemble the curated v1 command set (high-value commands first —
    the long tail stays reachable through the dynamic CLI ``api`` layer)."""
    return (
        # ------------------------------------------------------ providers
        AppCommand(
            id="brain-switch",
            title="Switch brain provider",
            description=(
                "Switch the ACTIVE main brain (LLM) provider, e.g. from openai "
                "to claude-api. Reversible; validated against the provider "
                "catalog and stored credentials."
            ),
            method="POST",
            path="/api/brain/switch",
            params=_provider_switch_params("brain", brain_switchable_only=True),
            ui_section="apikeys",
            voice_aliases={
                "de": ("wechsle den brain-provider zu claude",),  # i18n-allow: input vocab
                "en": ("switch the brain provider to claude",),
                "es": ("cambia el proveedor del cerebro a claude",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="tts-switch",
            title="Switch voice (TTS) provider",
            description="Switch the active text-to-speech provider (live, no restart).",
            method="POST",
            path="/api/tts/switch",
            params=_provider_switch_params("tts"),
            ui_section="apikeys",
            voice_aliases={
                "de": ("wechsle die stimme zu elevenlabs",),  # i18n-allow: input vocab
                "en": ("switch the voice to elevenlabs",),
                "es": ("cambia la voz a elevenlabs",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="stt-switch",
            title="Switch speech-recognition (STT) provider",
            description=(
                "Switch the speech-to-text provider. Takes effect on the next "
                "voice-pipeline start (restart required)."
            ),
            method="POST",
            path="/api/stt/switch",
            params=_provider_switch_params("stt"),
            ui_section="apikeys",
            voice_aliases={
                "de": ("wechsle die spracherkennung zu deepgram",),  # i18n-allow: input vocab
                "en": ("switch speech recognition to deepgram",),
                "es": ("cambia el reconocimiento de voz a deepgram",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="realtime-switch",
            title="Switch realtime voice provider",
            description=(
                "Switch which realtime voice engine (speech-to-speech) is "
                "active, including subscription- and API-backed providers. "
                "Experimental transports require explicit acknowledgement."
            ),
            method="POST",
            path="/api/realtime/switch",
            params=_provider_switch_params(
                "realtime",
                allow_experimental_ack=True,
            ),
            ui_section="apikeys",
            voice_aliases={
                "de": ("wechsle das realtime-modell zu gemini",),  # i18n-allow: input vocab
                "en": ("switch the realtime model to gemini",),
                "es": ("cambia el modelo en tiempo real a gemini",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="local-realtime-models-set",
            title="Configure local realtime models",
            description=(
                "Select the Ollama brain and speech model for the managed local "
                "realtime server, then activate them only after a voice test."
            ),
            method="POST",
            path="/api/providers/local-realtime/managed-server/setup",
            params={
                "type": "object",
                "properties": {
                    "brain_model": _str_param(
                        "Exact Ollama model tag.", min_length=1, max_length=201
                    ),
                    "voice_model": _str_param(
                        "Managed speech-model profile id.",
                        enum=["qwen3-tts-1.7b", "qwen3-tts-0.6b"],
                    ),
                },
                "required": ["brain_model", "voice_model"],
            },
            dangerous=True,
            ui_section="apikeys",
            voice_aliases={
                "de": ("stelle die lokalen sprachmodelle um",),  # i18n-allow: input vocab
                "en": ("change the local realtime models",),
                "es": ("cambia los modelos de voz locales",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="computer-use-switch",
            title="Switch Computer-Use provider",
            description=(
                "Switch the dedicated Computer-Use planner provider (screen "
                "control), decoupled from the main brain."
            ),
            method="POST",
            path="/api/computer-use/switch",
            params=_provider_switch_params("brain"),
            ui_section="apikeys",
            voice_aliases={
                "de": ("wechsle den computer-use-provider zu gemini",),  # i18n-allow: input vocab
                "en": ("switch the computer use provider to gemini",),
                "es": ("cambia el proveedor de computer use a gemini",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="jarvis-agent-switch",
            title="Switch mission-worker provider",
            description=(
                "Switch the provider used for new missions (e.g. codex to "
                "openai). The next mission uses the new provider."
            ),
            method="POST",
            path="/api/jarvis-agent/switch",
            params=_provider_switch_params("brain"),
            ui_section="agents",
            voice_aliases={
                "de": ("wechsle den agent-provider zu openai",),  # i18n-allow: input vocab
                "en": ("switch the agent provider to openai",),
                "es": ("cambia el proveedor del agente a openai",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="providers-list",
            title="List providers",
            description="List all configured providers and which ones are active.",
            method="GET",
            path="/api/providers",
            worker_allowed=True,
            ui_section="apikeys",
            voice_aliases={
                "de": ("welche provider sind konfiguriert",),  # i18n-allow: input vocab
                "en": ("which providers are configured",),
                "es": ("qué proveedores están configurados",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="provider-test",
            title="Test a provider",
            description="Test connectivity and authentication for one provider.",
            method="POST",
            path="/api/providers/{provider_id}/test",
            params={
                "type": "object",
                "properties": {
                    "provider_id": _str_param(
                        "Provider id to test.", enum=_all_provider_ids() or None,
                        min_length=1,
                    ),
                },
                "required": ["provider_id"],
            },
            path_params=("provider_id",),
            worker_allowed=True,
            ui_section="apikeys",
            voice_aliases={
                "de": ("teste den openai-provider",),  # i18n-allow: input vocab
                "en": ("test the openai provider",),
                "es": ("prueba el proveedor de openai",),  # i18n-allow: input vocab
            },
        ),
        # ------------------------------------------------- voice & language
        AppCommand(
            id="reply-language-set",
            title="Set reply language",
            description=(
                "Pin the language Jarvis answers in (auto follows the spoken "
                "language)."
            ),
            method="PUT",
            path="/api/settings/reply-language",
            params={
                "type": "object",
                "properties": {
                    "language": _str_param(
                        "Reply language.", enum=list(REPLY_LANGUAGES)
                    ),
                    "persist": {
                        "type": "boolean", "default": True,
                        "description": "Persist as boot default.",
                    },
                },
                "required": ["language"],
            },
            ui_section="languages",
            voice_aliases={
                "de": ("antworte ab jetzt auf englisch",),  # i18n-allow: input vocab
                "en": ("answer in german from now on",),
                "es": ("responde en inglés a partir de ahora",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="voice-mode-set",
            title="Set voice mode (pipeline / realtime)",
            description=(
                "Choose the voice engine: the classic STT-brain-TTS pipeline "
                "or a realtime speech-to-speech model."
            ),
            method="PUT",
            path="/api/settings/voice-mode",
            params={
                "type": "object",
                "properties": {
                    "mode": _str_param("Voice mode.", enum=list(VOICE_MODES)),
                    "persist": {
                        "type": "boolean", "default": True,
                        "description": "Persist as boot default.",
                    },
                },
                "required": ["mode"],
            },
            ui_section="settings",
            voice_aliases={
                "de": ("schalte auf den realtime-modus um",),  # i18n-allow: input vocab
                "en": ("switch to realtime mode",),
                "es": ("cambia al modo en tiempo real",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="wake-word-get",
            title="Show wake word",
            description="Show the current wake word and wake-engine settings.",
            method="GET",
            path="/api/settings/wake-word",
            worker_allowed=True,
            ui_section="settings",
            voice_aliases={
                "de": ("wie lautet mein wake word",),  # i18n-allow: input vocab
                "en": ("what is my wake word",),
                "es": ("cuál es mi palabra de activación",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="wake-word-set",
            title="Change wake word",
            description="Set the phrase that wakes Jarvis up.",
            method="PUT",
            path="/api/settings/wake-word",
            params={
                "type": "object",
                "properties": {
                    "phrase": _str_param(
                        "The new wake phrase.", min_length=1, max_length=64
                    ),
                },
                "required": ["phrase"],
            },
            ui_section="settings",
            voice_aliases={
                "de": ("ändere mein wake word zu nova",),  # i18n-allow: input vocab
                "en": ("change my wake word to nova",),
                "es": ("cambia mi palabra de activación a nova",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="tts-volume-set",
            title="Set voice volume",
            description="Set the text-to-speech output volume (0.0 to 1.0).",
            method="PUT",
            path="/api/settings/tts-volume",
            params={
                "type": "object",
                "properties": {
                    "volume": {
                        "type": "number", "minimum": 0.0, "maximum": 1.0,
                        "description": "Output volume between 0.0 and 1.0.",
                    },
                    "persist": {
                        "type": "boolean", "default": True,
                        "description": "Persist as boot default.",
                    },
                },
                "required": ["volume"],
            },
            ui_section="settings",
            voice_aliases={
                "de": ("stell die lautstärke auf 50 prozent",),  # i18n-allow: input vocab
                "en": ("set the voice volume to 50 percent",),
                "es": ("pon el volumen de la voz al 50 por ciento",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="audio-devices-list",
            title="List audio devices",
            description="List available speaker and microphone devices.",
            method="GET",
            path="/api/settings/audio-devices",
            worker_allowed=True,
            ui_section="settings",
            voice_aliases={
                "de": ("welche audiogeräte gibt es",),  # i18n-allow: input vocab
                "en": ("list my audio devices",),
                "es": ("qué dispositivos de audio hay",),  # i18n-allow: input vocab
            },
        ),
        # ------------------------------------------------ knowledge & history
        AppCommand(
            id="wiki-ingest",
            title="Store a fact in the Wiki",
            description=(
                "Store one self-contained fact or summary through the guarded "
                "Wiki curator. The command succeeds only after a page is written."
            ),
            method="POST",
            path="/api/wiki/ingest",
            worker_allowed=True,
            params={
                "type": "object",
                "properties": {
                    "text": _str_param(
                        "Self-contained fact or summary to store.",
                        min_length=12,
                        max_length=32_000,
                    ),
                    "source": _str_param(
                        "Optional short audit label for the content source.",
                        min_length=1,
                        max_length=128,
                    ),
                },
                "required": ["text"],
            },
            ui_section="memory",
            voice_aliases={
                "de": ("trag das in mein wiki ein",),  # i18n-allow: input vocab
                "en": ("store that in my wiki",),
                "es": ("guarda eso en mi wiki",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="ultrawiki-ask",
            title="Ask the knowledge base",
            description=(
                "Answer one question from UltraWiki evidence and return the "
                "numbered source citations used for the answer."
            ),
            method="POST",
            path="/api/ultrawiki/ask",
            worker_allowed=True,
            params={
                "type": "object",
                "properties": {
                    "question": _str_param(
                        "Question to answer from the knowledge base.",
                        min_length=1,
                        max_length=4_000,
                    ),
                    "k": {
                        "type": "integer",
                        "description": "Maximum evidence items to retrieve (1-20).",
                        "minimum": 1,
                        "maximum": 20,
                    },
                    "area": _str_param(
                        "Optional UltraWiki area id.",
                        max_length=200,
                    ),
                },
                "required": ["question"],
            },
            ui_section="memory",
            voice_aliases={
                "de": ("frag mein ultrawiki",),  # i18n-allow: input vocab
                "en": ("ask my ultrawiki",),
                "es": ("pregunta a mi ultrawiki",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="people-list",
            title="List the people I know",
            description=(
                "List the people the knowledge base has identified, newest "
                "evidence merged, optionally filtered by a name or an "
                "identifier (an e-mail address, a phone number). Use this to "
                "answer 'who do you know about' and to look up which stored "
                "person a spoken name refers to."
            ),
            method="GET",
            path="/api/ultrawiki/identity/people",
            params={
                "type": "object",
                "properties": {
                    "q": _str_param(
                        "Optional filter over display names and identifiers.",
                        max_length=200,
                    ),
                    "limit": {
                        "type": "integer",
                        "description": "Maximum people to return (1-1000).",
                        "minimum": 1,
                        "maximum": 1000,
                    },
                },
            },
            ui_section="memory",
            voice_aliases={
                "de": ("welche personen kennst du",),  # i18n-allow: input vocab
                "en": ("which people do you know about",),
                "es": ("qué personas conoces",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="person-profile",
            title="Show what I know about one person",
            description=(
                "Read one identified person in full: every known name, e-mail, "
                "phone and handle, which identities were merged into them, and "
                "which merge proposals are still open. Take the id from "
                "people-list; a merged-away id forwards to the surviving person."
            ),
            method="GET",
            path="/api/ultrawiki/identity/people/{entity_id}",
            params={
                "type": "object",
                "properties": {
                    "entity_id": {
                        "type": "integer",
                        "description": "Person id as returned by people-list.",
                        "minimum": 1,
                    },
                },
                "required": ["entity_id"],
            },
            path_params=("entity_id",),
            ui_section="memory",
            voice_aliases={
                "de": ("was weißt du über diese person",),  # i18n-allow: input vocab
                "en": ("what do you know about this person",),
                "es": ("qué sabes de esta persona",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="identity-queue-list",
            title="List possible duplicate people",
            description=(
                "List the pairs the knowledge base suspects are the same "
                "person but refused to merge on its own, strongest evidence "
                "first. Nothing here has been merged — each pair waits for a "
                "human decision."
            ),
            method="GET",
            path="/api/ultrawiki/identity/queue",
            params={
                "type": "object",
                "properties": {
                    "status": _str_param(
                        "Which proposals to list.",
                        enum=["pending", "confirmed", "rejected", "all"],
                    ),
                    "limit": {
                        "type": "integer",
                        "description": "Maximum proposals to return (1-1000).",
                        "minimum": 1,
                        "maximum": 1000,
                    },
                },
            },
            ui_section="memory",
            voice_aliases={
                "de": ("welche personen könnten dieselbe sein",),  # i18n-allow: input vocab
                "en": ("which people might be the same person",),
                "es": ("qué personas podrían ser la misma",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="session-latest-turn",
            title="Show latest voice turn",
            description=(
                "Return the latest persisted user transcript and its complete "
                "voice turn, optionally restricted to one session."
            ),
            method="GET",
            path="/api/sessions/latest-turn",
            worker_allowed=True,
            params={
                "type": "object",
                "properties": {
                    "session_id": _str_param(
                        "Optional voice-session id.", min_length=1, max_length=128
                    ),
                },
            },
            ui_section="sessions",
            voice_aliases={
                "de": ("lies die letzte transkription",),  # i18n-allow: input vocab
                "en": ("read the latest transcript",),
                "es": ("lee la última transcripción",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="tools-list",
            title="List effective tools",
            description=(
                "Return the effective live Brain tool surface, including native, "
                "connected CLI, Marketplace, and MCP tools."
            ),
            method="GET",
            # The brief listing, deliberately not /api/tools: the full route
            # serializes every schema (50-200k chars) and the tool-result cap
            # slices that mid-JSON (2026-07-28 cost audit).
            path="/api/tools/brief",
            worker_allowed=True,
            params={"type": "object", "properties": {}},
            ui_section="settings",
            voice_aliases={
                "de": ("welche tools mcps und clis sind verbunden",),  # i18n-allow: input vocab
                "en": ("list the connected tools mcps and clis",),
                "es": ("lista las herramientas mcps y clis conectadas",),  # i18n-allow: input vocab
            },
        ),
        # -------------------------------------------------------- dictation
        AppCommand(
            id="dictation-start",
            title="Start dictation",
            description=(
                "Start dictation: speak, and the transcribed text is inserted "
                "into whatever text field currently has focus. Stops with "
                "dictation-stop or the dictation shortcut."
            ),
            method="POST",
            path="/api/dictation/start",
            params={
                "type": "object",
                "properties": {
                    "target": {
                        "type": "string",
                        "enum": ["insert", "chat"],
                        "description": (
                            "insert = paste into the app in front; "
                            "chat = fill the chat input only"
                        ),
                    }
                },
            },
            ui_section="dictation",
            voice_aliases={
                "de": ("starte das diktat", "diktier-modus an"),  # i18n-allow: input vocab
                "en": ("start dictation", "turn on dictation mode"),
                "es": ("inicia el dictado", "activa el modo dictado"),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="dictation-stop",
            title="Stop dictation",
            description="Finish the running dictation and deliver the text.",
            method="POST",
            path="/api/dictation/stop",
            ui_section="dictation",
            voice_aliases={
                "de": ("stopp das diktat", "diktier-modus aus"),  # i18n-allow: input vocab
                "en": ("stop dictation", "turn off dictation mode"),
                "es": ("detén el dictado", "desactiva el modo dictado"),  # i18n-allow: input vocab
            },
        ),
        # ----------------------------------------------------------- system
        AppCommand(
            id="app-restart",
            title="Restart Jarvis",
            description="Restart the Jarvis desktop app (voice + UI restart too).",
            method="POST",
            path="/api/settings/restart-app",
            dangerous=True,
            ui_section="settings",
            voice_aliases={
                "de": ("starte jarvis neu",),  # i18n-allow: input vocab
                "en": ("restart jarvis",),
                "es": ("reinicia jarvis",),  # i18n-allow: input vocab
            },
        ),
        # ----------------------------------------------- missions & tasks
        AppCommand(
            id="missions-list",
            title="List missions",
            description="List Jarvis-Agent missions and their status.",
            method="GET",
            path="/api/missions",
            worker_allowed=True,
            ui_section="agents",
            voice_aliases={
                "de": ("zeig mir die missionen",),  # i18n-allow: input vocab
                "en": ("show me the missions",),
                "es": ("muéstrame las misiones",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="mission-result",
            title="Read a mission result",
            description=(
                "Read the signed summary and actual deliverable contents of one "
                "completed Jarvis-Agent mission. Use this after listing missions "
                "when the user asks what a mission found or produced."
            ),
            method="GET",
            path="/api/missions/{mission_id}/result",
            worker_allowed=True,
            params={
                "type": "object",
                "properties": {
                    "mission_id": _str_param(
                        "Mission id whose result should be read.", min_length=1
                    ),
                },
                "required": ["mission_id"],
            },
            path_params=("mission_id",),
            ui_section="agents",
            voice_aliases={
                "de": ("was hat die mission herausgefunden",),  # i18n-allow: input vocab
                "en": ("what did the mission find",),
                "es": ("qué encontró la misión",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="mission-cancel",
            title="Cancel a mission",
            description="Cancel a running Jarvis-Agent mission by id.",
            method="POST",
            path="/api/missions/{mission_id}/cancel",
            params={
                "type": "object",
                "properties": {
                    "mission_id": _str_param("Mission id to cancel.", min_length=1),
                },
                "required": ["mission_id"],
            },
            path_params=("mission_id",),
            dangerous=True,
            ui_section="agents",
            voice_aliases={
                "de": ("brich die mission ab",),  # i18n-allow: input vocab
                "en": ("cancel the mission",),
                "es": ("cancela la misión",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="tasks-list",
            title="List tasks",
            description="List scheduled and running tasks.",
            method="GET",
            path="/api/tasks",
            worker_allowed=True,
            ui_section="tasks",
            voice_aliases={
                "de": ("zeig mir meine aufgaben",),  # i18n-allow: input vocab
                "en": ("show me my tasks",),
                "es": ("muéstrame mis tareas",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="task-cancel",
            title="Cancel a task",
            description="Cancel a running or scheduled task by id.",
            method="POST",
            path="/api/tasks/{task_id}/cancel",
            params={
                "type": "object",
                "properties": {
                    "task_id": _str_param("Task id to cancel.", min_length=1),
                },
                "required": ["task_id"],
            },
            path_params=("task_id",),
            dangerous=True,
            ui_section="tasks",
            voice_aliases={
                "de": ("brich die aufgabe ab",),  # i18n-allow: input vocab
                "en": ("cancel the task",),
                "es": ("cancela la tarea",),  # i18n-allow: input vocab
            },
        ),
        # ------------------------------------------------------- agentic IDE
        # The Agentic IDE runs coding agents in named terminals; these three
        # commands are what make the workspace addressable by voice. Reading is
        # free (status / report); writing is one narrow channel — type a prompt
        # into a named terminal — and it can only ever type text plus Enter (the
        # endpoint strips control characters, so voice cannot interrupt or kill
        # an agent). No entry here is worker_allowed: a mission worker has no
        # business steering the user's interactive coding panes (AP-5).
        AppCommand(
            id="agentic-ide-status",
            title="Agentic IDE status",
            description=(
                "Report the open Agentic-IDE workspace: which folder, which "
                "coding agents run in which named terminals, and whether the "
                "focused coding mode is on."
            ),
            method="GET",
            # The brief projection, deliberately not /state: the full state is
            # a ~25 000-character UI snapshot whose tail the tool-result cap
            # slices off mid-JSON — the model paid ~2 000 input tokens per
            # loop iteration for a broken fragment (2026-07-28 cost audit).
            path="/api/agentic-ide/state/brief",
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("was läuft in der agentic ide",),  # i18n-allow: input vocab
                "en": ("what is running in the agentic ide",),
                "es": ("qué se está ejecutando en el ide agéntico",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-terminal-report",
            title="Report on one Agentic-IDE terminal",
            description=(
                "Read what the coding agent in a named terminal is doing — its "
                "status and its recent terminal output. Use this whenever the "
                "user asks about a terminal by name (e.g. 'what is Mika doing?')."
            ),
            method="GET",
            path="/api/agentic-ide/terminals/{name}/report",
            params={
                "type": "object",
                "properties": {
                    "name": _str_param(
                        "Call-sign of the terminal, e.g. 'Mika'.", min_length=1
                    ),
                    "lines": {
                        "type": "integer",
                        "default": 40,
                        # Model-facing ceiling only; the REST route itself
                        # clamps at 300 for the UI. Past ~60 lines the
                        # tool-result cap truncates the report anyway, so a
                        # larger ask just burns input tokens on every
                        # further loop iteration (2026-07-28 cost audit).
                        "minimum": 1,
                        "maximum": 60,
                        "description": "How many recent output lines to read.",
                    },
                },
                "required": ["name"],
            },
            path_params=("name",),
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("was macht mika",),  # i18n-allow: input vocab
                "en": ("what is mika doing",),
                "es": ("qué está haciendo mika",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-prompt",
            title="Prompt an Agentic-IDE terminal",
            description=(
                "Send an instruction to the coding agent in ONE terminal. "
                "Terminals are called T plus their place in the grid (T1, T2, "
                "T3, left to right). Use this whenever the user tells a "
                "terminal to do something ('tell T1 to ...', "
                "'T2 soll ...', "  # i18n-allow: quoted addressing example
                "'let terminal three "
                "refactor ...', 'prompt the second terminal') "
                "— that work belongs to that agent, never to a background "
                "worker. For SEVERAL terminals ('T1 and T2 both ...', 'let "
                "the two of them ...') call 'agentic-ide-fanout' instead, with "
                "every call-sign in 'terminals': it briefs them at once and "
                "reports which ones really got the work. Calling this command "
                "twice for a pair leaves the second agent idle whenever the "
                "second call is forgotten, which is the failure mode fanout "
                "exists to remove. "
                "Pass the instruction in the USER's words: everything "
                "they asked for, every constraint and file they named, nothing "
                "invented and nothing summarised away. Do NOT write the brief "
                "yourself — a prompt writer that has read this repository turns "
                "what you pass into a briefed task with the relevant files "
                "attached, and a headline you composed instead arrives at the "
                "agent as its whole assignment. "
                "Only ever names a terminal that is ALREADY running: if the "
                "call fails with 'no terminal called …', opening a pane will not "
                "create that name — use 'agentic-ide-fanout' with spawn to open "
                "and brief one in a single step, and never repeat the spawn. "
                "CHECK THE REPLY: it carries a 'submitted' flag. True means the "
                "agent accepted the prompt and started. False means the text is "
                "only sitting in that terminal's input box — say so plainly and "
                "name the terminal, never report it as done."
            ),
            method="POST",
            path="/api/agentic-ide/terminals/{name}/prompt",
            params={
                "type": "object",
                "properties": {
                    "name": _str_param(
                        "Call-sign of the terminal to prompt, e.g. 'Mika'.",
                        min_length=1,
                    ),
                    "prompt": _str_param(
                        "The instruction to type into that agent.",
                        min_length=1,
                        max_length=4000,
                    ),
                    "compose": {
                        "type": "boolean",
                        "default": True,
                        "description": (
                            "Leave this out. On by default: the instruction is "
                            "rewritten into a briefed task with this "
                            "workspace's files attached, which is what makes a "
                            "spoken sentence worth running. Set it false ONLY "
                            "for a literal keystroke that must reach the agent "
                            "unchanged, such as 'continue' or 'yes'."
                        ),
                    },
                },
                "required": ["name", "prompt"],
            },
            path_params=("name",),
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("sag mika sie soll die tests laufen lassen",),  # i18n-allow: input vocab
                "en": ("tell mika to run the tests",),
                "es": ("dile a mika que ejecute las pruebas",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-fanout",
            title="Open and brief Agentic-IDE terminals in one step",
            description=(
                "Give ONE task to coding terminals — existing ones, brand-new "
                "ones, or both — in a single call. This is the ONLY correct way "
                "to handle 'if no terminal is called that, open one and prompt "
                "it' and 'spawn N terminals and let them do X': pass the work as "
                "'instruction', the panes to brief as 'terminals', and the panes "
                "to open first as 'spawn'. Never emulate it by opening panes and "
                "then prompting — call-signs are the panes' positions (T1, T2, "
                "…) and are assigned by the workspace, so a "
                "pane you open is NOT called the name you had in mind, and "
                "re-spawning after a failed prompt just leaves blank panes "
                "behind. Set 'split' true only when the user asked for the work "
                "to be DIVIDED between the agents. "
                "CHECK THE REPLY: 'delivered' names the agents that really got "
                "the task and 'undelivered' those that did not — report both, "
                "and use the call-signs the reply gives you. A spawn repeating "
                "an instruction that recently went to panes still open is "
                "refused as a duplicate; the refusal names who is already on "
                "it — tell the user that fleet is working, do not retry."
            ),
            method="POST",
            path="/api/agentic-ide/fanout",
            params={
                "type": "object",
                "properties": {
                    "instruction": _str_param(
                        "The work the agents should do, in plain words.",
                        min_length=1,
                        max_length=4000,
                    ),
                    "terminals": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Call-signs of running terminals to brief. Leave "
                            "empty to brief only the newly opened panes."
                        ),
                    },
                    "spawn": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "count": {
                                    "type": "integer",
                                    "description": "Panes to open in this group.",
                                },
                                "agent": {
                                    "type": "string",
                                    "enum": _coding_agent_ids() or None,
                                    "description": (
                                        "Coding agent for this group; omit to "
                                        "inherit the last pane's."
                                    ),
                                },
                            },
                            "required": ["count"],
                        },
                        "description": (
                            "Panes to open before briefing, group by group. "
                            "Only the newly opened panes are briefed, so agents "
                            "already working are not interrupted."
                        ),
                    },
                    "split": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Divide the instruction into one distinct assignment "
                            "per agent instead of giving all of them the same "
                            "brief."
                        ),
                    },
                    "force": {
                        "type": "boolean",
                        "default": False,
                        "description": (
                            "Leave this out. A spawn whose instruction recently "
                            "went to panes that are still open is refused as a "
                            "duplicate, and the refusal names the panes already "
                            "working on it — report those instead of retrying. "
                            "Set true ONLY when the user explicitly asked to run "
                            "the same task again on a fresh fleet."
                        ),
                    },
                },
                "required": ["instruction"],
            },
            ui_section="agentic-ide",
            voice_aliases={
                "de": (
                    "spawne ein terminal und lass es die tests fixen",  # i18n-allow
                ),
                "en": ("open a terminal and have it fix the tests",),
                "es": ("abre una terminal y que arregle las pruebas",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-spawn-terminals",
            title="Open more Agentic-IDE terminals",
            description=(
                "Open one or more additional coding terminals in the open "
                "workspace, WITHOUT giving them work. Use this only when the "
                "user asks for bare panes ('spawn five new Claude Code "
                "terminals', 'open two more Codex terminals') — that is a "
                "request for workspace panes, never for a background worker. "
                "When the new panes are also meant to DO something, use "
                "'agentic-ide-fanout' instead, which opens and briefs them in "
                "one step. Pass count, and agent only when the user named one — "
                "the accepted ids are listed on the parameter itself, and it is "
                "the only list that is right for this install. Omitted, the new "
                "panes run whatever the "
                "last pane runs. Their call-signs are their positions in the "
                "grid (T1, T2, …), assigned by the workspace — "
                "the reply's names are the only way to address them, and calling "
                "this again never produces a name you picked. "
                "CHECK THE REPLY: 'capped' true means the workspace maximum cut "
                "the request short — say how many actually opened and name them, "
                "never report the full number as done."
            ),
            method="POST",
            path="/api/agentic-ide/terminals/batch",
            params={
                "type": "object",
                "properties": {
                    "count": {
                        "type": "integer",
                        "default": 1,
                        "description": "How many terminals to open.",
                    },
                    "agent": {
                        "type": "string",
                        "enum": _coding_agent_ids() or None,
                        "description": (
                            "Coding agent for the new panes; omit to inherit "
                            "the last pane's."
                        ),
                    },
                },
                "required": ["count"],
            },
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("spawne fünf neue terminals",),  # i18n-allow: input vocab
                "en": ("spawn five new claude code terminals",),
                "es": ("abre dos terminales de codex",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-rename-terminal",
            title="Rename an Agentic-IDE terminal",
            description=(
                "Give one running terminal another call-sign without restarting "
                "its agent or losing its conversation. Use it when the user asks "
                "to rename a pane, for example 'rename T1 to Frontend'. The old "
                "call-sign must name a terminal that is already open, and the new "
                "one must be unique inside that workspace."
            ),
            method="PATCH",
            path="/api/agentic-ide/terminals/{terminal}",
            params={
                "type": "object",
                "properties": {
                    "terminal": _str_param(
                        "Current call-sign of the terminal, e.g. 'T1'.",
                        min_length=1,
                    ),
                    "name": _str_param(
                        "New call-sign for the terminal, e.g. 'Frontend'.",
                        min_length=1,
                        max_length=40,
                    ),
                },
                "required": ["terminal", "name"],
            },
            path_params=("terminal",),
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("benenne t1 in frontend um",),  # i18n-allow: input vocab
                "en": ("rename t1 to frontend",),
                "es": ("renombra t1 a frontend",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-move-terminal",
            title="Move an Agentic-IDE terminal in the grid",
            description=(
                "Rearrange the open workspace: put one terminal at another "
                "one's place. Nothing is started or stopped — the panes keep "
                "their agents and their conversations, only where they are "
                "drawn changes. Use it for 'swap Mika and Nova', 'put Mika "
                "next to Nova', 'move Mika under Nova'. "
                "'swap' exchanges the two panes and leaves the rest of the grid "
                "alone; 'left'/'right' give the moved pane its own column beside "
                "the target; 'above'/'below' stack it in the target's column. "
                "Both names must be terminals that are already open."
            ),
            method="POST",
            path="/api/agentic-ide/terminals/{name}/move",
            params={
                "type": "object",
                "properties": {
                    "name": _str_param(
                        "Call-sign of the terminal to move, e.g. 'Mika'.",
                        min_length=1,
                    ),
                    "target": _str_param(
                        "Call-sign of the terminal it should move to, e.g. 'Nova'.",
                        min_length=1,
                    ),
                    "position": _str_param(
                        "Where it lands relative to the target.",
                        enum=["swap", "left", "right", "above", "below"],
                    ),
                },
                "required": ["name", "target"],
            },
            path_params=("name",),
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("tausche mika und nova",),  # i18n-allow: input vocab
                "en": ("swap mika and nova",),
                "es": ("intercambia mika y nova",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-close-agent-terminals",
            title="Close Agentic-IDE terminals by coding agent",
            description=(
                "Stop and remove every terminal of one coding CLI in the front "
                "workspace. Use only when the user explicitly asks to close all "
                "Claude Code or all Codex terminals; this is destructive and "
                "requires confirmation."
            ),
            method="DELETE",
            path="/api/agentic-ide/terminals/agent/{agent}",
            params={
                "type": "object",
                "properties": {
                    "agent": _str_param(
                        "Coding agent whose terminals should be closed.",
                        enum=_coding_agent_ids() or None,
                    ),
                },
                "required": ["agent"],
            },
            path_params=("agent",),
            dangerous=True,
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("schließe alle codex terminals",),  # i18n-allow: input vocab
                "en": ("close all codex terminals",),
                "es": ("cierra todas las terminales de codex",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-focus",
            title="Toggle Agentic-IDE focus mode",
            description=(
                "Turn the focused coding mode on or off. While on, answers are "
                "given inside the open coding workspace; turning it off returns "
                "to normal behaviour without stopping any agent."
            ),
            method="PUT",
            path="/api/agentic-ide/mode",
            params={
                "type": "object",
                "properties": {
                    "enabled": {
                        "type": "boolean",
                        "description": "True enters focused coding mode, False leaves it.",
                    },
                },
                "required": ["enabled"],
            },
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("geh in den coding modus",),  # i18n-allow: input vocab
                "en": ("switch into coding mode",),
                "es": ("entra en el modo de programación",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-resume",
            title="Resume the last Agentic-IDE workspace",
            description=(
                "Reopen the coding workspace that was last open: the same "
                "folder, the same named terminals in the same grid positions, "
                "running the same coding CLIs — and continuing the same "
                "conversations wherever that CLI supports it. Use this when the "
                "user asks for their terminals or their coding session back "
                "after closing the window, restarting the app, or rebooting. "
                "CHECK THE REPLY: 'resumable_count' is how many panes actually "
                "continued their conversation and 'started_fresh' how many "
                "reopened empty. Name the empty ones — an agent that lost its "
                "history looks exactly like one that did not until it is asked "
                "a follow-up question."
            ),
            method="POST",
            path="/api/agentic-ide/resume",
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("stell meine terminals wieder her",),  # i18n-allow: input vocab
                "en": ("resume all my coding sessions",),
                "es": ("restaura mis terminales",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-interrupted",
            title="List interrupted Agentic-IDE sessions",
            description=(
                "Which coding terminals came back holding their conversation "
                "and have been told nothing since. That is what a restart "
                "leaves behind: reopening a workspace reconnects each pane to "
                "the conversation it was having, but the coding CLI reads that "
                "transcript and then WAITS at its prompt — so an agent stopped "
                "mid-task looks exactly like one that finished. Use this to "
                "answer 'what was interrupted?' before continuing anything. "
                "'continuable' is per pane: a pane whose agent is not running "
                "cannot be typed into, and 'blocked_reason' says why."
            ),
            method="GET",
            path="/api/agentic-ide/interrupted",
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("was wurde unterbrochen",),  # i18n-allow: input vocab
                "en": ("which coding sessions were interrupted",),
                "es": ("qué sesiones se interrumpieron",),  # i18n-allow: input vocab
            },
        ),
        AppCommand(
            id="agentic-ide-continue-interrupted",
            title="Continue interrupted Agentic-IDE sessions",
            description=(
                "Tell the coding terminals a restart left standing still to "
                "carry on: 'continue' is typed into each one and submitted. "
                "With no names, every interrupted pane in every open workspace "
                "— which is the shape of the problem, since a restart stops "
                "them all at once. CHECK THE REPLY: 'continued' really started, "
                "'queued' had not finished starting yet and will carry on by "
                "itself within seconds (say 'shortly', not 'done'), "
                "'unconfirmed' had the text typed in without a confirmed "
                "submit (it may be sitting in the input box — tell the user to "
                "look at that pane), and 'failed' names what refused and why. "
                "Reporting an unconfirmed or queued pane as running is the one "
                "wrong thing to do with this answer. Pressing twice is safe: "
                "each pane is claimed before anything is typed, so a repeat "
                "call cannot send a second 'continue' into the same agent."
            ),
            method="POST",
            path="/api/agentic-ide/interrupted/continue",
            params={
                "type": "object",
                "properties": {
                    "names": {
                        "type": "array",
                        "items": {"type": "string"},
                        "description": (
                            "Call-signs to continue. Omit or leave empty to "
                            "continue every interrupted pane."
                        ),
                    },
                    "prompt": {
                        "type": "string",
                        "description": (
                            "What to send instead of the default 'continue'. "
                            "The agent still holds its whole conversation, so "
                            "short beats elaborate."
                        ),
                    },
                },
            },
            ui_section="agentic-ide",
            voice_aliases={
                "de": ("mach mit den unterbrochenen sitzungen weiter",),  # i18n-allow: input vocab
                "en": ("continue the interrupted coding sessions",),
                "es": ("continúa las sesiones interrumpidas",),  # i18n-allow: input vocab
            },
        ),
    )


@lru_cache(maxsize=1)
def get_registry() -> tuple[AppCommand, ...]:
    """The command catalog — built lazily on first access, then cached."""
    return _build_registry()


def get_command(command_id: str) -> AppCommand | None:
    """Look up one command by id, or None."""
    for cmd in get_registry():
        if cmd.id == command_id:
            return cmd
    return None


def registry_as_dicts() -> list[dict[str, Any]]:
    """The catalog as plain dicts (route responses, docs generation)."""
    return [cmd.as_dict() for cmd in get_registry()]
