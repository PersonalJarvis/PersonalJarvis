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


def _provider_switch_params(tier: str, *, brain_switchable_only: bool = False,
                            ) -> dict[str, Any]:
    enum = _provider_ids(tier, brain_switchable_only=brain_switchable_only)
    return {
        "type": "object",
        "properties": {
            "provider": _str_param(
                f"Target {tier} provider id.", enum=enum or None, min_length=1
            ),
            "persist": {
                "type": "boolean",
                "default": True,
                "description": "Persist the choice to jarvis.toml (survives restart).",
            },
        },
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
                "active, e.g. openai-realtime or gemini-live."
            ),
            method="POST",
            path="/api/realtime/switch",
            params=_provider_switch_params("realtime"),
            ui_section="apikeys",
            voice_aliases={
                "de": ("wechsle das realtime-modell zu gemini",),  # i18n-allow: input vocab
                "en": ("switch the realtime model to gemini",),
                "es": ("cambia el modelo en tiempo real a gemini",),  # i18n-allow: input vocab
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
            title="Switch Jarvis-Agent (worker) provider",
            description=(
                "Switch the Jarvis-Agent / worker provider used for missions "
                "(e.g. codex to openai). Restart required."
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
            path="/api/tools",
            worker_allowed=True,
            params={"type": "object", "properties": {}},
            ui_section="settings",
            voice_aliases={
                "de": ("welche tools mcps und clis sind verbunden",),  # i18n-allow: input vocab
                "en": ("list the connected tools mcps and clis",),
                "es": ("lista las herramientas mcps y clis conectadas",),  # i18n-allow: input vocab
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
