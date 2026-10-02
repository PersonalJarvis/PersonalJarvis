import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import { useT } from "@/i18n";
import { formatAppshotHotkey } from "@/lib/appshotApi";
import { chordFromCodes } from "@/lib/appshotChord";
import { cn } from "@/lib/utils";

/**
 * One appshot shortcut: the current keys as keycaps, a Change button that
 * records the next key gesture, and an off switch (X).
 *
 * Recording listens on `window` in the capture phase, so the keys never reach
 * the rest of the app, and commits when every key is let go — "hold your keys,
 * then let go", like the voice keybinds. Both Alt / both Shift / both Ctrl keys
 * on their own record as the two-sided gestures the backend watches. Esc (on
 * its own) or leaving the window cancels and keeps the old shortcut.
 */
export function AppshotShortcutField({
  value,
  isMac,
  disabled,
  testId,
  onSave,
}: {
  value: string;
  isMac: boolean;
  disabled: boolean;
  testId: string;
  onSave: (hotkey: string) => Promise<void>;
}) {
  const t = useT();
  const [recording, setRecording] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;

  const commit = useCallback(async (hotkey: string) => {
    setSaving(true);
    try {
      await onSaveRef.current(hotkey);
    } finally {
      setSaving(false);
    }
  }, []);

  useEffect(() => {
    if (!recording) return;
    const pressed = new Set<string>();
    const seen = new Set<string>();

    const stop = () => setRecording(false);
    const onKeyDown = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      if (event.repeat) return;
      if (event.code === "Escape" && seen.size === 0) {
        stop();
        return;
      }
      pressed.add(event.code);
      seen.add(event.code);
    };
    const onKeyUp = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      pressed.delete(event.code);
      if (pressed.size > 0 || seen.size === 0) return;
      stop();
      const result = chordFromCodes(seen);
      if ("combo" in result) {
        setProblem("");
        if (result.combo !== value) void commit(result.combo);
      } else if (result.problem === "modifier_only") {
        setProblem(t("appshots.shortcut_modifier_only"));
      }
    };

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("blur", stop);
    return () => {
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
      window.removeEventListener("blur", stop);
    };
  }, [recording, value, commit, t]);

  const keys = value ? formatAppshotHotkey(value, isMac).split(" + ") : [];

  return (
    <div className="flex flex-col items-end gap-1" data-testid={testId}>
      <div className="flex items-center gap-2">
        <div
          className={cn(
            "flex min-h-8 min-w-28 items-center justify-end gap-1 rounded-md px-2",
            recording && "bg-accent/10 ring-1 ring-accent",
          )}
          aria-live="polite"
        >
          {recording ? (
            <span className="text-sm text-foreground">{t("appshots.shortcut_recording")}</span>
          ) : keys.length > 0 ? (
            keys.map((key, i) => (
              <Fragment key={`${key}-${i}`}>
                {i > 0 && <span className="text-xs text-muted-foreground/60">+</span>}
                <kbd className="rounded border border-border bg-muted px-1.5 py-0.5 font-mono text-xs text-foreground shadow-[inset_0_-1px_0_rgba(0,0,0,0.25)]">
                  {key}
                </kbd>
              </Fragment>
            ))
          ) : (
            <span className="text-sm text-muted-foreground">{t("appshots.shortcut_off")}</span>
          )}
        </div>
        <Button
          type="button"
          variant="secondary"
          size="sm"
          disabled={disabled || saving}
          onClick={() => {
            setProblem("");
            setRecording((on) => !on);
          }}
          data-testid={`${testId}-change`}
        >
          {saving && <Loader2 className="animate-spin" aria-hidden />}
          {recording ? t("appshots.shortcut_cancel") : t("appshots.shortcut_change")}
        </Button>
        {value && !recording && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={disabled || saving}
            aria-label={t("appshots.shortcut_clear")}
            title={t("appshots.shortcut_clear")}
            onClick={() => void commit("")}
            data-testid={`${testId}-clear`}
          >
            <X aria-hidden />
          </Button>
        )}
      </div>
      {recording && (
        <p className="max-w-72 text-right text-xs text-muted-foreground">
          {t("appshots.shortcut_recording_hint")}
        </p>
      )}
      {!recording && problem && (
        <p className="max-w-72 text-right text-xs text-destructive" role="alert">
          {problem}
        </p>
      )}
    </div>
  );
}
