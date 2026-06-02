"""Static seed map for the built-in Jarvis capability surface.

Covers:
  - All 11 ROUTER_TOOLS from ``jarvis/brain/factory.py``
  - 5 local-action-gate patterns (open_app, type_text, hotkey,
    reset_orb_position, terminal_count)
  - 5 harness adapters (openclaw, mcp-remote, computer-use,
    python-script, open-interpreter)

Action verbs mirror ``BrainRoutingConfig.spawn_verbs`` so that
``CapabilityRegistry.has_action_intent`` and the manager's
``_should_force_spawn`` heuristic operate on the same vocabulary.

Call ``seed_registry(get_registry())`` once at boot before any voice
handling starts.
"""
from __future__ import annotations

from jarvis.core.capabilities import Capability, CapabilityRegistry

# ---------------------------------------------------------------------------
# Common verb sets reused across multiple capabilities
# ---------------------------------------------------------------------------

# Core action verbs shared by tools that "do things" — mirrors the
# BrainRoutingConfig.spawn_verbs list so has_action_intent stays in sync.
_ACTION_VERBS: tuple[str, ...] = (
    # DE — repair / implement
    "umsetz", "reparier", "behebe", "korrigier",
    "implementier", "entwickel", "refactor", "debug",
    # DE — file / system actions
    "lies", "lese", "liest", "schreib", "schreibe", "schreibt",
    "bau", "baue", "baut", "oeffne", "öffne", "oeffnet", "öffnet",
    "installier", "deinstallier", "deploy",
    "zeig", "zeige", "zeigt",
    "mach", "mache", "macht", "machen",
    "starte", "start", "starten", "startet",
    "delegier", "delegiere",
    "spawne", "spawn", "spawnen",
    # EN
    "fix", "repair",
    "read", "write", "build", "open", "install", "show", "make",
    "run", "execute", "launch", "start",
)

_READ_VERBS: tuple[str, ...] = (
    "lies", "lese", "liest", "zeig", "zeige", "zeigt", "show",
    "read", "recall", "suche", "such", "finde", "find", "lookup",
    "erinnere", "zeig", "hol", "hole", "retrieve", "get",
)

_SHELL_OBJECTS: tuple[str, ...] = (
    "shell", "terminal", "command", "cmd", "bash", "powershell",
    "befehl", "skript", "script",
)

_SCREEN_OBJECTS: tuple[str, ...] = (
    "screen", "screenshot", "bildschirm", "aufnahme", "snapshot",
    "capture", "foto", "bild",
)

_WIKI_OBJECTS: tuple[str, ...] = (
    "wiki", "notiz", "note", "wissen", "knowledge", "seite", "page",
    "eintrag", "entry", "fact", "fakt",
)

_AWARENESS_OBJECTS: tuple[str, ...] = (
    "awareness", "status", "zustand", "zustaende", "state", "context",
    "kontext", "erinnerung", "memory", "verlauf", "history", "episode",
)


# ---------------------------------------------------------------------------
# Seed table
# ---------------------------------------------------------------------------

_SEED_CAPABILITIES: list[Capability] = [
    # ------------------------------------------------------------------ #
    # ROUTER_TOOLS (11)
    # ------------------------------------------------------------------ #
    Capability(
        id="tool.run-shell",
        source="router_tool",
        verbs=_ACTION_VERBS + ("fuehre", "fuehr", "fuehren"),
        objects=_SHELL_OBJECTS,
        description="Run arbitrary shell commands (PowerShell / bash) on the host.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    Capability(
        id="tool.screen-snapshot",
        source="router_tool",
        verbs=_READ_VERBS + ("nimm", "mach", "mache", "capture", "take"),
        objects=_SCREEN_OBJECTS,
        description="Capture a screenshot of the current screen.",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    Capability(
        id="tool.dispatch-to-harness",
        source="router_tool",
        verbs=_ACTION_VERBS,
        objects=(
            "harness", "computer", "desktop", "app", "anwendung", "fenster",
            "window", "click", "klick", "klicke",
        ),
        description="Dispatch a computer-use / desktop-control task to the active harness.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    Capability(
        id="tool.multi-spawn",
        source="router_tool",
        verbs=("spawn", "spawne", "spawnen", "starte", "start", "launch", "delegier"),
        objects=("agent", "agenten", "agents", "worker", "workers", "aufgaben", "tasks"),
        description="Spawn multiple parallel worker agents for a complex task.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    Capability(
        id="tool.spawn-worker",
        source="router_tool",
        verbs=(
            "spawn", "spawne", "spawnen", "delegier", "delegiere",
            "delegate", "openclaw", "subagent",
        ),
        objects=(
            "openclaw", "open-claw", "open claw", "subagent", "sub-agent",
            "worker", "code", "datei", "file", "repo", "repository",
        ),
        description="Spawn an OpenClaw sub-agent for heavy code or file tasks.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    Capability(
        id="tool.dispatch-with-review",
        source="router_tool",
        verbs=_ACTION_VERBS + ("review", "check", "pruefe", "prüfe"),
        objects=(
            "review", "quality", "qualitaet", "qualität", "critic",
            "kritik", "check", "pruefung", "prüfung",
        ),
        description="Dispatch a task through the Quality-Gate (Worker + Critic) pipeline.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    Capability(
        id="tool.awareness-snapshot",
        source="router_tool",
        verbs=_READ_VERBS,
        objects=_AWARENESS_OBJECTS,
        description="Read a snapshot of the current awareness/context state (read-only).",
        risk_tier="safe",
        requires_evidence=False,
    ),
    Capability(
        id="tool.awareness-recall",
        source="router_tool",
        verbs=_READ_VERBS,
        objects=_AWARENESS_OBJECTS,
        description="Full-text search over the recent awareness episode log (read-only).",
        risk_tier="safe",
        requires_evidence=False,
    ),
    Capability(
        id="tool.run-skill",
        source="router_tool",
        verbs=_ACTION_VERBS + ("skill", "faehigkeit", "fähigkeit"),
        objects=("skill", "skills", "faehigkeit", "fähigkeit", "macro", "makro"),
        description="Execute an installed user skill / macro.",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    Capability(
        id="tool.wiki-recall",
        source="router_tool",
        verbs=_READ_VERBS,
        objects=_WIKI_OBJECTS,
        description="Keyword search over the long-term Obsidian wiki vault (read-only).",
        risk_tier="safe",
        requires_evidence=False,
    ),
    Capability(
        id="tool.wiki-page-read",
        source="router_tool",
        verbs=_READ_VERBS + ("oeffne", "öffne", "open"),
        objects=_WIKI_OBJECTS,
        description="Read a full wiki page by vault path (read-only).",
        risk_tier="safe",
        requires_evidence=False,
    ),
    Capability(
        id="tool.wiki-ingest",
        source="router_tool",
        verbs=(
            "speicher", "save", "merk", "merke", "notier", "notiere",
            "ingest", "store", "schreib", "write",
        ),
        objects=_WIKI_OBJECTS,
        description="Store a fact / note deterministically into the wiki vault.",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    # ------------------------------------------------------------------ #
    # LOCAL-ACTION-GATE patterns (5)
    # ------------------------------------------------------------------ #
    Capability(
        id="local.open_app",
        source="local_action",
        verbs=(
            "oeffne", "öffne", "oeffnet", "öffnet", "starte", "start",
            "open", "launch", "mach", "mache", "macht",
        ),
        objects=(
            "chrome", "browser", "firefox", "edge", "notepad", "terminal",
            "spotify", "word", "excel", "app", "anwendung", "programm",
            "application",
        ),
        description="Open or launch a named desktop application.",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    Capability(
        id="local.type_text",
        source="local_action",
        verbs=(
            "schreib", "schreibe", "schreibt", "tippe", "tipp", "type",
            "eingabe", "write",
        ),
        objects=(
            "text", "nachricht", "message", "eingabe", "input",
            "feld", "field", "zeile", "line",
        ),
        description="Type text into the currently focused input field.",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    Capability(
        id="local.hotkey",
        source="local_action",
        verbs=(
            "drueck", "drücke", "druecke", "press", "shortcut",
            "tastenkombination", "hotkey", "strg", "ctrl",
        ),
        objects=(
            "hotkey", "shortcut", "tastenkombination", "taste", "key",
            "strg", "ctrl", "alt", "shift",
        ),
        description="Execute a keyboard shortcut / hotkey combination.",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    Capability(
        id="local.reset_orb_position",
        source="local_action",
        verbs=(
            "reset", "zurueck", "zurück", "bring", "bringe",
            "orb", "overlay", "move", "verschiebbe",
        ),
        objects=(
            "orb", "overlay", "position", "fenster", "window",
            "zurueck", "zurück",
        ),
        description="Reset the Orb overlay to its default screen position.",
        risk_tier="safe",
        requires_evidence=True,
    ),
    Capability(
        id="local.terminal_count",
        source="local_action",
        verbs=(
            "oeffne", "öffne", "starte", "start", "spawne", "spawn",
            "open", "launch", "neue", "new",
        ),
        objects=(
            "terminal", "terminals", "konsole", "konsolen", "console",
            "fenster", "window", "wt",
        ),
        description="Open one or more new terminal windows.",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    # ------------------------------------------------------------------ #
    # HARNESS ADAPTERS (5)
    # ------------------------------------------------------------------ #
    Capability(
        id="harness.openclaw",
        source="harness",
        verbs=_ACTION_VERBS + (
            "openclaw", "open-claw",
            "delegier", "delegiere", "delegate",
        ),
        objects=(
            "code", "datei", "file", "repo", "repository", "projekt",
            "project", "script", "skript",
        ),
        description="OpenClaw subprocess harness for heavy code/file tasks.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    Capability(
        id="harness.mcp-remote",
        source="harness",
        verbs=_ACTION_VERBS,
        objects=(
            "mcp", "server", "remote", "service", "dienst", "integration",
        ),
        description="Generic MCP-remote harness adapter for registered MCP servers.",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    Capability(
        id="harness.computer-use",
        source="harness",
        verbs=_ACTION_VERBS + (
            "klick", "klicke", "click", "steuere", "steuern",
            "bedien", "bediene", "control",
        ),
        objects=(
            "computer", "desktop", "bildschirm", "screen", "fenster",
            "window", "app", "maus", "mouse",
        ),
        description="POAV computer-use harness for GUI automation on the desktop.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    Capability(
        id="harness.python-script",
        source="harness",
        verbs=_ACTION_VERBS + (
            "fuehre", "fuehr", "fuehren",
            "run", "execute", "ausfuehren",
        ),
        objects=(
            "python", "script", "skript", "py", "datei", "file",
        ),
        description="Run a Python script in a sandboxed subprocess.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    Capability(
        id="harness.open-interpreter",
        source="harness",
        verbs=_ACTION_VERBS,
        objects=(
            "interpreter", "open-interpreter", "openinterpreter",
            "code", "programm",
        ),
        description="Open Interpreter harness for multi-language code execution.",
        risk_tier="ask",
        requires_evidence=True,
    ),
    # ------------------------------------------------------------------ #
    # CHUNK B — jarvis-contacts (3)
    # ------------------------------------------------------------------ #
    # These exist so the capability gate routes a named-person action to the
    # contact surface instead of refusing it ("Das kann ich noch nicht") or
    # spawning a contextless worker. CRITICAL constraint (test_capability_
    # coupling_e2e hard-negatives): NONE of these verbs may appear in the
    # canonical dispatch hard-negatives ("schick Email", "trag Termin ein",
    # "sende WhatsApp", "bestelle Pizza", "poste auf X") — so the contact verbs
    # deliberately EXCLUDE the dispatch verbs schick/sende/trag/bestelle/poste.
    # call-contact is the SOLE owner of the call verbs (ruf/anruf/call/
    # telefonier) so "ruf Christoph an" resolves there unambiguously, which is
    # exactly what flips _is_generic_subagent_work from spawn to no-spawn
    # (BUG-class project_bug_subagent_not_natively_recognized).
    Capability(
        id="tool.contact-lookup",
        source="router_tool",
        verbs=(
            "schreib", "schreibe", "mail", "maile",
            "such", "suche", "find", "finde", "zeig", "zeige",
            "nenn", "nenne", "kontaktier", "kontaktiere", "wer", "lookup",
        ),
        objects=(
            "kontakt", "kontakte", "contact", "contacts", "person", "leute",
            "mail", "email", "e-mail", "nummer", "number", "telefonnummer",
            "telefon", "phone", "adresse", "address",
        ),
        description="Resolve a saved contact by name/alias to their e-mail, phone and address (read-only).",
        risk_tier="safe",
        requires_evidence=False,
    ),
    Capability(
        id="tool.contact-upsert",
        source="router_tool",
        verbs=(
            "merk", "merke", "speicher", "speichere", "save", "store",
            "update", "aktualisier", "aktualisiere", "notier", "notiere",
            "schreib",
        ),
        objects=(
            "kontakt", "kontakte", "contact", "person", "nummer", "number",
            "telefonnummer", "telefon", "adresse", "address",
            "mail", "email", "e-mail",
        ),
        description="Create or update a saved contact (name, phone, e-mail, address, note).",
        risk_tier="monitor",
        requires_evidence=True,
    ),
    Capability(
        id="tool.call-contact",
        source="router_tool",
        verbs=(
            "ruf", "rufe", "ruft", "anruf", "anrufe", "anrufen",
            "call", "telefonier", "telefoniere", "dial",
        ),
        objects=(
            "kontakt", "kontakte", "contact", "person", "anruf", "telefon",
            "phone", "nummer", "number",
        ),
        description="Place a real outbound phone call to a saved contact.",
        risk_tier="ask",
        requires_evidence=True,
    ),
]


# ---------------------------------------------------------------------------
# Seeding entry point
# ---------------------------------------------------------------------------


def seed_registry(registry: CapabilityRegistry) -> None:
    """Register all built-in capabilities into *registry*.

    Idempotent: safe to call multiple times (re-registration silently
    replaces the previous entry).
    """
    for cap in _SEED_CAPABILITIES:
        registry.register(cap)
