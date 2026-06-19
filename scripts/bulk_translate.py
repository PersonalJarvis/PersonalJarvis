"""Bulk-translate German strings across the frontend."""
import os
import re

BASE = "C:/Users/Administrator/Desktop/Personal Jarvis/jarvis/ui/web/frontend/src"

def patch_file(rel_path, replacements, ensure_imports=None, ensure_hooks=None):
    fp = os.path.join(BASE, rel_path).replace("\\", "/")
    if not os.path.exists(fp):
        print(f"SKIP (missing): {rel_path}")
        return
    with open(fp, encoding="utf-8") as f:
        content = f.read()
    original = content

    if ensure_imports:
        for marker, insertion in ensure_imports:
            if insertion not in content and marker in content:
                # insert insertion after the line containing marker
                idx = content.index(marker)
                # find end of this line
                line_end = content.index("\n", idx)
                content = content[:line_end + 1] + insertion + "\n" + content[line_end + 1:]

    if ensure_hooks:
        for marker, insertion in ensure_hooks:
            if insertion not in content and marker in content:
                content = content.replace(marker, marker + "\n" + insertion, 1)

    found = 0
    notfound = []
    for old, new in replacements:
        if old in content:
            content = content.replace(old, new)
            found += 1
        else:
            notfound.append(old[:60])

    if content != original:
        with open(fp, "w", encoding="utf-8") as f:
            f.write(content)
    print(f"  {rel_path}: {found}/{len(replacements)}")
    for nf in notfound:
        print(f"    NOT FOUND: {nf}")


# ---------- ApiKeyForm ----------
patch_file(
    "components/ApiKeyForm.tsx",
    [
        ('`Speichern fehlgeschlagen: ${(e as Error).message}`',
         '`${t("common.save_failed")}: ${(e as Error).message}`'),
        ('`Löschen fehlgeschlagen: ${(e as Error).message}`',
         '`${t("common.delete_failed")}: ${(e as Error).message}`'),
        ('`L\xf6schen fehlgeschlagen: ${(e as Error).message}`',
         '`${t("common.delete_failed")}: ${(e as Error).message}`'),
    ],
)

# ---------- CliConnectCoach ----------
patch_file(
    "components/CliConnectCoach.tsx",
    [
        ('`${coach.displayName} ist verbunden`',
         't("cli_connect.is_connected").replace("{0}", coach.displayName)'),
    ],
)

# ---------- CliConnectPoller ----------
patch_file(
    "components/CliConnectPoller.tsx",
    [
        ('`${coach.displayName} ist verbunden`',
         't("cli_connect.is_connected").replace("{0}", coach.displayName)'),
        ('`${coach.displayName}: Login-Pruefung nach 5min erfolglos abgebrochen. Manuell neu starten falls noetig.`',
         't("cli_connect.login_aborted").replace("{0}", coach.displayName)'),
    ],
)

# ---------- MascotGigi ----------
patch_file(
    "components/MascotGigi.tsx",
    [
        ('`wechsel auf ${brainProvider}. ok!`',
         't("mascot.brain_switched").replace("{0}", brainProvider)'),
    ],
)

# ---------- HeatmapGrid ----------
patch_file(
    "components/board/HeatmapGrid.tsx",
    [
        ('`${c.date} - ${c.activity_events} Aktivitaeten, ${c.tasks_completed} Tasks, ${c.conversation_hours.toFixed(1)} h Gespraeche`',
         't("board.heatmap_tooltip").replace("{0}", c.date).replace("{1}", String(c.activity_events)).replace("{2}", String(c.tasks_completed)).replace("{3}", c.conversation_hours.toFixed(1))'),
    ],
)

# ---------- PairDialog ----------
patch_file(
    "components/board/PairDialog.tsx",
    [
        ('schliessen', '{t("common.close")}'),
        ('schließen', '{t("common.close")}'),
    ],
)

# ---------- StoryComposer ----------
patch_file(
    "components/board/StoryComposer.tsx",
    [
        ('schliessen', '{t("story_composer.close")}'),
        ('schließen', '{t("story_composer.close")}'),
    ],
)

# ---------- ReactionBar ----------
patch_file(
    "components/board/ReactionBar.tsx",
    [
        ('Andere haben reagiert', '{t("reactions.others_reacted")}'),
    ],
)

# ---------- DocsContent ----------
patch_file(
    "components/docs/DocsContent.tsx",
    [
        ('title="Markdown-Datei im OS-Standard-Editor oeffnen"', 'title={t("common.open_in_editor")}'),
        ('title="Markdown-Datei im OS-Standard-Editor öffnen"', 'title={t("common.open_in_editor")}'),
        ('Konnte Doc nicht laden.', '{t("docs.could_not_load")}'),
        ('fuer die Volltextsuche.', '{t("docs.for_fulltext")}'),
        ('für die Volltextsuche.', '{t("docs.for_fulltext")}'),
    ],
)

# ---------- DocsSidebar ----------
patch_file(
    "components/docs/DocsSidebar.tsx",
    [
        ('title="Volltextsuche (Strg+K)"', 'title={t("docs.fulltext_search")}'),
        ('placeholder="Filter Titel/Tag…"', 'placeholder={t("docs.search_placeholder")}'),
    ],
)

# ---------- DocsSearchModal ----------
patch_file(
    "components/docs/DocsSearchModal.tsx",
    [
        ('<Dialog.Title className="sr-only">Docs durchsuchen</Dialog.Title>',
         '<Dialog.Title className="sr-only">{t("docs.search_modal_title")}</Dialog.Title>'),
        ('placeholder="Docs durchsuchen…"', 'placeholder={t("docs.search_modal_placeholder")}'),
        ('Tippe einen Begriff zum Suchen…', '{t("docs.search_hint")}'),
        ('                  Suche…', '                  {t("docs.search_loading")}'),
        ('Keine Treffer fuer „{debouncedQuery}"',
         '{t("docs.no_results").replace("{0}", debouncedQuery)}'),
        ('Keine Treffer für „{debouncedQuery}"',
         '{t("docs.no_results").replace("{0}", debouncedQuery)}'),
    ],
)

# ---------- AddFriendMenu ----------
patch_file(
    "components/friends/AddFriendMenu.tsx",
    [
        ('Anzeigename', '{t("add_friend.display_name")}'),
    ],
)

# ---------- SessionDetail ----------
patch_file(
    "components/sessions/SessionDetail.tsx",
    [
        ('Wähle links eine Session aus.', '{t("sessions.select_one")}'),
        ('Diese Session enthält keine Turns — vermutlich nur ein Wake-Word',
         '{t("sessions.no_turns")}'),
    ],
)

# ---------- SessionList ----------
patch_file(
    "components/sessions/SessionList.tsx",
    [
        ('return "läuft";', 'return t("sessions.running");'),
        ('"läuft"', 't("sessions.running")'),
    ],
)

# ---------- ProviderSwitcher ----------
patch_file(
    "components/ProviderSwitcher.tsx",
    [
        ('Provider-Liste wird geladen — falls dauerhaft leer, prüfe das Backend.',
         '{t("provider_switcher.loading_hint")}'),
    ],
)

# ---------- ApiKeysView ----------
patch_file(
    "views/ApiKeysView.tsx",
    [
        ('`${descriptor.label}: erst Codex verbinden oder API-Key speichern, dann aktivieren.`',
         't("apikeys_codex.needs_codex_full").replace("{0}", descriptor.label)'),
        ('`${descriptor.label}: erst API-Key speichern, dann aktivieren.`',
         't("apikeys_codex.needs_key_full").replace("{0}", descriptor.label)'),
        ('"codex login wurde im Terminal gestartet"', 't("apikeys_codex.login_started")'),
        ('"Codex wurde getrennt"', 't("apikeys_codex.disconnected")'),
        ('?? "Codex-Status wird geladen"', '?? t("apikeys_codex.status_loading")'),
    ],
)

# ---------- McpsView ----------
patch_file(
    "views/McpsView.tsx",
    [
        ('`${vars.name} verbunden`',
         't("mcps_toast.connected").replace("{0}", vars.name)'),
    ],
)

# ---------- OutputsView ----------
patch_file(
    "views/OutputsView.tsx",
    [
        ('title="Im Explorer oeffnen"', 'title={t("common.open_in_explorer")}'),
        ('title="Im Explorer öffnen"', 'title={t("common.open_in_explorer")}'),
    ],
)

# ---------- ProfileView ----------
patch_file(
    "views/ProfileView.tsx",
    [
        ('`${res.applied} Fact übernommen`',
         't("profile_toast.fact_applied").replace("{0}", String(res.applied))'),
    ],
)

# ---------- SkillsView ----------
patch_file(
    "views/SkillsView.tsx",
    [
        ('title="Zurueck zur Liste"', 'title={t("skills_toast.back_to_list")}'),
        ('title="Zurück zur Liste"', 'title={t("skills_toast.back_to_list")}'),
        ('`${label}${stale ? " (stale, refresh laeuft)" : ""}`',
         '`${label}${stale ? t("skills_toast.stale_refreshing") : ""}`'),
        ('`${label}${stale ? " (stale, refresh läuft)" : ""}`',
         '`${label}${stale ? t("skills_toast.stale_refreshing") : ""}`'),
    ],
)

# ---------- TerminalView ----------
patch_file(
    "views/TerminalView.tsx",
    [
        ('`\\r\\n\\x1b[33m[Sitzung beendet — exit ${code}]\\x1b[0m\\r\\n`',
         't("terminal_xterm.session_ended").replace("{0}", String(code))'),
        ('`${cliName}: Install lief durch (exit 0) aber Binary wurde nicht gefunden. PATH evtl. nicht aktualisiert.`',
         't("terminal_xterm.install_no_binary").replace("{0}", cliName)'),
        ('`${cliName}: Install fehlgeschlagen (exit ${code}). Versuch eine andere Methode.`',
         't("terminal_xterm.install_failed").replace("{0}", cliName).replace("{1}", String(code))'),
    ],
)

print("\nDone.")
