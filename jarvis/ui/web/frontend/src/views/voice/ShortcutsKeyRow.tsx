import { useEffect, useId, type ReactNode } from "react";
import { AlertTriangle, RotateCcw, XCircle } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  comboTokens,
  type ComboCaution,
  type KeybindAction,
  type KeybindsConfig,
  type KeybindSaveResult,
} from "@/hooks/useHotkey";
import { formatCombo, useKeybindEditor, type KeybindEditor } from "@/views/settings/KeybindRow";
import { KeyboardMap } from "@/views/settings/KeyboardMap";
import { detectKeyboardPlatform } from "@/views/settings/keyboardLayout";
import { KeyCombo, StatusDot, VoiceNote, VoiceRow } from "@/views/voice/voiceUi";
import { useT } from "@/i18n";
import { cn } from "@/lib/utils";

// The keyboard family (Mac vs PC modifier labels) is fixed for the session.
const _KB_PLATFORM = detectKeyboardPlatform();

/**
 * One short sentence per caution. The Settings recorder explains each rule in
 * full; inside a dictation row the same rule only has to say what will happen.
 * `mouse_button` shares the system-shortcut sentence for the reason
 * `KeybindRow` gives: a bound mouse button is not swallowed either.
 */
const CAUTION_KEY: Record<ComboCaution, string> = {
  modifier_only: "voice.shortcuts.caution.modifier_only",
  os_shortcut: "voice.shortcuts.caution.os_shortcut",
  mouse_button: "voice.shortcuts.caution.os_shortcut",
  overlap: "voice.shortcuts.caution.overlap",
  solo_typing_key: "voice.shortcuts.caution.solo_typing_key",
  solo_nav: "voice.shortcuts.caution.solo_nav",
};

/** The combo as display keycaps ("ctrl+right_alt+j" → Ctrl, AltGr, J). */
function comboKeys(combo: string): string[] {
  return formatCombo(combo).split(" + ").filter(Boolean);
}

/**
 * What the row says about the combo on screen, or null.
 *
 * Same precedence as the Settings recorder: a collision is the one blocking
 * message and always speaks; cautions wait until the keys are let go, because
 * a half-built chord is not a decision yet.
 */
function comboNotice(
  editor: KeybindEditor,
  t: (key: string) => string,
  actionLabel: (action: string) => string,
): { tone: "error" | "warning"; text: string } | null {
  const v = editor.validation;
  if (v.status === "error" && v.reason === "collision" && v.conflict) {
    return {
      tone: "error",
      text: t("voice.shortcuts.collision").replace("{action}", actionLabel(v.conflict.action)),
    };
  }
  if (editor.holding || v.cautions.length === 0) return null;
  const sentences = [...new Set(v.cautions.map((c) => t(CAUTION_KEY[c])))];
  return { tone: "warning", text: sentences.join(" ") };
}

export interface ShortcutsKeyRowProps {
  action: KeybindAction;
  label: string;
  hint: string;
  config: KeybindsConfig | null;
  loading: boolean;
  onSave: (a: KeybindAction, h: string) => Promise<KeybindSaveResult>;
  suggestions?: string[];
  onSaved?: (combo: string) => void | Promise<void>;
  /** Names any action the way this tab labels it ("Push to talk"). */
  actionLabel: (action: string) => string;
  /** Which row is recording right now; another row's recording ends this one's. */
  recordingAction: KeybindAction | null;
  onRecordingChange: (action: KeybindAction, recording: boolean) => void;
  /** Notes about this key that come from outside the recorder (mode, insertion). */
  notes?: ReactNode;
}

/**
 * One dictation key as a row of the Shortcuts tab: the name and one sentence
 * on the left, the keycaps and a single Change control on the right. Every
 * recorder detail — the live prompt, suggestions, the on-screen keyboard,
 * Remove and Reset — appears only while the row is being edited; what remains
 * otherwise is the combo and, when there is one, a short note about it.
 *
 * The behaviour (recording, validation, auto-save) is `useKeybindEditor`, the
 * same state Settings' keybind rows use.
 */
export function ShortcutsKeyRow({
  action,
  label,
  hint,
  config,
  loading,
  onSave,
  suggestions,
  onSaved,
  actionLabel,
  recordingAction,
  onRecordingChange,
  notes,
}: ShortcutsKeyRowProps) {
  const t = useT();
  const editor = useKeybindEditor({
    action,
    config,
    onSave,
    suggestions,
    onSaved,
    actionLabel,
  });
  const { combo, capturing, current } = editor;
  const hintId = useId();
  const statusId = useId();

  // Tell the tab when this row starts or stops recording …
  useEffect(() => {
    onRecordingChange(action, capturing);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [capturing]);

  // … and stand down when another row starts: two recorders listening on the
  // same window would both take the next chord.
  useEffect(() => {
    if (capturing && recordingAction && recordingAction !== action) editor.cancelCapture();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [recordingAction]);

  const notice = comboNotice(editor, t, actionLabel);
  // Untouched since the recorder opened: the field asks for keys instead of
  // showing the old combo as if it were the new one.
  const awaitingKeys = capturing && editor.pressedCodes.size === 0 && combo === current;
  const liveText = editor.holding
    ? t("voice.shortcuts.recording_holding")
    : t("voice.shortcuts.recording_hint");

  const field = (
    <span
      data-testid={`combo-field-${action}`}
      // App-level chords (the quick switcher) stand down while this is set, so
      // the keys being recorded reach the recorder instead of opening something.
      data-keybind-recording={capturing ? "true" : undefined}
      className={cn(
        "inline-flex min-h-8 items-center gap-2 rounded-md",
        capturing && "bg-accent-soft px-2.5 py-1 ring-2 ring-accent",
      )}
    >
      {capturing && <StatusDot tone="live" live />}
      {awaitingKeys ? (
        <span className="text-sm text-foreground">{t("settings_view.keybinds.recording")}</span>
      ) : combo ? (
        <KeyCombo keys={comboKeys(combo)} />
      ) : (
        <span className="text-sm text-muted-foreground">
          {loading ? "—" : t("settings_view.keybinds.unbound")}
        </span>
      )}
    </span>
  );

  const control = (
    <>
      {field}
      <Button
        type="button"
        size="sm"
        variant="outline"
        data-testid={`record-keybind-${action}`}
        aria-label={
          capturing
            ? t("voice.shortcuts.cancel_aria").replace("{0}", label)
            : t("voice.shortcuts.change_aria").replace("{0}", label)
        }
        aria-describedby={capturing ? statusId : hintId}
        aria-expanded={capturing}
        disabled={loading}
        onClick={() => (capturing ? editor.cancelCapture() : editor.setCapturing(true))}
      >
        {capturing ? t("common.cancel") : t("voice.shortcuts.change")}
      </Button>
    </>
  );

  return (
    <VoiceRow
      testId={`shortcut-row-${action}`}
      title={label}
      description={<span id={hintId}>{hint}</span>}
      control={control}
    >
      {capturing && (
        <div
          className="space-y-3 rounded-lg border border-border bg-background p-3"
          data-testid={`shortcut-editor-${action}`}
        >
          <p
            id={statusId}
            role="status"
            aria-live="polite"
            className="text-sm text-muted-foreground"
          >
            {liveText}
          </p>

          {editor.freeSuggestions.length > 0 && (
            <div className="flex flex-wrap items-center gap-2">
              <span className="text-xs text-muted-foreground">
                {t("voice.shortcuts.suggestions_title")}
              </span>
              {editor.freeSuggestions.map((s) => (
                <button
                  key={s}
                  type="button"
                  data-testid={`suggestion-${action}-${s}`}
                  onClick={() => editor.assign(s)}
                  className="inline-flex min-h-8 items-center rounded-md border border-border bg-card px-2 text-xs text-foreground transition-colors hover:border-border-strong focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  {formatCombo(s)}
                </button>
              ))}
            </div>
          )}

          <KeyboardMap
            pressedCodes={editor.pressedCodes}
            selectedTokens={comboTokens(combo)}
            boundTokens={editor.boundTokens}
            platform={_KB_PLATFORM}
            onToggleToken={editor.onToggleToken}
            mouseSupported={editor.mouseSupported}
            mouseReason={editor.mouseReason}
          />

          <div className="flex flex-wrap items-center gap-2">
            {editor.showReset && editor.def && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                data-testid={`reset-keybind-${action}`}
                onClick={editor.resetToDefault}
                className="gap-1.5"
              >
                <RotateCcw aria-hidden="true" className="h-3.5 w-3.5" />
                {t("settings_view.keybinds.reset")}
                <span className="text-muted-foreground">({formatCombo(editor.def)})</span>
              </Button>
            )}
            <Button
              type="button"
              size="sm"
              variant="ghost"
              data-testid={`clear-keybind-${action}`}
              onClick={() => void editor.clear()}
              disabled={editor.saving || loading || !current}
            >
              {t("voice.shortcuts.remove")}
            </Button>
          </div>
        </div>
      )}

      {notice && (
        <VoiceNote
          tone={notice.tone}
          icon={notice.tone === "error" ? <XCircle /> : <AlertTriangle />}
          testId={`keybind-validation-${action}`}
        >
          {notice.text}
        </VoiceNote>
      )}

      {editor.saved && (
        <VoiceNote testId={`keybind-restart-${action}`}>
          {t("settings_view.keybinds.restart_required")}
        </VoiceNote>
      )}

      {notes}
    </VoiceRow>
  );
}
