import { Keyboard } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { KeybindRow } from "@/views/settings/KeybindRow";
import { useKeybinds, type KeybindAction } from "@/hooks/useHotkey";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

export interface ShortcutsTabProps {
  /**
   * Suppress this view's own `ViewHeader`.
   *
   * Set by the merged voice section, which renders one "{name} Voice" header
   * above the tab bar — a second bordered band right below it reads as a
   * rendering fault. Standalone rendering keeps its own header.
   */
  hideHeader?: boolean;
}

const ROWS: {
  action: KeybindAction;
  labelKey: string;
  hintKey: string;
}[] = [
  {
    action: "dictate",
    labelKey: "voice.shortcuts.ptt_label",
    hintKey: "voice.shortcuts.ptt_hint",
  },
  {
    action: "dictate_toggle",
    labelKey: "voice.shortcuts.toggle_label",
    hintKey: "voice.shortcuts.toggle_hint",
  },
];

/**
 * "Shortcuts" tab of the merged voice section — the keys that start dictation.
 *
 * Two rows over the SAME row component Settings uses, so the recorder, the live
 * validation, the collision check and the on-screen keyboard behave identically
 * in both places:
 *
 *   * Push to talk  → the `dictate` action (hold the keys, speak, let go)
 *   * Hands-free    → the `dictate_toggle` action (press once, press again)
 *
 * Saving the push-to-talk combo also pins `[dictation].mode` to "hold". A user
 * who has switched the mode to "toggle" would otherwise hold the push-to-talk
 * keys and get toggle behaviour — the label would be lying. The hands-free row
 * needs no such pin: its action is release-independent by construction.
 */
export function ShortcutsTab({ hideHeader = false }: ShortcutsTabProps = {}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const { config, loading, error, saveKeybind } = useKeybinds();

  async function pinHoldMode() {
    try {
      const res = await fetch("/api/dictation/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "hold", persist: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
    } catch (e) {
      // The keybind itself is already saved — report the missing side effect
      // instead of failing the whole save (or, worse, staying silent).
      pushToast("warning", (e as Error).message);
    }
  }

  return (
    <div className="flex h-full flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<Keyboard className="h-4 w-4 text-primary" />}
          title={t("voice.shortcuts.title")}
          subtitle={t("voice.shortcuts.description")}
        />
      )}
      <div
        className="flex-1 overflow-y-auto scrollbar-jarvis p-6"
        data-testid="voice-shortcuts-tab"
      >
        <div className="mx-auto flex max-w-3xl flex-col gap-4">
          {/* Embedded, the band above carries the section brand rather than
              this tab's purpose — so the purpose is stated here instead. */}
          {hideHeader && (
            <p className="text-xs text-muted-foreground">
              {t("voice.shortcuts.description")}
            </p>
          )}
          {error && <p className="text-xs text-destructive">{error}</p>}
          {ROWS.map((row) => (
            <KeybindRow
              key={row.action}
              action={row.action}
              variant="voice"
              label={t(row.labelKey)}
              hint={t(row.hintKey)}
              config={config}
              loading={loading}
              onSave={saveKeybind}
              suggestions={config?.suggestions}
              onSaved={row.action === "dictate" ? pinHoldMode : undefined}
            />
          ))}
        </div>
      </div>
    </div>
  );
}
