"""Section families that share one desktop window and its native identity."""

WINDOW_GROUPS: dict[str, tuple[str, ...]] = {
    "agentic-ide": ("agentic-ide", "agentic-ide-classic", "chat-workspace"),
    "settings": (
        "settings", "taskbar", "languages", "profile", "agent-instructions",
        "socials", "apikeys", "telephony", "telephony-setup", "computers",
        "appshots", "shortcuts", "pets", "costs", "feedback",
    ),
    "plugins": ("plugins", "mcps", "skills"),
    "clis": ("clis", "cli-test-hub"),
}

SECTION_WINDOW_TITLES = {
    "agentic-ide": "Agentic IDE",
    "agents": "Agents",
    "settings": "Settings",
    "plugins": "Plugins",
    "clis": "CLIs",
    "docs": "Docs",
    "memory": "Wiki",
    "board": "Board",
    "sessions": "Sessions",
    "marketplace": "Marketplace",
}


def section_window(view: str) -> str:
    """Resolve a tab to its owning window without changing its navigation id."""
    return next((owner for owner, tabs in WINDOW_GROUPS.items() if view in tabs), view)
