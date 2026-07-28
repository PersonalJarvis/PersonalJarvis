import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Pencil, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  chordToCombo,
  codeToKeyToken,
  composeCombo,
  comboTokens,
  validateCombo,
  type ComboValidation,
  type KeybindAction,
  type KeybindsConfig,
  type KeybindSaveResult,
} from "@/hooks/useHotkey";
import { KeyboardMap } from "@/views/settings/KeyboardMap";
import { detectKeyboardPlatform } from "@/views/settings/keyboardLayout";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

// The keyboard family (Mac vs PC modifier labels) is fixed for the session.
const _KB_PLATFORM = detectKeyboardPlatform();

/** Pretty-print a combo string ("ctrl+right_alt+j" → "Ctrl + AltGr + J"). */
export function formatCombo(combo: string): string {
  const labels: Record<string, string> = {
    ctrl: "Ctrl",
    control: "Ctrl",
    right_ctrl: "Right-Ctrl",
    alt: "Alt",
    left_alt: "Left-Alt",
    // The right Alt key is labelled the way the keycap actually reads — "AltGr"
    // on a PC layout, the Option glyph on a Mac — so the chip in the row and the
    // cap on the on-screen keyboard name the SAME physical key. "Right-Alt"
    // named a key that no keyboard prints.
    right_alt: _KB_PLATFORM === "mac" ? "⌥" : "AltGr",
    altgr: _KB_PLATFORM === "mac" ? "⌥" : "AltGr",
    shift: "Shift",
    win: "Win",
    space: "Space",
    // Navigation / editing cluster + numpad operators (the backend key names).
    up: "↑",
    down: "↓",
    left: "←",
    right: "→",
    insert: "Insert",
    delete: "Delete",
    home: "Home",
    end: "End",
    page_up: "PageUp",
    page_down: "PageDown",
    enter: "Enter",
    tab: "Tab",
    backspace: "Backspace",
    add_key: "Num +",
    subtract_key: "Num −",
    multiply_key: "Num *",
    divide_key: "Num /",
    decimal_key: "Num .",
  };
  // Numpad digits render as "Num 3" rather than "NUMPAD_3".
  const numpad = (p: string) =>
    /^numpad_[0-9]$/.test(p) ? "Num " + p.slice(7) : null;
  // `?? ""` for the same reason as in comboTokens: the combo comes from a
  // backend payload, so an absent action arrives as undefined at runtime no
  // matter what the type says.
  return (combo ?? "")
    .split("+")
    .map((p) => labels[p] ?? numpad(p) ?? p.toUpperCase())
    .join(" + ");
}

/**
 * Each action's i18n label key — used to mark a key "already used by <action>"
 * and to name the other side of a collision the way the UI labels it.
 * Mirrors KEYBIND_ACTIONS in jarvis/core/config_writer.py.
 */
export const ACTION_LABEL_KEY: Record<KeybindAction, string> = {
  call: "settings_view.keybinds.call_label",
  hangup: "settings_view.keybinds.hangup_label",
  dictate: "settings_view.keybinds.dictate_label",
  dictate_toggle: "settings_view.keybinds.dictate_toggle_label",
};

/** The combo rendered as keycap chips ("Ctrl + F5" → [Ctrl] + [F5]). */
export function ComboChips({ combo }: { combo: string }) {
  const parts = formatCombo(combo).split(" + ");
  return (
    <>
      {parts.map((p, i) => (
        <Fragment key={`${p}-${i}`}>
          {i > 0 && <span className="text-muted-foreground/50">+</span>}
          <kbd className="rounded border border-border bg-muted/70 px-1.5 py-0.5 font-mono text-[11px] leading-none text-foreground shadow-[inset_0_-1px_0_rgba(0,0,0,0.35)]">
            {p}
          </kbd>
        </Fragment>
      ))}
    </>
  );
}

/** The localized live-validation message for the combo being built, or null. */
export function validationText(
  v: ComboValidation,
  t: (key: string) => string,
): string | null {
  if (v.status !== "error" && v.status !== "warning") return null;
  if (v.reason === "collision") {
    // The conflict carries the ACTION ID (unique); the message names it the way
    // the UI labels that row. An id the frontend does not know yet (a newer
    // backend) falls back to the raw id instead of rendering an empty name.
    const labelKey = ACTION_LABEL_KEY[v.conflict.action as KeybindAction];
    return t("settings_view.keybinds.validation.collision")
      .replace("{action}", labelKey ? t(labelKey) : v.conflict.action)
      .replace("{combo}", formatCombo(v.conflict.combo));
  }
  return t(`settings_view.keybinds.validation.${v.reason}`);
}

export interface KeybindRowProps {
  action: KeybindAction;
  label: string;
  config: KeybindsConfig | null;
  loading: boolean;
  onSave: (a: KeybindAction, h: string) => Promise<KeybindSaveResult>;
  /** One explanatory line under the label (voice variant only). */
  hint?: string;
  /**
   * "settings" — the compact row inside Settings → Voice Keybinds (unchanged).
   * "voice" — the wider row of the Shortcuts tab: label + hint on the left, the
   * combo chips plus a pencil (record) and a plus (pick on the keyboard) on the
   * right.
   */
  variant?: "settings" | "voice";
  /** Curated combos offered as one-click chips (voice variant only). */
  suggestions?: string[];
  /**
   * Called after a combo was accepted by the backend. Lets the owner apply a
   * side effect that belongs to the same user intent (the Push-to-Talk row
   * pins the dictation mode to "hold"). Failures are the owner's to report.
   */
  onSaved?: (combo: string) => void | Promise<void>;
}

/**
 * One editable keybind: shows the current combo, records a new one (physical
 * chord or click-to-assign on the on-screen keyboard), validates it live and
 * saves it. The backend validator stays the authority — a rejected combo
 * surfaces its reason as a toast.
 */
export function KeybindRow({
  action,
  label,
  config,
  loading,
  onSave,
  hint,
  variant = "settings",
  suggestions,
  onSaved,
}: KeybindRowProps) {
  const t = useT();
  const pushToast = useEventStore((s) => s.pushToast);
  // Every read defaults, at BOTH levels. A backend that does not know this
  // action yet returns no entry (`keybinds.dictate` undefined) and one that
  // errors or predates the route returns no map at all (`keybinds` undefined) —
  // the first fed comboTokens an undefined combo, the second dereferenced
  // undefined directly. Either one used to take the WHOLE Settings view down
  // with it (reported as "Cannot read properties of undefined (reading
  // 'split')"), because a frontend build and the running backend are updated
  // separately and a version skew is the normal state, not an edge case.
  const current = config?.keybinds?.[action] ?? "";
  const def = config?.defaults?.[action];

  const [combo, setCombo] = useState("");
  const [capturing, setCapturing] = useState(false);
  const [saving, setSaving] = useState(false);
  const [saved, setSaved] = useState(false);
  // Physical codes currently held — mirrored from the recorder so the on-screen
  // keyboard lights up live as the user presses keys.
  const [pressedCodes, setPressedCodes] = useState<Set<string>>(new Set());

  useEffect(() => {
    if (config) setCombo(config.keybinds?.[action] ?? "");
  }, [config, action]);

  // Tokens already bound to the OTHER actions → marked "used" on the keyboard so
  // the user can pick a free key (their keys "can't be free", as reported).
  const boundTokens = useMemo(() => {
    const out: Record<string, string> = {};
    if (!config?.keybinds) return out;
    for (const [act, c] of Object.entries(config.keybinds)) {
      if (act === action) continue;
      const labelKey = ACTION_LABEL_KEY[act as KeybindAction];
      const lbl = labelKey ? t(labelKey) : act;
      for (const tok of comboTokens(c ?? "")) out[tok] = lbl;
    }
    return out;
  }, [config, action, t]);

  // The OTHER actions' combos keyed by their ACTION ID. Keying by the translated
  // label collapsed two rows that happen to share a label into one entry, so one
  // of them stopped being checked for collisions; validationText translates the
  // id back for the message.
  const otherCombos = useMemo(() => {
    const out: Record<string, string> = {};
    if (!config?.keybinds) return out;
    for (const [act, c] of Object.entries(config.keybinds)) {
      if (act !== action) out[act] = c ?? "";
    }
    return out;
  }, [config, action]);

  // Live validation — every backend rule surfaces HERE, while the user builds
  // the combo, instead of as a cryptic post-Save error toast (the reported
  // "I picked Arrow Up and got a weird error message" experience).
  const validation = useMemo(
    () => validateCombo(combo, otherCombos),
    [combo, otherCombos],
  );
  const invalid = validation.status === "error";
  const validationMsg = validationText(validation, t);

  // Click-to-assign: toggle a key in/out of the combo without a physical press.
  // Functional update — toggles dispatched before the next render must each
  // build on the previous one, not on a stale closure combo (last-click-wins).
  function onToggleToken(token: string) {
    setCombo((prev) => {
      const tokens = comboTokens(prev);
      if (tokens.has(token)) tokens.delete(token);
      else tokens.add(token);
      return composeCombo(tokens);
    });
    setSaved(false);
  }

  // While capturing, listen on `window` (capture phase) instead of on a single
  // button. Three reasons:
  //   1. Focus: clicking the "Record" button puts focus on THAT button, so a
  //      key listener living only on the display field never fired — the combo
  //      was silently dropped. A window listener catches the chord no matter
  //      which control has focus.
  //   2. Chord: a held set accumulates every non-modifier key, so several keys
  //      pressed together (WASD, F7+F8, I+Y) — which the global-hotkeys backend
  //      registers natively (the Call default is f3+f4) — all land in the combo
  //      instead of only the first one.
  //   3. Commit on FULL release, not on the first keyup. We track every
  //      physically-held key (incl. modifiers, by `event.code`) and only commit
  //      once the user has let go of everything. Committing on the first keyup
  //      ended the recording the instant any one key lifted, so a human pressing
  //      a chord (whose key releases are never perfectly simultaneous, and whose
  //      presses roll in one after another) only ever got the first key — the
  //      reported "press several, only one is recorded" bug. Now the rule is the
  //      natural one: "hold your keys, then let go".
  // preventDefault on both edges also stops the keystrokes from leaking into
  // the rest of the app while recording (the "everything lags" symptom).
  // What Escape restores: the SAVED value (the server truth), falling back to
  // the combo as of recording start when nothing is saved yet. Kept in a ref so
  // the capture effect (deps: [capturing]) always reads the live value — a
  // mid-recording save refetches the config, and restoring a stale snapshot
  // would silently diverge the field from what the server actually has.
  const currentRef = useRef(current);
  currentRef.current = current;
  const comboBeforeCapture = useRef(combo);

  useEffect(() => {
    if (!capturing) return;
    comboBeforeCapture.current = combo; // fallback when nothing is saved yet
    setPressedCodes(new Set()); // fresh highlight state for this gesture
    const held = new Set<string>(); // non-modifier key tokens seen this gesture
    const pressed = new Set<string>(); // physical event.codes currently down
    let pending: string | null = null; // fullest chord captured so far
    let idle: ReturnType<typeof setTimeout> | undefined; // fallback-commit timer

    function commit() {
      if (pending) {
        setCombo(pending);
        setSaved(false);
        setCapturing(false);
      }
    }

    function onKeyDown(e: KeyboardEvent) {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") {
        if (idle) clearTimeout(idle); // cancel a pending fallback commit
        // Undo the live preview: back to the saved value (server truth).
        setCombo(currentRef.current || comboBeforeCapture.current);
        setCapturing(false);
        return;
      }
      pressed.add(e.code);
      setPressedCodes(new Set(pressed)); // live keyboard highlight
      const tok = codeToKeyToken(e.code);
      if (tok) held.add(tok);
      const next = chordToCombo(e, held);
      if (next) {
        pending = next;
        setCombo(next); // live preview as the chord grows
        setSaved(false);
      }
      // Fallback: some keys — function keys especially, and any key whose
      // release lands while the window is losing focus — do NOT reliably
      // deliver a keyup. Without this the "commit on full release" path below
      // would hang forever ("F5+F6 never records"). Re-arm an idle timer on
      // every keydown; once the user stops pressing for ~900 ms, commit the
      // chord we have even if a keyup never came.
      if (idle) clearTimeout(idle);
      idle = setTimeout(commit, 900);
    }

    function onKeyUp(e: KeyboardEvent) {
      e.preventDefault();
      e.stopPropagation();
      pressed.delete(e.code);
      setPressedCodes(new Set(pressed)); // live keyboard highlight
      // Fast path: commit the instant EVERY key is released. `pending` holds
      // the fullest chord seen during the gesture, so the release order never
      // matters and early-lifted keys are not lost.
      if (pressed.size === 0 && pending) {
        if (idle) clearTimeout(idle);
        commit();
      }
    }

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    return () => {
      if (idle) clearTimeout(idle);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
    };
  }, [capturing]);

  // Clear the live highlight on the falling edge of capturing, so reopening the
  // picker never flashes the previous chord's keys before the first new press.
  useEffect(() => {
    if (!capturing) setPressedCodes(new Set());
  }, [capturing]);

  async function onSaveClick() {
    const trimmed = combo.trim().toLowerCase();
    if (!trimmed) return;
    setSaving(true);
    setSaved(false);
    try {
      const res = await onSave(action, trimmed);
      setSaved(res.restart_required);
      // The save concludes the recording session. Leaving the recorder open
      // kept a stale pre-recording snapshot around that a later Esc would
      // "restore" — silently diverging the field from the saved value.
      setCapturing(false);
      pushToast("success", t("settings_view.keybinds.saved"));
      await onSaved?.(trimmed);
    } catch (e) {
      // Backend rejected the combo (unsafe / collision) — show its reason.
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  // Immediate, one-click unbind — no staging step, mirroring the "Reset to
  // default" link's immediacy. Bypasses onSaveClick's trimmed-empty guard,
  // which exists to stop an in-progress recording from saving nothing.
  async function onClearClick() {
    setSaving(true);
    try {
      const res = await onSave(action, "");
      setCombo("");
      setCapturing(false);
      setSaved(res.restart_required);
      pushToast("success", t("settings_view.keybinds.cleared"));
    } catch (e) {
      pushToast("error", (e as Error).message);
    } finally {
      setSaving(false);
    }
  }

  const dirty = !!config && combo.trim().toLowerCase() !== current;
  const showReset = !!def && combo.trim().toLowerCase() !== def;

  // Curated combos that are still free — a chip proposing a combo another
  // action already owns would only manufacture a rejected save.
  const freeSuggestions = useMemo(() => {
    if (!suggestions?.length) return [];
    const taken = new Set(
      Object.values(otherCombos)
        .map((c) => c.trim().toLowerCase())
        .filter(Boolean),
    );
    return suggestions
      .map((s) => s.trim().toLowerCase())
      .filter((s) => s && !taken.has(s) && s !== combo.trim().toLowerCase())
      .slice(0, 4);
  }, [suggestions, otherCombos, combo]);

  const comboField = (
    <button
      type="button"
      data-testid={`combo-field-${action}`}
      onClick={() => setCapturing((c) => !c)}
      disabled={loading}
      className={`flex min-h-[34px] flex-wrap items-center gap-1 rounded-md border px-3 py-1.5 text-left text-sm transition-colors focus:outline-none focus:ring-1 focus:ring-primary disabled:opacity-50 ${
        variant === "voice" ? "" : "flex-1"
      } ${capturing ? "border-primary bg-primary/10" : "border-input bg-background"}`}
    >
      {capturing && (
        <span className="relative mr-1 flex h-2 w-2 shrink-0">
          <span className="absolute inline-flex h-full w-full animate-ping rounded-full bg-primary opacity-75" />
          <span className="relative inline-flex h-2 w-2 rounded-full bg-primary" />
        </span>
      )}
      {combo ? (
        <ComboChips combo={combo} />
      ) : (
        <span className="text-muted-foreground">
          {capturing
            ? t("settings_view.keybinds.recording")
            : loading
              ? "—"
              : t("settings_view.keybinds.unbound")}
        </span>
      )}
    </button>
  );

  // ONE stable status line: the validation message when there is one, the
  // recording hint otherwise. Two separately appearing lines made the keyboard
  // below jump vertically on every combo click.
  const statusLine = (capturing || validationMsg) && (
    <p
      data-testid={validationMsg ? `keybind-validation-${action}` : undefined}
      className={`mt-2 text-[11px] ${
        validationMsg
          ? validation.status === "error"
            ? "text-destructive"
            : "text-amber-400"
          : "text-muted-foreground"
      }`}
    >
      {validationMsg ?? t("settings_view.keybinds.recording_hint")}
    </p>
  );

  const keyboard = capturing && (
    <KeyboardMap
      pressedCodes={pressedCodes}
      selectedTokens={comboTokens(combo)}
      boundTokens={boundTokens}
      platform={_KB_PLATFORM}
      onToggleToken={onToggleToken}
    />
  );

  const restartHint = saved && (
    <p className="mt-2 text-[11px] text-muted-foreground">
      {t("settings_view.keybinds.restart_required")}
    </p>
  );

  if (variant === "voice") {
    return (
      <div className="rounded-lg border border-border bg-card/60 p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0 flex-1">
            <p className="text-sm font-semibold text-foreground">{label}</p>
            {hint && (
              <p className="mt-0.5 text-xs text-muted-foreground">{hint}</p>
            )}
          </div>
          <div className="flex shrink-0 items-center gap-2">
            {comboField}
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid={`record-keybind-${action}`}
              aria-label={
                capturing
                  ? t("settings_view.keybinds.stop")
                  : t("settings_view.keybinds.record")
              }
              title={
                capturing
                  ? t("settings_view.keybinds.stop")
                  : t("settings_view.keybinds.record")
              }
              onClick={() => setCapturing((c) => !c)}
              disabled={loading}
            >
              <Pencil className="h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="outline"
              data-testid={`add-keybind-${action}`}
              aria-label={t("settings_view.keybinds.keyboard.hint")}
              title={t("settings_view.keybinds.keyboard.hint")}
              onClick={() => setCapturing(true)}
              disabled={loading}
            >
              <Plus className="h-3.5 w-3.5" />
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              data-testid={`clear-keybind-${action}`}
              aria-label={t("settings_view.keybinds.clear")}
              title={t("settings_view.keybinds.clear")}
              onClick={onClearClick}
              disabled={saving || loading || !current}
            >
              <X className="h-3.5 w-3.5" />
            </Button>
          </div>
        </div>

        {freeSuggestions.length > 0 && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <span className="text-[11px] text-muted-foreground">
              {t("voice.shortcuts.suggestions_title")}
            </span>
            {freeSuggestions.map((s) => (
              <button
                key={s}
                type="button"
                data-testid={`suggestion-${action}-${s}`}
                onClick={() => {
                  setCombo(s);
                  setSaved(false);
                }}
                className="rounded-full border border-border bg-background px-2 py-0.5 font-mono text-[11px] text-muted-foreground transition-colors hover:border-primary/60 hover:text-foreground"
              >
                {formatCombo(s)}
              </button>
            ))}
          </div>
        )}

        {statusLine}

        {/* The commit controls only appear once there is something to commit —
            a resting row is just the shortcut, not a form. */}
        {(dirty || showReset) && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <Button
              size="sm"
              onClick={onSaveClick}
              disabled={saving || loading || !dirty || invalid}
            >
              {saving
                ? t("settings_view.saving")
                : t("settings_view.keybinds.save")}
            </Button>
            {showReset && (
              <button
                type="button"
                className="text-[11px] text-muted-foreground underline hover:text-foreground"
                onClick={() => {
                  if (def) {
                    setCombo(def);
                    setSaved(false);
                  }
                }}
              >
                {t("settings_view.keybinds.reset")}
              </button>
            )}
          </div>
        )}

        {keyboard}
        {restartHint}
      </div>
    );
  }

  return (
    <div className="rounded-md border border-border/60 bg-background/40 p-3">
      <div className="flex items-center justify-between gap-2">
        <span className="text-xs font-semibold text-foreground">{label}</span>
        {showReset && (
          <button
            type="button"
            className="text-[11px] text-muted-foreground underline hover:text-foreground"
            onClick={() => {
              if (def) {
                setCombo(def);
                setSaved(false);
              }
            }}
          >
            {t("settings_view.keybinds.reset")}
          </button>
        )}
      </div>
      <div className="mt-2 flex items-center gap-2">
        {comboField}
        <Button
          size="sm"
          variant="outline"
          onClick={() => setCapturing((c) => !c)}
          disabled={loading}
        >
          {capturing
            ? t("settings_view.keybinds.stop")
            : t("settings_view.keybinds.record")}
        </Button>
        <Button
          size="sm"
          onClick={onSaveClick}
          disabled={saving || loading || !dirty || invalid}
        >
          {saving ? t("settings_view.saving") : t("settings_view.keybinds.save")}
        </Button>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          data-testid={`clear-keybind-${action}`}
          aria-label={t("settings_view.keybinds.clear")}
          title={t("settings_view.keybinds.clear")}
          onClick={onClearClick}
          disabled={saving || loading || !current}
        >
          <X className="h-3.5 w-3.5" />
        </Button>
      </div>
      {statusLine}
      {keyboard}
      {restartHint}
    </div>
  );
}
