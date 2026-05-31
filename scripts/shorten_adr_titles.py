"""ADR-Titel kuerzen — Frontmatter-``title`` auf prägnante Form bringen.

User-Mandat 2026-04-29: Display-Titel in der DocsView-Sidebar muss
vollstaendig lesbar sein (max 60 Zeichen, empfohlen 50). Body-H1 bleibt
unveraendert — der lange Erklaerungs-Titel im Body ist eigenstaendig.

Idempotent: wenn ein ADR schon einen kurzen Titel hat, wird er
uebersprungen. Pflegt nur das ``title``-Field, alles andere bleibt.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ADR_DIR = REPO / "docs" / "adr"

# Hand-kuratierte Kurz-Titel pro ADR-Nummer.
# Format: ``ADR-NNNN: <prägnant>`` — Diataxis-Pill liefert das ADR-Label
# zusaetzlich, aber wir lassen die Nummer im Titel weil sie der
# Identifier ist.
SHORT_TITLES: dict[str, str] = {
    "0001": "ADR-0001: IPC via Named Pipe + HMAC",
    "0002": "ADR-0002: UIA-Tree-Pruning",
    "0003": "ADR-0003: Task-Queue in Memory-DB",
    "0004": "ADR-0004: Kill-Switch < 2s",
    "0005": "ADR-0005: Lightweight-Scheduler",
    "0006": "ADR-0006: Cost-Budget im Brain",
    "0007": "ADR-0007: Flight-Recorder JSONL",
    "0008": "ADR-0008: Computer-Use in-process",
    "0009": "ADR-0009: Self-Healing Worker-Critic",
    "0010": "ADR-0010: Output-Filter Pattern-based",
    "0011": "ADR-0011: Pure Dispatcher (4 Tools)",
}


def adr_number(filename: str) -> str:
    m = re.match(r"^(\d{4})-", filename)
    return m.group(1) if m else "0000"


def replace_title_in_frontmatter(text: str, new_title: str) -> str:
    """Ersetzt ``title: "..."`` in der Frontmatter-Section. Idempotent —
    wenn das title-Field schon den new_title traegt, return unveraendert."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    fm = parts[1]
    body = parts[2]
    # Match `title: "..."` Zeile
    title_re = re.compile(r'(?m)^title\s*:\s*"[^"]*"\s*$')
    quoted = f'title: "{new_title}"'
    if title_re.search(fm):
        new_fm = title_re.sub(quoted, fm)
    else:
        # Falls kein title vorhanden: anhaengen
        new_fm = fm.rstrip() + f"\n{quoted}\n"
    return f"---{new_fm}---{body}"


def main() -> None:
    if not ADR_DIR.is_dir():
        raise SystemExit(f"ADR-Dir nicht gefunden: {ADR_DIR}")

    updated = 0
    skipped = 0
    for path in sorted(ADR_DIR.glob("*.md")):
        num = adr_number(path.name)
        if num not in SHORT_TITLES:
            print(f"  skip   {path.name} (kein Kurz-Titel hinterlegt)")
            skipped += 1
            continue
        new_title = SHORT_TITLES[num]
        text = path.read_text(encoding="utf-8")
        # Pruefen, ob schon der Kurz-Titel drin ist
        if f'title: "{new_title}"' in text:
            print(f"  skip   {path.name} (schon kurz)")
            skipped += 1
            continue
        new_text = replace_title_in_frontmatter(text, new_title)
        path.write_text(new_text, encoding="utf-8")
        print(f"  short  {path.name}  ->  {new_title}  ({len(new_title)} Zeichen)")
        updated += 1

    print(f"\n{updated} ADRs gekuerzt, {skipped} uebersprungen.")


if __name__ == "__main__":
    main()
