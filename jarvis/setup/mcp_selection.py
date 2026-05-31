"""MCP-Server-Selection-Step fuer den Setup-Wizard.

Dieser Schritt ist **separat** vom eigentlichen Wizard-Modul (wizard.py), damit
er parallel zur Phase-1b-Arbeit am Wizard entwickelt werden kann. Die Integration
ins Wizard erfolgt spaeter als expliziter Merge-Schritt:

    from jarvis.setup.mcp_selection import run_mcp_selection_step

    def step_3_mcp(state: WizardState) -> None:
        state.enabled_mcp_servers = run_mcp_selection_step()

Der Schritt kann auch standalone aufgerufen werden:

    python -m jarvis.setup.mcp_selection

In dem Fall gibt er die Auswahl nur aus — persistiert wird nichts, das
uebernimmt der Wizard.
"""
from __future__ import annotations

from dataclasses import dataclass

# ----------------------------------------------------------------------
# Fallback-Spec falls jarvis.mcp.registry noch nicht existiert (B2 parallel)
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
        description="Lokaler Datei-Zugriff (read/write/list).",
        mandatory=True,
    ),
    _FallbackSpec(
        name="memory-mcp",
        display="Memory",
        description="Persistenter Key-Value-Store fuer Fakten, Notizen, Context.",
        mandatory=True,
    ),
    _FallbackSpec(
        name="gmail-mcp",
        display="Gmail",
        description="Email lesen, triagieren, senden (nach Bestaetigung).",
        required_auth=("google_oauth",),
    ),
    _FallbackSpec(
        name="google-calendar-mcp",
        display="Google Calendar",
        description="Termine lesen, anlegen, verschieben.",
        required_auth=("google_oauth",),
    ),
    _FallbackSpec(
        name="fetch-mcp",
        display="Fetch / Web",
        description="HTTP-Requests, Wetter, RSS, APIs.",
    ),
    _FallbackSpec(
        name="github-mcp",
        display="GitHub",
        description="Repos, Issues, PRs, Workflows.",
        required_auth=("github_token",),
    ),
    _FallbackSpec(
        name="windows-mcp",
        display="Windows OS",
        description="DND, Fenstermanagement, Shell-Commands.",
        mandatory=True,
    ),
)


def _load_bootstrap() -> tuple:
    """Laedt BOOTSTRAP_SERVERS aus jarvis.mcp.registry oder nutzt Fallback."""
    try:
        from jarvis.mcp.registry import BOOTSTRAP_SERVERS  # type: ignore

        return tuple(BOOTSTRAP_SERVERS)
    except Exception:  # noqa: BLE001
        return _FALLBACK_BOOTSTRAP


# ----------------------------------------------------------------------
# Wizard-IO Helpers (Stil aus wizard.py uebernommen)
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
    """Interaktive Auswahl der MCP-Server.

    Returns:
        Liste der `name`-Strings der aktivierten Server.
    """
    specs = _load_bootstrap()

    _println("=" * 60)
    _println(" MCP-Server auswaehlen")
    _println("=" * 60)
    _println("Mandatory-Server sind vorausgewaehlt (empfohlen).")
    _println("Optional-Server werden nur aktiviert wenn Du 'j' sagst.")
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
                    f"      -> benoetigt Auth: {', '.join(required_auth)} "
                    f"(kann spaeter im Wizard eingegeben werden)"
                )
        _println("")

    return selected


def main() -> int:
    try:
        sel = run_mcp_selection_step()
    except KeyboardInterrupt:
        _println("\nAbgebrochen.")
        return 130
    _println("")
    _println(f"Ausgewaehlt: {sel}")
    _println("")
    _println("Diese Auswahl muss in jarvis.toml unter [mcp].enabled eingetragen werden:")
    _println(f"  enabled = {sel}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
