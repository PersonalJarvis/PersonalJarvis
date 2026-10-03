import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";

import { useT } from "@/i18n";
import { formatAppshotHotkey } from "@/lib/appshotApi";
import { chordFromCodes } from "@/lib/appshotChord";
import { cn } from "@/lib/utils";

/**
 * One appshot shortcut as a single field, styled like the select beside it:
 * the keys as small keycaps, click to record new ones, a quiet × to turn the
 * shortcut off. What the user should do while recording (or why a gesture was
 * refused) is reported through ``onStatus`` so the row can show it in place
 * of its description instead of pushing the layout around.
 *
 * Recording listens on `window` in the capture phase, so the keys never reach
 * the rest of the app, and commits when every key is let go — "hold your keys,
 * then let go", like the voice keybinds. Both Alt / both Shift / both Ctrl keys
 * on their own record as the two-sided gestures the backend watches. Esc (on
 * its own), a second click or leaving the window cancels and keeps the old
 * shortcut.
 */
export function AppshotShortcutField({
  value,
  isMac,
  disabled,
  testId,
  label,
  className,
  onSave,
  onStatus,
}: {
  value: string;
  isMac: boolean;
  disabled: boolean;
  testId: string;
  label: string;
  className?: string;
  onSave: (hotkey: string) => Promise<void>;
  onStatus?: (text: string | null) => void;
}) {
  const t = useT();
  const [recording, setRecording] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;

  useEffect(() => {
    onStatusRef.current?.(recording ? t("appshots.shortcut_recording_hint") : problem || null);
  }, [recording, problem, t]);

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
    const characters = new Map<string, string>();

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
      // Windows/X11 match the logical letter (VK/key symbol), not its US
      // position. On QWERTZ, physical KeyY is the letter Z. macOS's backend
      // deliberately uses physical keycodes, so keep its existing mapping.
      if (!isMac && /^Key[A-Z]$/.test(event.code) && /^[a-z]$/i.test(event.key)) {
        characters.set(event.code, event.key.toLowerCase());
      }
    };
    const onKeyUp = (event: KeyboardEvent) => {
      event.preventDefault();
      event.stopPropagation();
      pressed.delete(event.code);
      if (pressed.size > 0 || seen.size === 0) return;
      stop();
      const result = chordFromCodes(seen, characters);
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
  }, [recording, value, commit, t, isMac]);

  const keys = value ? formatAppshotHotkey(value, isMac).split(" + ") : [];
  const busy = disabled || saving;

  return (
    <div className={cn("relative", className)} data-testid={testId}>
      <button
        type="button"
        disabled={busy}
        aria-label={`${label}: ${recording ? t("appshots.shortcut_recording") : keys.join(" + ") || t("appshots.shortcut_off")}`}
        aria-pressed={recording}
        title={recording ? undefined : t("appshots.shortcut_change")}
        onClick={() => {
          setProblem("");
          setRecording((on) => !on);
        }}
        data-testid={`${testId}-change`}
        className={cn(
          "flex h-9 w-full items-center gap-1 rounded-md bg-input py-2 pl-3 text-left text-sm text-foreground",
          "transition-colors duration-150 hover:bg-popover focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong",
          value && !recording ? "pr-8" : "pr-3",
          recording && "bg-popover ring-2 ring-border-strong",
          busy && "cursor-not-allowed opacity-50",
        )}
      >
        {saving ? (
          <Loader2 className="h-4 w-4 animate-spin text-muted-foreground" aria-hidden />
        ) : recording ? (
          <span className="truncate text-muted-foreground">{t("appshots.shortcut_recording")}</span>
        ) : keys.length > 0 ? (
          keys.map((key, i) => (
            <Fragment key={`${key}-${i}`}>
              {i > 0 && <span className="px-0.5 text-xs text-muted-foreground">+</span>}
              <kbd className="rounded border border-border bg-background px-1.5 py-px font-sans text-xs font-medium leading-5 text-foreground">
                {key}
              </kbd>
            </Fragment>
          ))
        ) : (
          <span className="truncate text-muted-foreground">{t("appshots.shortcut_off")}</span>
        )}
      </button>
      {value && !recording && (
        <button
          type="button"
          disabled={busy}
          aria-label={t("appshots.shortcut_clear")}
          title={t("appshots.shortcut_clear")}
          onClick={() => void commit("")}
          data-testid={`${testId}-clear`}
          className="absolute right-1.5 top-1/2 flex h-6 w-6 -translate-y-1/2 items-center justify-center rounded text-muted-foreground transition-colors hover:bg-background hover:text-foreground focus:outline-none focus-visible:ring-2 focus-visible:ring-border-strong disabled:opacity-50"
        >
          <X className="h-3.5 w-3.5" aria-hidden />
        </button>
      )}
      {problem && !recording && (
        <span className="sr-only" role="alert">
          {problem}
        </span>
      )}
    </div>
  );
}
