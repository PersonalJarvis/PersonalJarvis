import { useCallback, useEffect, useState } from "react";
import { AlertTriangle, ClipboardCopy, Keyboard } from "lucide-react";

import { ViewHeader } from "@/views/ChatsView";
import { Button } from "@/components/ui/button";
import { ACTION_LABEL_KEY } from "@/views/settings/KeybindRow";
import { useKeybinds, type KeybindAction } from "@/hooks/useHotkey";
import { useEventStore } from "@/store/events";
import { ShortcutsKeyRow } from "@/views/voice/ShortcutsKeyRow";
import { VoiceGroup, VoiceNote, VoicePage, VoiceSection } from "@/views/voice/voiceUi";
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
    hintKey: "voice.shortcuts.paste_last_hint_short",
  },
];

/**
 * "Shortcuts" tab of the merged voice section — every key that has to do with
 * dictation, as three rows of one group:
 *
 *   * Push to talk  → the `dictate` action (hold the keys, speak, let go)
 *   * Hands-free    → the `dictate_toggle` action (press once, press again)
 *   * Paste again   → the `paste_last` action (re-insert the last transcript)
 *
 * The recorder behind each row is `useKeybindEditor`, the same state Settings'
 * keybind rows use, so recording, live validation, the collision check and the
 * on-screen keyboard behave identically in both places.
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
  // One recorder at a time: starting a second row ends the first.
  const [recordingAction, setRecordingAction] = useState<KeybindAction | null>(null);

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

  const onRecordingChange = useCallback((action: KeybindAction, recording: boolean) => {
    setRecordingAction((prev) => (recording ? action : prev === action ? null : prev));
  }, []);

  // A dictation row is named the way this tab labels it; every other action
  // (call, hang up, …) the way Settings does.
  const actionLabel = useCallback(
    (action: string) => {
      const row = ROWS.find((r) => r.action === action);
      if (row) return t(row.labelKey);
      const key = ACTION_LABEL_KEY[action as KeybindAction];
      return key ? t(key) : action;
    },
    [t],
  );

  // Only a KNOWN "toggle" raises the notice. An older backend that reports no
  // mode at all must not accuse the user of a setting they may not have.
  const pttIsToggle = status?.mode === "toggle";
  const insertionBlocked = status?.insertion?.can_insert === false;

  return (
    <div className="flex h-full min-h-0 flex-col">
      {!hideHeader && (
        <ViewHeader
          icon={<Keyboard className="h-4 w-4 text-foreground" />}
          title={t("voice.shortcuts.title")}
          subtitle={t("voice.shortcuts.description")}
        />
      )}
      <div className="min-h-0 flex-1">
        <VoicePage testId="voice-shortcuts-tab">
          <VoiceSection
            title={t("voice.shortcuts.section_title")}
            description={t("voice.shortcuts.section_description")}
          >
            {error && (
              <VoiceNote tone="error" icon={<AlertTriangle />}>
                {error}
              </VoiceNote>
            )}
            <VoiceGroup testId="shortcuts-group">
              {ROWS.map((row) => (
                <ShortcutsKeyRow
                  key={row.action}
                  action={row.action}
                  label={t(row.labelKey)}
                  hint={t(row.hintKey)}
                  config={config}
                  loading={loading}
                  onSave={saveKeybind}
                  suggestions={config?.suggestions}
                  onSaved={row.action === "dictate" ? pinHoldMode : undefined}
                  actionLabel={actionLabel}
                  recordingAction={recordingAction}
                  onRecordingChange={onRecordingChange}
                  notes={
                    row.action === "dictate" && pttIsToggle ? (
                      // A setting that contradicts its own label is degraded,
                      // not broken — a warning note with the one-click fix.
                      <VoiceNote
                        tone="warning"
                        icon={<AlertTriangle />}
                        testId="shortcuts-mode-notice"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-2">
                          <span>{t("voice.shortcuts.mode_notice_short")}</span>
                          <Button
                            size="sm"
                            variant="outline"
                            data-testid="shortcuts-mode-fix"
                            onClick={() => void pinHoldMode()}
                          >
                            {t("voice.shortcuts.mode_notice_fix")}
                          </Button>
                        </div>
                      </VoiceNote>
                    ) : row.action === "paste_last" && insertionBlocked ? (
                      // "The key works, the paste does not" is the degraded
                      // case this note exists to name.
                      <VoiceNote
                        tone="warning"
                        icon={<ClipboardCopy />}
                        testId="shortcuts-paste-last-blocked"
                      >
                        {t("voice.shortcuts.paste_last_blocked_short")}
                      </VoiceNote>
                    ) : undefined
                  }
                />
              ))}
            </VoiceGroup>
          </VoiceSection>
        </VoicePage>
      </div>
    </div>
  );
}
