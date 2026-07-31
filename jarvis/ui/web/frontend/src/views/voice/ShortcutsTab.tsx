import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, Keyboard } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
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

/** Live dictation state this tab needs — a thin slice of GET /api/dictation/status. */
interface ShortcutsStatus {
  /** "hold" | "toggle" — what the push-to-talk key actually does today. */
  mode?: string;
  insertion?: { can_insert?: boolean };
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
  {
    action: "paste_last",
    labelKey: "voice.shortcuts.paste_last_label",
    hintKey: "voice.shortcuts.paste_last_hint",
  },
];

/**
 * "Shortcuts" tab of the merged voice section — every key that has to do with
 * dictation, on ONE surface.
 *
 * Three rows over the SAME row component Settings uses, so the recorder, the
 * live validation, the collision check and the on-screen keyboard behave
 * identically in both places:
 *
 *   * Push to talk  → the `dictate` action (hold the keys, speak, let go)
 *   * Hands-free    → the `dictate_toggle` action (press once, press again)
 *   * Paste again   → the `paste_last` action (re-insert the last transcript)
 *
 * Two honesty rules this tab carries, because nothing else can:
 *
 * 1. **Push-to-talk must MEAN hold.** Saving that combo also pins
 *    `[dictation].mode` to "hold". A user left on "toggle" would hold the keys
 *    and get toggle behaviour — the label would be lying. Since the old
 *    "Key behaviour" dropdown is gone (the rows are the source of truth now),
 *    an install that is already on "toggle" would have no way back — so the
 *    row says so and offers the one-click fix. The hands-free row needs no
 *    pin: its action is release-independent by construction.
 * 2. **"Paste again" cannot always paste.** On Wayland, on a headless host, or
 *    with an elevated window in front, the OS blocks one program from typing
 *    into another. The key still works — the text goes to the clipboard — and
 *    the row says that up front instead of letting the user discover it by
 *    pressing a key that seems to do nothing.
 */
export function ShortcutsTab({ hideHeader = false }: ShortcutsTabProps = {}) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  const { config, loading, error, saveKeybind } = useKeybinds();
  const [status, setStatus] = useState<ShortcutsStatus | null>(null);

  const refetchStatus = useCallback(async () => {
    // Informational only: a backend that cannot answer leaves both notices
    // away rather than turning this tab into an error page. Every read below
    // therefore checks for the value explicitly instead of assuming one.
    try {
      const res = await fetch("/api/dictation/status");
      if (!res.ok) return;
      setStatus((await res.json()) as ShortcutsStatus);
    } catch {
      /* keep the last known state */
    }
  }, []);

  useEffect(() => {
    void refetchStatus();
  }, [refetchStatus]);

  const pinHoldMode = useCallback(async () => {
    try {
      const res = await fetch("/api/dictation/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ mode: "hold", persist: true }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      await refetchStatus();
    } catch (e) {
      // The keybind itself is already saved — report the missing side effect
      // instead of failing the whole save (or, worse, staying silent).
      pushToast("warning", (e as Error).message);
    }
  }, [pushToast, refetchStatus]);

  // Only a KNOWN "toggle" raises the notice. An older backend that reports no
  // mode at all must not accuse the user of a setting they may not have.
  const pttIsToggle = status?.mode === "toggle";
  const insertionBlocked = status?.insertion?.can_insert === false;

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

          {pttIsToggle && (
            <div
              className="flex items-start gap-3 rounded-lg border border-amber-500/40 bg-amber-500/10 p-3"
              data-testid="shortcuts-mode-notice"
            >
              <AlertTriangle className="mt-0.5 h-4 w-4 shrink-0 text-amber-500" />
              <div className="min-w-0 flex-1">
                <p className="text-xs text-muted-foreground">
                  {t("voice.shortcuts.mode_notice")}
                </p>
                <Button
                  size="sm"
                  variant="outline"
                  className="mt-2"
                  data-testid="shortcuts-mode-fix"
                  onClick={() => void pinHoldMode()}
                >
                  {t("voice.shortcuts.mode_notice_fix")}
                </Button>
              </div>
            </div>
          )}

          {ROWS.map((row) => (
            <div key={row.action} className="flex flex-col gap-1">
              <KeybindRow
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
              {row.action === "paste_last" && insertionBlocked && (
                <p
                  className="text-[11px] text-amber-500"
                  data-testid="shortcuts-paste-last-blocked"
                >
                  {t("voice.shortcuts.paste_last_blocked")}
                </p>
              )}
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
