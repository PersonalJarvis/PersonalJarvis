"""Final pass: replace remaining German strings via direct UTF-8 file ops."""
import os, re

BASE = "C:/Users/Administrator/Desktop/Personal Jarvis/jarvis/ui/web/frontend/src"

def patch(rel, replacements):
    fp = os.path.join(BASE, rel).replace("\\", "/")
    if not os.path.exists(fp):
        return
    with open(fp, encoding="utf-8") as f:
        s = f.read()
    found = 0
    nf = []
    for old, new in replacements:
        if old in s:
            s = s.replace(old, new)
            found += 1
        else:
            nf.append(old[:60])
    with open(fp, "w", encoding="utf-8") as f:
        f.write(s)
    print(f"{rel}: {found}/{len(replacements)}")
    for x in nf:
        print(f"  NF: {x!r}")

# ApiKeyForm
patch("components/ApiKeyForm.tsx", [
    ('`Löschen fehlgeschlagen: ${(e as Error).message}`',
     '`${t("common.delete_failed")}: ${(e as Error).message}`'),
])

# Need useT in ApiKeyForm
fp = os.path.join(BASE, "components/ApiKeyForm.tsx").replace("\\", "/")
if os.path.exists(fp):
    with open(fp, encoding="utf-8") as f:
        s = f.read()
    if 'from "@/i18n"' not in s and "useT" not in s:
        s = re.sub(r'(import .* from "@/[^"]+";\n)(?!import )',
                   r'\1import { useT } from "@/i18n";\n', s, count=1)
        # add useT call in main exported function
        s = re.sub(r'export function ([A-Z][A-Za-z0-9_]*)\(([^)]*)\) \{(\n)',
                   r'export function \1(\2) {\3  const t = useT();\n', s, count=1)
        with open(fp, "w", encoding="utf-8") as f:
            f.write(s)
        print("ApiKeyForm: added useT")

# HeatmapGrid - direct umlauts
patch("components/board/HeatmapGrid.tsx", [
    ('`${c.date} - ${c.activity_events} Aktivitäten, ${c.tasks_completed} Tasks, ${c.conversation_hours.toFixed(1)} h Gespräche`',
     't("board.heatmap_tooltip").replace("{0}", c.date).replace("{1}", String(c.activity_events)).replace("{2}", String(c.tasks_completed)).replace("{3}", c.conversation_hours.toFixed(1))'),
])

# Add useT to HeatmapGrid
fp = os.path.join(BASE, "components/board/HeatmapGrid.tsx").replace("\\", "/")
with open(fp, encoding="utf-8") as f:
    s = f.read()
if 'from "@/i18n"' not in s:
    s = re.sub(r'(import .* from "[^"]+";\n)(?=\nexport function)',
               r'\1import { useT } from "@/i18n";\n', s, count=1)
    s = re.sub(r'(export function HeatmapGrid\([^)]*\) \{\n)',
               r'\1  const t = useT();\n', s, count=1)
    with open(fp, "w", encoding="utf-8") as f:
        f.write(s)
    print("HeatmapGrid: added useT")

# DocsContent
patch("components/docs/DocsContent.tsx", [
    ('title="Markdown-Datei im OS-Standard-Editor öffnen"', 'title={t("common.open_in_editor")}'),
    ('für die Volltextsuche.', '{t("docs.for_fulltext")}'),
])

# DocsSearchModal
patch("components/docs/DocsSearchModal.tsx", [
    ('Keine Treffer für „{debouncedQuery}"',
     '{t("docs.no_results").replace("{0}", debouncedQuery)}'),
])

# SessionList
patch("components/sessions/SessionList.tsx", [
    ('return "läuft";', 'return t("sessions.running");'),
    ('"läuft"', 't("sessions.running")'),
])

# OutputsView
patch("views/OutputsView.tsx", [
    ('title="Im Explorer öffnen"', 'title={t("common.open_in_explorer")}'),
])

# SkillsView
patch("views/SkillsView.tsx", [
    ('title="Zurück zur Liste"', 'title={t("skills_toast.back_to_list")}'),
    ('`${label}${stale ? " (stale, refresh läuft)" : ""}`',
     '`${label}${stale ? t("skills_toast.stale_refreshing") : ""}`'),
])

# TerminalView
patch("views/TerminalView.tsx", [
    ('`\\r\\n\\x1b[33m[Sitzung beendet — exit ${code}]\\x1b[0m\\r\\n`',
     't("terminal_xterm.session_ended").replace("{0}", String(code))'),
    ('`${cliName}: Install lief durch (exit 0) aber Binary wurde nicht gefunden. PATH evtl. nicht aktualisiert.`',
     't("terminal_xterm.install_no_binary").replace("{0}", cliName)'),
    ('`${cliName}: Install fehlgeschlagen (exit ${code}). Versuch eine andere Methode.`',
     't("terminal_xterm.install_failed").replace("{0}", cliName).replace("{1}", String(code))'),
])

# ApiKeysView - " (aktiv ab nächstem Voice-Start)"
patch("views/ApiKeysView.tsx", [
    ('? " (aktiv ab nächstem Voice-Start)"', '? " (active from next voice start)"'),
])

# Need useT in DocsContent + DocsSearchModal + SessionList if not present
for f_rel in [
    "components/docs/DocsContent.tsx",
    "components/docs/DocsSearchModal.tsx",
    "components/docs/DocsSidebar.tsx",
    "components/sessions/SessionList.tsx",
]:
    fp = os.path.join(BASE, f_rel).replace("\\", "/")
    if not os.path.exists(fp):
        continue
    with open(fp, encoding="utf-8") as f:
        s = f.read()
    if 'from "@/i18n"' not in s:
        # Insert import right after first import block
        s = re.sub(r'(import .* from "[^"]+";\n)(?=\n(?:export |function |interface |const |/\*\*))',
                   r'\1import { useT } from "@/i18n";\n', s, count=1)
        # Try adding hook to main exported function
        m = re.search(r'export function ([A-Z][A-Za-z0-9_]*)\(([^)]*)\)\s*[:\w<>\[\] ,|]*\s*\{(\n)', s)
        if m:
            # Only add if there's no "const t = useT()" already
            if "const t = useT()" not in s[m.end():m.end()+200]:
                s = s[:m.end()] + "  const t = useT();\n" + s[m.end():]
        with open(fp, "w", encoding="utf-8") as f:
            f.write(s)
        print(f"{f_rel}: added useT import")
print("\nDone.")
