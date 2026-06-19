"""SelfModRegistry — hardcoded allowlist of mutable config paths.

Plan-§AD-1 / §AP-11: The allowlist is a ClassVar constant; there is
NO dynamic `register()`. Extension requires a code edit plus
code review. This prevents constraint-self-bypass by the LLM
(an allowlist edit as a tool call would be the failure mode).

Plan-§7.1 Public API: `is_mutable`, `get_spec`, `list_all`.
"""
from __future__ import annotations

from fnmatch import fnmatch
from typing import ClassVar

from .errors import AllowlistViolationError, SecretAccessError
from .schema import MutableSpec

# Defense-in-depth patterns (Plan-§AP-9): even if a path were to land
# accidentally in `ALLOWED`, the forbidden check additionally blocks
# read and write attempts.
FORBIDDEN_PATTERNS: tuple[str, ...] = (
    "security.*",
    "safety.*",  # risk-tier whitelist/blacklist — never readable/writable via self-mod or the Control API
    "mcp_server.*",
    "harness.*",
    "*_api_key",
    "*_token",
    "*_secret",
    "*_password",
    "*_password_hash",
    "*_credential",
)


class SelfModRegistry:
    """Read-only allowlist of mutable settings (Plan-§7.1)."""

    ALLOWED: ClassVar[tuple[MutableSpec, ...]] = (
        MutableSpec(
            path="tts.provider",
            pydantic_model_name="TTSConfig",
            field_name="provider",
            risk_tier="ask",
            needs_restart=False,
            description="TTS-Provider (Hot-Reload abgedeckt)",
        ),
        MutableSpec(
            path="tts.voice_de",
            pydantic_model_name="TTSConfig",
            field_name="voice_de",
            risk_tier="ask",
            needs_restart=False,
            description="Deutsche TTS-Stimme (Hot-Reload abgedeckt)",
        ),
        MutableSpec(
            path="tts.voice_en",
            pydantic_model_name="TTSConfig",
            field_name="voice_en",
            risk_tier="ask",
            needs_restart=False,
            description="Englische TTS-Stimme (Hot-Reload abgedeckt)",
        ),
        MutableSpec(
            path="tts.speed",
            pydantic_model_name="TTSConfig",
            field_name="speed",
            risk_tier="safe",
            needs_restart=False,
            description="TTS-Sprechgeschwindigkeit (trivial, Bypass-Whitelist)",
        ),
        MutableSpec(
            path="stt.provider",
            pydantic_model_name="STTConfig",
            field_name="provider",
            risk_tier="ask",
            needs_restart=True,
            description="STT-Provider (STT-Init nicht hot-reloadbar)",
        ),
        MutableSpec(
            path="brain.primary",
            pydantic_model_name="BrainConfig",
            field_name="primary",
            risk_tier="ask",
            needs_restart=True,
            description="Primary brain provider (requires a BrainManager re-init).",
        ),
        MutableSpec(
            path="ui.theme",
            pydantic_model_name="UIConfig",
            field_name="theme",
            risk_tier="safe",
            needs_restart=False,
            description="UI-Theme (trivial, Bypass-Whitelist)",
        ),
        # Interface (display) language — every label/button/message in the app.
        # SAFE: trivially reversible. needs_restart=False: the frontend switches
        # live when the write fires ConfigReloaded over /ws (no restart). This is
        # the VISIBLE language the user sees; brain.reply_language is what Jarvis
        # SPEAKS. "switch the language" should usually target this one.
        MutableSpec(
            path="ui.language",
            pydantic_model_name="UIConfig",
            field_name="language",
            risk_tier="safe",
            needs_restart=False,
            description=(
                "Interface / display language of the whole app (en/de/es) — what "
                "the user SEES. Applies live, no restart."
            ),
        ),
        MutableSpec(
            path="profile.language",
            pydantic_model_name="ProfileConfig",
            field_name="language",
            risk_tier="ask",
            needs_restart=False,
            description="Profile language (legacy; canonical is brain.reply_language).",
        ),
        # Canonical reply-language pin (Jarvis Control API, 2026-06-08). This is
        # the ONLY setting that changes spoken/chat output language —
        # BrainManager._reply_language_directive reads brain.reply_language, NOT
        # profile.language (which is a legacy no-op kept for old configs). SAFE:
        # trivially reversible, the user explicitly wants an instant switch, so
        # it auto-applies through the writer with no confirmation round-trip. A
        # ConfigReloaded subscriber calls BrainManager.set_reply_language so the
        # NEXT turn is in the new language without a restart.
        MutableSpec(
            path="brain.reply_language",
            pydantic_model_name="BrainConfig",
            field_name="reply_language",
            risk_tier="safe",
            needs_restart=False,
            description=(
                "Reply language for spoken/chat output (auto/de/en/es). "
                "Canonical language setting; applies to the next turn (no "
                "restart). Voice: \"switch your language to English\"."
            ),
        ),
        # STT input-locale hint. Read at STT init, so a change needs a restart.
        MutableSpec(
            path="stt.language",
            pydantic_model_name="STTConfig",
            field_name="language",
            risk_tier="ask",
            needs_restart=True,
            description=(
                "Speech-to-text input locale hint (auto/de/en/...). Read at STT "
                "init — needs restart to take effect."
            ),
        ),
        # TTS output locale. Read at TTS init; most cloud voices also auto-detect
        # the language from the reply text, so this mainly pins fixed-locale
        # engines. Needs restart to re-init the synthesizer.
        MutableSpec(
            path="tts.language_code",
            pydantic_model_name="TTSConfig",
            field_name="language_code",
            risk_tier="ask",
            needs_restart=True,
            description=(
                "Text-to-speech output locale (de-DE/en-US/...). Read at TTS "
                "init — needs restart to take effect."
            ),
        ),
        # Voice-tunable computer-use step budget. Points at ``step_budget`` —
        # the field the screenshot loop actually reads (via
        # ComputerUseContext.step_budget, factory.py). The legacy ``max_steps``
        # field is NOT read at runtime, so the voice command used to be a no-op
        # (fixed 2026-05-30). The 1..1000 range is enforced by the Pydantic
        # Field constraint on ComputerUseConfig.step_budget; the allowlist entry
        # here is what lets the Self-Mod tools propose a write. Hot-reload-safe:
        # the ConfigReloaded subscription in computer_use_context.py refreshes
        # the live context singleton, so the next mission picks up the new
        # ceiling without a restart.
        MutableSpec(
            path="computer_use.step_budget",
            pydantic_model_name="ComputerUseConfig",
            field_name="step_budget",
            risk_tier="ask",
            needs_restart=False,
            description=(
                "Computer-Use per-mission step ceiling (range 1-1000). Voice: "
                "\"setze Schrittlimit auf 200\". Hot-reload — applies to "
                "the next mission."
            ),
        ),
    )

    @classmethod
    def is_forbidden(cls, path: str) -> bool:
        """True if the path belongs to a protected section."""
        return any(fnmatch(path, pattern) for pattern in FORBIDDEN_PATTERNS)

    @classmethod
    def is_mutable(cls, path: str) -> bool:
        """Hard allowlist lookup. Deny-by-default."""
        if cls.is_forbidden(path):
            return False
        return any(spec.path == path for spec in cls.ALLOWED)

    @classmethod
    def get_spec(cls, path: str) -> MutableSpec | None:
        """Returns the spec for the given path — `None` if not in the allowlist
        or blocked by FORBIDDEN_PATTERNS.
        """
        if cls.is_forbidden(path):
            return None
        for spec in cls.ALLOWED:
            if spec.path == path:
                return spec
        return None

    @classmethod
    def require_spec(cls, path: str) -> MutableSpec:
        """Like `get_spec`, but raises instead of returning `None`.

        - `SecretAccessError` for FORBIDDEN_PATTERNS (defense-in-depth).
        - `AllowlistViolationError` for unknown paths.
        """
        if cls.is_forbidden(path):
            raise SecretAccessError(
                f"Pfad '{path}' gehört zu einer geschützten Sektion und darf "
                "weder gelesen noch geändert werden."
            )
        spec = cls.get_spec(path)
        if spec is None:
            raise AllowlistViolationError(
                f"Pfad '{path}' ist nicht in SelfModRegistry.ALLOWED. "
                "Mutationen müssen vorab im Code registriert werden."
            )
        return spec

    @classmethod
    def list_all(cls) -> list[MutableSpec]:
        """Returns the complete allowlist as a new list."""
        return list(cls.ALLOWED)
