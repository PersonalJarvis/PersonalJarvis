"""Inventory German strings in user-facing positions across the frontend."""
import re
import os
from collections import defaultdict

GERMAN_INDICATORS = [
    " der ", " die ", " das ", " den ", " dem ", " des ",
    " ein ", " eine ", " einen ", " einem ", " einer ",
    " und ", " oder ", " aber ",
    " nicht ", " kein ", " keine ", " keinen ",
    " mit ", " von ", " vom ", " zur ", " zum ",
    " auf ", " bei ", " aus ", " nach ",
    " werden", " wurde", " wird", "kann ", "koennen", "muss", "muessen",
    " ist ", " sind ", " war ", " waren ", " hat ", " haben ",
    "wenn ", "wann ", "warum", "weil",
    "noch ", "schon ", "auch ", "sehr", "nur ", "immer", "bitte", "danke",
    "Lade ", "Laden", "Speich", "Bearbeit", "Klick", "oeffne", "schliess",
    "waehle", "aktiv", "deaktiv", "loeschen", "Speichern", "Aktualisieren",
    "Versuche", "Fehler", "Anrede", "Pronomen", "Sprachen", "Geraete",
    "Stunden", "Tage ", " Tag ", "Tagen", "importiere", "generieren",
    "geprueft", "laeuft", "gestartet", "beendet", "Bereit",
    "konfigur", "Konfigur", "Verbindung", "Verbund", "verbunden",
    "Verlauf", "Anzeige", "Eintrag", "Eintr",
    "Gespr", "uebern", "uebersetz", "anzeig",
    "Gestern", "Heute", "Wartet"
]

UMLAUT_CHARS = "aouszAOUS"  # placeholder
UMLAUT_REAL = "äöüßÄÖÜ"

def has_umlaut(s):
    for c in UMLAUT_REAL:
        if c in s:
            return True
    return False

def is_german(s):
    if has_umlaut(s):
        return True
    spaced = " " + s + " "
    for w in GERMAN_INDICATORS:
        if w in spaced:
            return True
    return False

def is_user_facing(line, m_start):
    around = line[max(0, m_start-50):m_start+3]
    triggers = [
        "title=", "placeholder=", "aria-label=", "aria-description=",
        "label=", "subtitle=", "description=", "tooltip=",
        "pushToast(", "toast(", "detail:", "message:",
        "reason:", "note:", "helpText=",
        "header=", "caption=", "text:", "summary:",
    ]
    for trig in triggers:
        if trig in around:
            return True
    before = line[:m_start]
    if ">" in before:
        last_gt = before.rindex(">")
        rest = before[last_gt:]
        if "<" not in rest:
            return True
    return False

STRING_RE = re.compile(r'"((?:[^"\\]|\\.)*)"')
TEMPLATE_RE = re.compile(r'`([^`]+)`')

def main():
    base = "<USER_HOME>/Desktop/Personal Jarvis/jarvis/ui/web/frontend/src"
    results = defaultdict(list)
    total = 0

    for root, dirs, files in os.walk(base):
        norm = root.replace("\\", "/")
        if "node_modules" in norm or "__tests__" in norm or "/i18n/" in norm:
            continue
        for fn in files:
            if not fn.endswith(".tsx"):
                continue
            fp = os.path.join(root, fn).replace("\\", "/")
            try:
                with open(fp, encoding="utf-8") as f:
                    lines = f.read().split("\n")
            except Exception:
                continue
            for i, line in enumerate(lines, 1):
                stripped = line.strip()
                if stripped.startswith("//") or stripped.startswith("*") or stripped.startswith("/*"):
                    continue
                if stripped.startswith("import ") or stripped.startswith("export *"):
                    continue
                for m in STRING_RE.finditer(line):
                    s = m.group(1)
                    if len(s) < 4:
                        continue
                    if not is_german(s):
                        continue
                    if not is_user_facing(line, m.start()):
                        continue
                    results[fp].append((i, s[:100]))
                    total += 1
                for m in TEMPLATE_RE.finditer(line):
                    s = m.group(1)
                    if len(s) < 4:
                        continue
                    if not is_german(s):
                        continue
                    results[fp].append((i, "`" + s[:100] + "`"))
                    total += 1

    print(f"=== TOTAL: {total} German strings ===\n")
    for fp in sorted(results.keys()):
        items = results[fp]
        rel = fp.replace(base + "/", "")
        print(f"\n### {rel} ({len(items)})")
        for ln, s in items:
            print(f"  L{ln}: {s}")

if __name__ == "__main__":
    main()
