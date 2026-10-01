import { Keyboard } from "lucide-react";
import { useT } from "@/i18n";
import { useKeybinds, type KeybindAction } from "@/hooks/useHotkey";
import { KeybindRow } from "@/views/settings/KeybindRow";

const _KEYBIND_ROWS: { action: KeybindAction; labelKey: string }[] = [
  { action: "call", labelKey: "settings_view.keybinds.call_label" },
  { action: "hangup", labelKey: "settings_view.keybinds.hangup_label" },
];

/**
 * Editable Call and Hangup keybinds, one row each — the two keys that start
 * and end a conversation. The user clicks Record and presses a combination, or
 * resets to default, then saves. The backend validator is the authority — an
 * unsafe combo or a collision with another action is rejected with a reason
 * shown as a toast. A successful save surfaces a restart-required hint.
 *
 * NO dictation row lives here. Dictation is a different act — it never reaches
 * the brain, it types into whatever window is in front, and it now has three
 * shortcuts of its own (hold, hands-free, paste again). Those belong together
 * on ONE surface, and that surface is the voice section's Shortcuts tab. This
 * panel is deliberately NOT synced with it: the two answer different questions,
 * and a row duplicated across both would let a user change the same key in two
 * places and see two different truths. (The Keyboard shortcuts page LISTS the
 * dictation keys read-only and links to that tab — it never edits them.)
 *
 * The row component itself is shared, so the recorder, the live validation and
 * the collision check behave identically in both places — the collision check
 * in particular still spans EVERY action, dictation included, because the
 * backend keeps serving the whole set. Fewer rows here, never less data.
 *
 * `bare` drops the card, icon and heading, for a host that draws its own (the
 * Keyboard shortcuts page).
 */
export function KeybindsPanel({ bare = false }: { bare?: boolean } = {}) {
  const t = useT();
  const { config, loading, error, saveKeybind } = useKeybinds();

  const rows = (
    <>
      {error && <p className="text-meta text-destructive">{error}</p>}
      <div className="space-y-stack">
        {_KEYBIND_ROWS.map((row) => (
          <KeybindRow
            key={row.action}
            action={row.action}
            label={t(row.labelKey)}
            config={config}
            loading={loading}
            onSave={saveKeybind}
          />
        ))}
      </div>
    </>
  );

  if (bare) return rows;

  return (
    <div className="rounded-lg border border-border bg-card p-block">
      <div className="flex items-start gap-3">
        <Keyboard className="mt-0.5 h-5 w-5 shrink-0 text-muted-foreground" />
        <div className="min-w-0 flex-1">
          <h4 className="text-title font-semibold text-foreground-strong">
            {t("settings_view.keybinds.title")}
          </h4>
          <p className="mt-1 text-meta text-muted-foreground">
            {t("settings_view.keybinds.description")}
          </p>
          <div className="mt-block">{rows}</div>
        </div>
      </div>
    </div>
  );
}
