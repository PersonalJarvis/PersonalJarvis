"""MCP-server-selection step for the setup wizard.

This step is **separate** from the actual wizard module (wizard.py), so it
can be developed in parallel with the Phase-1b work on the wizard. Integration
into the wizard happens later as an explicit merge step:

    from jarvis.setup.mcp_selection import run_mcp_selection_step

    def step_3_mcp(state: WizardState) -> None:
        state.enabled_mcp_servers = run_mcp_selection_step()

The step can also be invoked standalone:

    python -m jarvis.setup.mcp_selection

In that case it only prints the selection — nothing is persisted, that's
handled by the wizard.
"""
from __future__ import annotations

from dataclasses import dataclass

# ----------------------------------------------------------------------
# Fallback spec in case jarvis.mcp.registry doesn't exist yet (B2 parallel)
# ----------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class _FallbackSpec:
    name: str
    display: str
    description: str
    mandatory: bool = False
    required_auth: tuple[str, ...] = ()


_FALLBACK_BOOTSTRAP: tuple[_FallbackSpec, ...] = (
    _FallbackSpec(
        name="filesystem-mcp",
        display="Filesystem",
        description="Local file access (read/write/list).",
        mandatory=True,
    ),
    _FallbackSpec(
        name="memory-mcp",
        display="Memory",
        description="Persistent key-value store for facts, notes, context.",
        mandatory=True,
    ),
    _FallbackSpec(
        name="gmail-mcp",
        display="Gmail",
        description="Read, triage, send email (after confirmation).",
        required_auth=("google_oauth",),
    ),
    _FallbackSpec(
        name="google-calendar-mcp",
        display="Google Calendar",
        description="Read, create, move appointments.",
        required_auth=("google_oauth",),
    ),
    _FallbackSpec(
        name="fetch-mcp",
        display="Fetch / Web",
        description="HTTP requests, weather, RSS, APIs.",
    ),
    _FallbackSpec(
        name="github-mcp",
        display="GitHub",
        description="Repos, issues, PRs, workflows.",
        required_auth=("github_token",),
    ),
    _FallbackSpec(
        name="windows-mcp",
        display="Windows OS",
        description="DND, window management, shell commands.",
        mandatory=True,
    ),
)


def _load_bootstrap() -> tuple:
    """Loads BOOTSTRAP_SERVERS from jarvis.mcp.registry or uses the fallback."""
    try:
        from jarvis.mcp.registry import BOOTSTRAP_SERVERS  # type: ignore

        return tuple(BOOTSTRAP_SERVERS)
    except Exception:  # noqa: BLE001
        return _FALLBACK_BOOTSTRAP


# ----------------------------------------------------------------------
# Wizard IO helpers (style taken from wizard.py)
# ----------------------------------------------------------------------

def _println(msg: str = "") -> None:
    print(msg)


def _ask_yesno(prompt: str, default: bool = True) -> bool:
    d = "Y/n" if default else "y/N"
    try:
        ans = input(f"{prompt} [{d}]: ").strip().lower()
    except EOFError:
        return default
    if not ans:
        return default
    return ans in ("y", "yes", "j", "ja")


# ----------------------------------------------------------------------
# Public API
# ----------------------------------------------------------------------

def run_mcp_selection_step() -> list[str]:
    """Interactive selection of the MCP servers.

    Returns:
        List of the `name` strings of the enabled servers.
    """
    specs = _load_bootstrap()

    _println("=" * 60)
    _println(" Select MCP servers")
    _println("=" * 60)
    _println("Mandatory servers are pre-selected (recommended).")
    _println("Optional servers are only enabled if you say 'y'.")
    _println("")

    selected: list[str] = []
    for spec in specs:
        marker = "[X]" if getattr(spec, "mandatory", False) else "[ ]"
        _println(f"  {marker} {spec.display} — {spec.description}")
        default = bool(getattr(spec, "mandatory", False))
        label = "Mandatory" if default else "Optional"
        use = _ask_yesno(f"      Enable {spec.display}? ({label})", default=default)
        if use:
            selected.append(spec.name)
            required_auth = getattr(spec, "required_auth", ()) or ()
            if required_auth:
                _println(
                    f"      -> requires auth: {', '.join(required_auth)} "
                    f"(can be entered later in the wizard)"
                )
        _println("")

    return selected


def main() -> int:
    try:
        sel = run_mcp_selection_step()
    except KeyboardInterrupt:
        _println("\nAborted.")
        return 130
    _println("")
    _println(f"Selected: {sel}")
    _println("")
    _println("This selection must be entered in jarvis.toml under [mcp].enabled:")
    _println(f"  enabled = {sel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
