"""Shorten ADR titles — bring the frontmatter ``title`` into a concise form.

User mandate 2026-04-29: the display title in the DocsView sidebar must
be fully readable (max 60 characters, 50 recommended). The body H1 stays
unchanged — the long explanatory title in the body is independent.

Idempotent: if an ADR already has a short title, it is
skipped. Only maintains the ``title`` field; everything else is left alone.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
ADR_DIR = REPO / "docs" / "adr"

# Hand-curated short titles per ADR number.
# Format: ``ADR-NNNN: <concise>`` — the Diataxis pill supplies the ADR label
# additionally, but we keep the number in the title because it is the
# identifier.
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
    """Replaces ``title: "..."`` in the frontmatter section. Idempotent —
    if the title field already carries the new_title, returns unchanged."""
    if not text.startswith("---"):
        return text
    parts = text.split("---", 2)
    if len(parts) < 3:
        return text
    fm = parts[1]
    body = parts[2]
    # Match the `title: "..."` line
    title_re = re.compile(r'(?m)^title\s*:\s*"[^"]*"\s*$')
    quoted = f'title: "{new_title}"'
    if title_re.search(fm):
        new_fm = title_re.sub(quoted, fm)
    else:
        # If no title is present: append it
        new_fm = fm.rstrip() + f"\n{quoted}\n"
    return f"---{new_fm}---{body}"


def main() -> None:
    if not ADR_DIR.is_dir():
        raise SystemExit(f"ADR dir not found: {ADR_DIR}")

    updated = 0
    skipped = 0
    for path in sorted(ADR_DIR.glob("*.md")):
        num = adr_number(path.name)
        if num not in SHORT_TITLES:
            print(f"  skip   {path.name} (no short title on file)")
            skipped += 1
            continue
        new_title = SHORT_TITLES[num]
        text = path.read_text(encoding="utf-8")
        # Check whether the short title is already present
        if f'title: "{new_title}"' in text:
            print(f"  skip   {path.name} (already short)")
            skipped += 1
            continue
        new_text = replace_title_in_frontmatter(text, new_title)
        path.write_text(new_text, encoding="utf-8")
        print(f"  short  {path.name}  ->  {new_title}  ({len(new_title)} Zeichen)")
        updated += 1

    print(f"\n{updated} ADRs gekuerzt, {skipped} uebersprungen.")


if __name__ == "__main__":
    main()
