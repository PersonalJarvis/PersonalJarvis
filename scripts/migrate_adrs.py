"""Einmaliges Migrations-Skript: Frontmatter zu allen ADR-Files ergaenzen.

Idempotent — wenn ein Frontmatter schon existiert, wird die Datei nicht
angefasst. Pro ADR werden ``title`` aus dem ersten H1 extrahiert,
``slug`` aus dem Dateinamen, ``diataxis: adr``, plus eine grobe Phasen-
Zuordnung aus einer Hand-Mapping-Tabelle.

Aufruf:
    python scripts/migrate_adrs.py
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ADR_DIR = REPO / "docs" / "adr"

# Hand-Mapping: ADR-Nr -> Master-Plan-Phase. Aus Inhaltslesung erkannt.
PHASE_MAP: dict[str, str] = {
    "0001": "5",  # IPC Named Pipe — Admin
    "0002": "5",  # UIA Tree — Vision
    "0003": "5",  # Task Queue — Async
    "0004": "5",  # Kill Propagation — Control
    "0005": "5",  # Scheduler — Async
    "0006": "5",  # Cost Budget — Control
    "0007": "5",  # Flight Recorder — Telemetry
    "0008": "5",  # Computer Use Harness — Action
    "0009": "6",  # Self-Healing — Phase 6
    "0010": "1",  # Output-Filter — Persona/Phase 1
    "0011": "5",  # Router Pure Dispatcher — Persona/Phase 5
}

# Heutiges Datum als ``last_reviewed``-Default. ADRs sind in Phasen
# entstanden, aber wir markieren sie heute als reviewed (sie waren in den
# Phasen-Reviews mit drin).
TODAY = "2026-04-29"


def extract_title(body: str) -> str:
    """Liest den ersten ``# H1``-Text und liefert ihn ohne ``#`` und Whitespace."""
    m = re.search(r"^#\s+(.+?)\s*$", body, re.MULTILINE)
    return m.group(1).strip() if m else "ADR"


def adr_number(filename: str) -> str:
    """``0009-self-healing.md`` -> ``"0009"``."""
    m = re.match(r"^(\d{4})-", filename)
    return m.group(1) if m else "0000"


def slug_from_filename(filename: str) -> str:
    """``0009-self-healing-worker-critic.md`` -> ``adr-0009-self-healing-worker-critic``."""
    stem = filename[:-3] if filename.endswith(".md") else filename
    return f"adr-{stem}"


def already_has_frontmatter(text: str) -> bool:
    return text.startswith("---\n") or text.startswith("---\r\n")


def build_frontmatter(adr_num: str, title: str, slug: str) -> str:
    phase = PHASE_MAP.get(adr_num, "-")
    quoted_title = title.replace('"', '\\"')
    return (
        "---\n"
        f'title: "{quoted_title}"\n'
        f"slug: {slug}\n"
        "diataxis: adr\n"
        "status: active\n"
        "owner: sam\n"
        f"last_reviewed: {TODAY}\n"
        f"phase: {phase}\n"
        "audience: developer\n"
        "---\n\n"
    )


def main() -> None:
    if not ADR_DIR.is_dir():
        raise SystemExit(f"ADR-Dir nicht gefunden: {ADR_DIR}")

    migrated = 0
    skipped = 0
    for path in sorted(ADR_DIR.glob("*.md")):
        text = path.read_text(encoding="utf-8")
        if already_has_frontmatter(text):
            print(f"  skip   {path.name} (schon Frontmatter)")
            skipped += 1
            continue
        adr_num = adr_number(path.name)
        title = extract_title(text)
        slug = slug_from_filename(path.name)
        fm = build_frontmatter(adr_num, title, slug)
        new_text = fm + text
        path.write_text(new_text, encoding="utf-8")
        print(f"  added  {path.name}  slug={slug}  phase={PHASE_MAP.get(adr_num, '-')}")
        migrated += 1

    print(f"\n{migrated} ADRs migriert, {skipped} uebersprungen.")


if __name__ == "__main__":
    main()
