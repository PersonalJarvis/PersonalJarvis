"""AwarenessConfig — Pydantic models for the [awareness] TOML block.

Attached by the root loader (``jarvis.core.config.load_config``) via
``JarvisConfig.awareness``. All fields have defaults so that a
``jarvis.toml`` without an ``[awareness]`` block can be read cleanly as
``AwarenessConfig()`` (backward-compat AC §4).

Defaults are taken 1:1 from JARVIS_AWARENESS_PLAN §4 — when changing
them, also update the ``[awareness]`` block in ``jarvis.toml``.
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

# Default patterns from Plan §4. Lifted into module constants so that tests
# can compare them without a Pydantic round-trip (test_config.py).
_DEFAULT_BLOCKED_PROCESSES: list[str] = [
    "1password*", "keepass*", "bitwarden*", "lastpass*",
]

_DEFAULT_BLOCKED_TITLE_PATTERNS: list[str] = [
    "*Banking*", "*PayPal*", "*Stripe*Dashboard*",
    "*Sparkasse*", "*Postbank*", "*Online-Banking*",
    "*Passwort*", "*Password*Manager*",
    "*Inkognito*", "*Private Browsing*",
]

_DEFAULT_ALLOWED_PROCESSES: list[str] = [
    "code.exe", "cursor.exe", "windsurf.exe",
    "WindowsTerminal.exe", "pwsh.exe", "cmd.exe",
]


class AwarenessPrivacyConfig(BaseModel):
    """System-layer privacy filter patterns."""
    model_config = ConfigDict(extra="allow")

    blocked_processes: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_BLOCKED_PROCESSES),
    )
    blocked_title_patterns: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_BLOCKED_TITLE_PATTERNS),
    )
    allowed_processes: list[str] = Field(
        default_factory=lambda: list(_DEFAULT_ALLOWED_PROCESSES),
    )
    default_when_unknown: str = "block_for_browsers_allow_for_others"


class AwarenessWatchersConfig(BaseModel):
    """Watcher settings — consumed by ``AwarenessManager`` from phase A1 onward."""
    model_config = ConfigDict(extra="allow")

    enable_window: bool = True
    enable_idle: bool = True
    idle_threshold_minutes: int = 5


class AwarenessQuotasConfig(BaseModel):
    """Mirror of ``StorageQuota`` defaults; consumed by ``StoryTracker`` from A2 onward."""
    model_config = ConfigDict(extra="allow")

    max_bytes: int = 50 * 1024 * 1024     # 50 MiB
    max_episodes: int = 1000


class AwarenessVerdichterConfig(BaseModel):
    """Verdichter brain settings (Plan §6, ``[awareness.verdichter]``).

    Controls the episode-summary brain call. ``enabled = false`` turns off
    the Verdichter entirely (StoryTracker then persists episodes with
    ``summary=""``) — hot-disable analogous to the top-level switch.

    Defaults (Plan §6 D-A4):
        provider          = "claude-api"
        model             = "claude-haiku-4-5-20251001"
        max_input_tokens  = 800       # hard cap on input per episode
        max_output_tokens = 200       # hard cap on output per episode
        timeout_s         = 5.0       # asyncio.wait_for timeout
    """
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    provider: str = "claude-api"
    model: str = "claude-haiku-4-5-20251001"
    max_input_tokens: int = 800
    max_output_tokens: int = 200
    timeout_s: float = 5.0


class AwarenessProbesConfig(BaseModel):
    """Deep probes settings (Plan §9, ``[awareness.probes]``).

    A5-Lite: only GitProbe + FileSystemProbe (MCP/LSP deferred to Phase 6).
    All probes share a combined 200 ms total budget — if exceeded,
    probe_all() is aborted and unset fields remain None.

    enabled = false → no probes registered; FrameSnapshot keeps
    git_branch=None / open_file_hint=None.
    """
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    enable_git: bool = True
    enable_filesystem: bool = True
    total_budget_ms: int = 200    # hard cap for probe_all (asyncio.gather)
    fs_max_watched_roots: int = 10    # cap for FileSystemProbe.watch


class AwarenessStoryConfig(BaseModel):
    """StoryTracker settings (Plan §6, ``[awareness.story]``).

    Controls buffering and trigger logic for L2 episode flushes. ``enabled = false``
    keeps watchers running (L1) but persists no episodes — useful for privacy mode
    or debugging without LLM cost.

    Defaults (Plan §6 StoryTracker trigger logic):
        buffer_max               = 200    # max frames + events in builder
        episode_min_duration_s   = 60     # < 60 s = spam, skip flush
        episode_max_duration_min = 30     # > 30 min = hard cap, force flush
        hard_timer_min           = 5      # flush at least every 5 min even without activity
    """
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    buffer_max: int = 200
    episode_min_duration_s: int = 60
    episode_max_duration_min: int = 30
    hard_timer_min: int = 5


class AwarenessConfig(BaseModel):
    """Root of the ``[awareness]`` block.

    ``enabled = false`` hot-disables the entire subsystem — the factory
    will not build an ``AwarenessManager`` at all and tools will not be
    registered (Plan §15 rollback plan).
    """
    model_config = ConfigDict(extra="allow")

    enabled: bool = True
    privacy: AwarenessPrivacyConfig = Field(default_factory=AwarenessPrivacyConfig)
    watchers: AwarenessWatchersConfig = Field(default_factory=AwarenessWatchersConfig)
    quotas: AwarenessQuotasConfig = Field(default_factory=AwarenessQuotasConfig)
    verdichter: AwarenessVerdichterConfig = Field(default_factory=AwarenessVerdichterConfig)
    story: AwarenessStoryConfig = Field(default_factory=AwarenessStoryConfig)
    probes: AwarenessProbesConfig = Field(default_factory=AwarenessProbesConfig)

    @classmethod
    def default(cls) -> AwarenessConfig:
        """Convenience factory for tests and direct initialization in A1+.

        Equivalent to ``cls()``, but as a named constructor is clearer
        at the call site.
        """
        return cls()
