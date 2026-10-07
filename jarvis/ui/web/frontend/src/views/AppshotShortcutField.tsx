import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { Loader2, X } from "lucide-react";

import { useT } from "@/i18n";
import { codeToModifierToken, composeCombo } from "@/hooks/useHotkey";
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
 * the rest of the app. A finished chord is saved when the keys come up, when
 * a key-up was swallowed, or when the window loses focus (the Windows key and
 * Alt do that). A modifier released before the letter stays part of the same
 * gesture, so the keys do not have to land in the same instant. Both Alt /
 * both Shift / both Ctrl record as the two-sided gestures the backend watches.
 * Esc, or a second click before any real key, cancels and keeps the old shortcut.
 */

/** Rescue for a key-up the WebView never delivers. Modifiers do not use it. */
const LOST_KEYUP_MS = 900;

type FlagWord = { ctrl: boolean; alt: boolean; shift: boolean; meta: boolean };
type Family = "ctrl" | "alt" | "shift" | "meta";

function flagsOf(event: KeyboardEvent | MouseEvent): FlagWord {
  return {
    ctrl: event.ctrlKey,
    alt: event.altKey,
    shift: event.shiftKey,
    meta: event.metaKey,
  };
}

function familyOf(code: string): Family | null {
  if (code.startsWith("Control")) return "ctrl";
  if (code.startsWith("Alt")) return "alt";
  if (code.startsWith("Shift")) return "shift";
  if (code.startsWith("Meta")) return "meta";
  return null;
}

function codesFor(family: Family): string[] {
  switch (family) {
    case "ctrl":
      return ["ControlLeft", "ControlRight"];
    case "alt":
      return ["AltLeft", "AltRight", "AltGraph"];
    case "shift":
      return ["ShiftLeft", "ShiftRight"];
    case "meta":
      return ["MetaLeft", "MetaRight"];
  }
}

function canonical(family: Family): string {
  switch (family) {
    case "ctrl":
      return "ControlLeft";
    case "alt":
      return "AltLeft";
    case "shift":
      return "ShiftLeft";
    case "meta":
      return "MetaLeft";
  }
}

/** The letter the user actually typed, including keys outside A–Z. */
function typedCharacter(event: KeyboardEvent): string | null {
  if (event.key.length !== 1 || event.key < " " || event.key === " ") return null;
  return event.key.toLowerCase();
}
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
  const tRef = useRef(t);
  tRef.current = t;
  const valueRef = useRef(value);
  valueRef.current = value;
  const isMacRef = useRef(isMac);
  isMacRef.current = isMac;
  const [recording, setRecording] = useState(false);
  const [saving, setSaving] = useState(false);
  const [problem, setProblem] = useState("");
  const [draft, setDraft] = useState("");
  const onSaveRef = useRef(onSave);
  onSaveRef.current = onSave;
  const onStatusRef = useRef(onStatus);
  onStatusRef.current = onStatus;
  // The click that ends a recording has to see the keys already pressed.
  // The listener itself must NOT restart when the page re-renders: `t` is a
  // new function every render, and the recording panel refreshes every couple
  // of seconds. Either one used to wipe the gesture between key-down and key-up.
  const finishGesture = useRef<() => void>(() => {});

  useEffect(() => {
    const text = problem
      ? problem
      : recording
        ? t("appshots.shortcut_recording_hint")
        : null;
    onStatusRef.current?.(text);
  }, [recording, problem, t]);

  const commit = useCallback(async (hotkey: string) => {
    setSaving(true);
    try {
      await onSaveRef.current(hotkey);
      setProblem("");
    } catch (error) {
      const message = error instanceof Error ? error.message : "";
      setProblem(message || tRef.current("appshots.save_error").replace("{0}", ""));
    } finally {
      setSaving(false);
    }
  }, []);

  useEffect(() => {
    if (!recording) return;
    const pressed = new Set<string>();
    const seen = new Set<string>();
    const characters = new Map<string, string>();
    let closed = false;
    let idle: ReturnType<typeof setTimeout> | undefined;
    let nativeUnavailable = false;
    const nativeSeen = new Set<Family>();

    function preview() {
      const result = chordFromCodes(seen, characters);
      if ("combo" in result) {
        setDraft(result.combo);
        return;
      }
      if (result.problem !== "modifier_only") {
        setDraft("");
        return;
      }
      const tokens = new Set<string>();
      for (const code of seen) {
        const token = codeToModifierToken(code);
        if (token) tokens.add(token);
      }
      setDraft(composeCombo(tokens));
    }

    function close(save: boolean) {
      if (closed) return;
      closed = true;
      if (idle) clearTimeout(idle);
      const snapshot = new Set(seen);
      const typed = new Map(characters);
      setRecording(false);
      setDraft("");
      if (!save) return;
      const result = chordFromCodes(snapshot, typed);
      if ("combo" in result) {
        setProblem("");
        if (result.combo !== valueRef.current) void commit(result.combo);
        return;
      }
      if (result.problem === "modifier_only") {
        setProblem(tRef.current("appshots.shortcut_modifier_only"));
      } else if (result.problem === "unmapped") {
        setProblem(tRef.current("appshots.shortcut_unmapped"));
      }
    }

    // A lone Shift (or Ctrl, then nothing) is not saved, but it also must not
    // end the gesture: the letter often arrives a moment after the modifier
    // comes up. Escape and a second click are what cancel.
    function endIfReleased() {
      if (closed || pressed.size > 0 || seen.size === 0) return;
      const result = chordFromCodes(seen, characters);
      if ("combo" in result) {
        close(true);
        return;
      }
      if (result.problem === "modifier_only") {
        setProblem(tRef.current("appshots.shortcut_modifier_only"));
        return;
      }
      if (result.problem === "unmapped") {
        setProblem(tRef.current("appshots.shortcut_unmapped"));
        close(false);
      }
    }

    function staleKeysOnly() {
      if (pressed.size === 0) return false;
      for (const code of pressed) {
        if (familyOf(code) !== null) return false;
      }
      return true;
    }

    function armRescue() {
      if (idle) clearTimeout(idle);
      if (!staleKeysOnly()) return;
      idle = setTimeout(() => {
        if (!staleKeysOnly()) return;
        for (const code of [...pressed]) pressed.delete(code);
        endIfReleased();
      }, LOST_KEYUP_MS);
    }

    function dropReleased(flags: FlagWord, exceptCode?: string) {
      let changed = false;
      for (const code of [...pressed]) {
        if (code === exceptCode) continue;
        const family = familyOf(code);
        if (family !== null && !flags[family]) {
          pressed.delete(code);
          changed = true;
        }
      }
      return changed;
    }

    function ensureFlagged(flags: FlagWord) {
      for (const family of ["ctrl", "alt", "shift", "meta"] as const) {
        if (!flags[family]) continue;
        if (codesFor(family).some((code) => pressed.has(code) || seen.has(code))) continue;
        const code = canonical(family);
        pressed.add(code);
        seen.add(code);
      }
    }

    function onKeyDown(event: KeyboardEvent) {
      event.preventDefault();
      event.stopPropagation();
      if (event.code === "Escape") {
        close(false);
        return;
      }
      // Repeats prove the key is still down, so the rescue timer stays away.
      if (event.repeat) {
        armRescue();
        return;
      }
      pressed.add(event.code);
      seen.add(event.code);
      // Windows/X11 match the logical letter, not its US position. On a
      // QWERTZ keyboard physical KeyY is Z. macOS matches physical keycodes,
      // so a typed letter must not replace the code there.
      if (!isMacRef.current) {
        const typed = typedCharacter(event);
        if (typed) characters.set(event.code, typed);
      }
      const flags = flagsOf(event);
      ensureFlagged(flags);
      dropReleased(flags, event.code);
      setProblem("");
      preview();
      armRescue();
    }

    function onKeyUp(event: KeyboardEvent) {
      event.preventDefault();
      event.stopPropagation();
      pressed.delete(event.code);
      dropReleased(flagsOf(event), event.code);
      preview();
      endIfReleased();
      if (!closed && pressed.size > 0) armRescue();
    }

    function onMouseMove(event: MouseEvent) {
      if (!dropReleased(flagsOf(event))) return;
      preview();
      endIfReleased();
      if (!closed && pressed.size > 0) armRescue();
    }

    function onBlur() {
      const result = chordFromCodes(seen, characters);
      if ("combo" in result) close(true);
      else close(false);
    }

    finishGesture.current = () => {
      const result = chordFromCodes(seen, characters);
      close("combo" in result);
    };

    function applyNative(tokens: string[]) {
      const down = new Set(tokens);
      const nativeFlags: FlagWord = {
        ctrl: down.has("ctrl"),
        alt: down.has("alt"),
        shift: down.has("shift"),
        meta: ["cmd", "win", "command", "window", "meta", "super"].some((token) => down.has(token)),
      };
      let changed = false;
      for (const family of ["ctrl", "alt", "shift", "meta"] as const) {
        if (nativeFlags[family]) nativeSeen.add(family);
        else if (nativeSeen.has(family)) {
          nativeSeen.delete(family);
          for (const code of codesFor(family)) {
            if (pressed.delete(code)) changed = true;
          }
        }
      }
      if (!changed) return;
      preview();
      endIfReleased();
      if (!closed && pressed.size > 0) armRescue();
    }

    async function tickNative() {
      if (nativeUnavailable || closed) return;
      try {
        const response = await fetch("/api/settings/keybinds/held");
        if (!response.ok || closed) return;
        const data = (await response.json()) as { available?: boolean; tokens?: string[] };
        if (data.available !== true) {
          nativeUnavailable = true;
          return;
        }
        applyNative(Array.isArray(data.tokens) ? data.tokens : []);
      } catch {
        // One failed read is not "this computer cannot tell". The next tick retries.
      }
    }

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("mousemove", onMouseMove, true);
    window.addEventListener("blur", onBlur);
    const nativeTimer = setInterval(() => void tickNative(), 80);
    void tickNative();
    return () => {
      closed = true;
      if (idle) clearTimeout(idle);
      clearInterval(nativeTimer);
      finishGesture.current = () => {};
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
      window.removeEventListener("mousemove", onMouseMove, true);
      window.removeEventListener("blur", onBlur);
    };
  }, [recording, commit]);

  const shown = recording && draft ? draft : value;
  const keys = shown ? formatAppshotHotkey(shown, isMac).split(" + ") : [];
  const busy = disabled || saving;

  return (
    <div
      className={cn("relative", className)}
      data-testid={testId}
      data-keybind-recording={recording ? "true" : undefined}
    >
      <button
        type="button"
        disabled={busy}
        aria-label={`${label}: ${recording ? t("appshots.shortcut_recording") : keys.join(" + ") || t("appshots.shortcut_off")}`}
        aria-pressed={recording}
        title={recording ? undefined : t("appshots.shortcut_change")}
        onClick={() => {
          if (recording) {
            finishGesture.current();
            return;
          }
          setProblem("");
          setDraft("");
          setRecording(true);
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
        ) : recording && !draft ? (
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
      {problem && (
        <span className="sr-only" role="alert">
          {problem}
        </span>
      )}
    </div>
  );
}
