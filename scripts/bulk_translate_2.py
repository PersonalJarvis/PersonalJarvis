"""Second-pass bulk translation - umlaut-aware string replacements."""
import os

BASE = "<your-home>/Desktop/Personal Jarvis/jarvis/ui/web/frontend/src"

def patch_file(rel_path, replacements):
    fp = os.path.join(BASE, rel_path).replace("\\", "/")
    if not os.path.exists(fp):
        print(f"SKIP: {rel_path}")
        return
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    found = 0
    notfound = []
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            found += 1
        else:
            notfound.append(old[:60])
    with open(fp, "w", encoding="utf-8") as f:
        f.write(content)
    print(f"{rel_path}: {found}/{len(replacements)}")
    for nf in notfound:
        print(f"  NOT FOUND: {nf!r}")


# Encoding-fixed entries
patch_file("components/ApiKeyForm.tsx", [
    ('`Löschen fehlgeschlagen: ${(e as Error).message}`',
     '`${t("common.delete_failed")}: ${(e as Error).message}`'),
])

patch_file("components/board/HeatmapGrid.tsx", [
    ('`${c.date} - ${c.activity_events} Aktivitäten, ${c.tasks_completed} Tasks, ${c.conversation_hours.toFixed(1)} h Gespräche`',
     't("board.heatmap_tooltip").replace("{0}", c.date).replace("{1}", String(c.activity_events)).replace("{2}", String(c.tasks_completed)).replace("{3}", c.conversation_hours.toFixed(1))'),
    ('`${c.date} — ${c.activity_events} Aktivitäten, ${c.tasks_completed} Tasks, ${c.conversation_hours.toFixed(1)} h Gespräche`',
     't("board.heatmap_tooltip").replace("{0}", c.date).replace("{1}", String(c.activity_events)).replace("{2}", String(c.tasks_completed)).replace("{3}", c.conversation_hours.toFixed(1))'),
])

patch_file("components/board/PairDialog.tsx", [
    ('schließen', '{t("common.close")}'),
])

patch_file("components/board/StoryComposer.tsx", [
    ('schließen', '{t("story_composer.close")}'),
])

patch_file("components/docs/DocsContent.tsx", [
    ('title="Markdown-Datei im OS-Standard-Editor öffnen"',
     'title={t("common.open_in_editor")}'),
    ('für die Volltextsuche.', '{t("docs.for_fulltext")}'),
])

patch_file("components/docs/DocsSearchModal.tsx", [
    ('Keine Treffer für „{debouncedQuery}"',
     '{t("docs.no_results").replace("{0}", debouncedQuery)}'),
])

patch_file("components/sessions/SessionList.tsx", [
    ('"läuft"', 't("sessions.running")'),
    ('return "läuft";', 'return t("sessions.running");'),
])

patch_file("views/OutputsView.tsx", [
    ('title="Im Explorer öffnen"', 'title={t("common.open_in_explorer")}'),
])

patch_file("views/SkillsView.tsx", [
    ('title="Zurück zur Liste"', 'title={t("skills_toast.back_to_list")}'),
    ('`${label}${stale ? " (stale, refresh läuft)" : ""}`',
     '`${label}${stale ? t("skills_toast.stale_refreshing") : ""}`'),
])

patch_file("views/TerminalView.tsx", [
    ('`\\r\\n\\x1b[33m[Sitzung beendet — exit ${code}]\\x1b[0m\\r\\n`',
     't("terminal_xterm.session_ended").replace("{0}", String(code))'),
    ('`${cliName}: Install lief durch (exit 0) aber Binary wurde nicht gefunden. PATH evtl. nicht aktualisiert.`',
     't("terminal_xterm.install_no_binary").replace("{0}", cliName)'),
    ('`${cliName}: Install fehlgeschlagen (exit ${code}). Versuch eine andere Methode.`',
     't("terminal_xterm.install_failed").replace("{0}", cliName).replace("{1}", String(code))'),
])

# ProfileView - LoadingState + subtitle
patch_file("views/ProfileView.tsx", [
    ('<RefreshCw className="h-4 w-4 animate-spin" /> Lade Profil…',
     '<RefreshCw className="h-4 w-4 animate-spin" /> {t("common.loading")}'),
    ('if (!data) return "USER.md, bekannte Personen und Review-Queue vom Curator.";',
     'if (!data) return null;'),
    ('parts.push(`${n} ${n === 1 ? "Person" : "Personen"}`);',
     'parts.push(`${n} ${n === 1 ? t("profile_view.person_singular") : t("profile_view.person_plural")}`);'),
    ('if (r > 0) parts.push(`${r} ${r === 1 ? "Review" : "Reviews"} offen`);',
     'if (r > 0) parts.push(`${r} ${r === 1 ? t("profile_view.review_singular") : t("profile_view.review_plural")} ${t("profile_view.review_open")}`);'),
])

# LoadingState needs useT
patch_file("views/ProfileView.tsx", [
    ('function LoadingState() {\n  return (',
     'function LoadingState() {\n  const t = useT();\n  return ('),
])

# ApiKeysView - " (aktiv ab naechstem Voice-Start)" (Linter changed it)
patch_file("views/ApiKeysView.tsx", [
    ('? " (aktiv ab naechstem Voice-Start)"', '? " (active from next voice start)"'),
    ('? " (aktiv ab nächstem Voice-Start)"', '? " (active from next voice start)"'),
])

# McpsView - "${vars.name} getrennt"
patch_file("views/McpsView.tsx", [
    ('vars.enable ? t("mcps_toast.connected").replace("{0}", vars.name) : `${vars.name} getrennt`,',
     'vars.enable ? t("mcps_toast.connected").replace("{0}", vars.name) : `${vars.name} ${t("mcps_view.disconnected").toLowerCase()}`,'),
])

print("\nDone.")
