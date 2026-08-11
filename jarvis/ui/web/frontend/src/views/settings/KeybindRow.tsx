import { Fragment, useEffect, useMemo, useRef, useState } from "react";
import { Pencil, Plus, X } from "lucide-react";

import { Button } from "@/components/ui/button";
import {
  chordToCombo,
  codeToKeyToken,
  codeToModifierToken,
  composeCombo,
  comboTokens,
  validateCombo,
  type ComboCaution,
  type ComboValidation,
  type KeybindAction,
  type KeybindsConfig,
  type KeybindSaveResult,
} from "@/hooks/useHotkey";
import { KeyboardMap } from "@/views/settings/KeyboardMap";
import {
  detectKeyboardPlatform,
  mouseButtonCode,
  mouseButtonToToken,
} from "@/views/settings/keyboardLayout";
import { useEventStore } from "@/store/events";
import { useT } from "@/i18n";

// The keyboard family (Mac vs PC modifier labels) is fixed for the session.
const _KB_PLATFORM = detectKeyboardPlatform();

/**
 * How long click-to-assign waits after the last click before saving.
 *
 * Long enough to click "Ctrl", "Shift", "F5" at a human pace without saving
 * the first two on their own; short enough that the save still feels part of
 * the same gesture. A physical chord does not use this — letting go of the
 * keys is already an unambiguous "I am done".
 */
const _CLICK_SETTLE_MS = 1200;

/**
 * How long the recorder waits after the last key event before assuming a keyup
 * was swallowed and committing the chord anyway.
 *
 * This is a RESCUE path, never the normal one. It fires only while everything
 * still marked as held is an ordinary key — one that auto-repeats, and so
 * re-arms this timer many times a second for as long as it is genuinely down.
 * A held MODIFIER suppresses it outright and the recorder then waits
 * indefinitely: holding Ctrl+Alt while deciding on the third key is how a human
 * actually builds a chord, and committing "ctrl+alt" out from under them (the
 * reported "it saves before I can think") is precisely what this guard exists
 * to prevent. The gesture ends when the user lets go of everything — or when
 * the window loses focus, which ends it whether the keys came up or not.
 */
const _LOST_KEYUP_MS = 900;

/** Pretty-print a combo string ("ctrl+right_alt+j" → "Ctrl + AltGr + J"). */
export function formatCombo(combo: string): string {
  const labels: Record<string, string> = {
    // Same rule as ``win`` below, applied to the other two Mac modifiers: a
    // Mac keycap prints ⌃ and ⌥, never "Ctrl" or "Alt". Leaving these as PC
    // words put a chip on screen that named a key the machine does not have,
    // right next to the on-screen keyboard drawing that very key as ⌥ — and
    // the mismatch is what made a saved Mac shortcut read as a Windows one.
    ctrl: _KB_PLATFORM === "mac" ? "⌃" : "Ctrl",
    control: _KB_PLATFORM === "mac" ? "⌃" : "Ctrl",
    right_ctrl: _KB_PLATFORM === "mac" ? "⌃" : "Right-Ctrl",
    alt: _KB_PLATFORM === "mac" ? "⌥" : "Alt",
    left_alt: _KB_PLATFORM === "mac" ? "⌥" : "Left-Alt",
    // The right Alt key is labelled the way the keycap actually reads — "AltGr"
    // on a PC layout, the Option glyph on a Mac — so the chip in the row and the
    // cap on the on-screen keyboard name the SAME physical key. "Right-Alt"
    // named a key that no keyboard prints.
    right_alt: _KB_PLATFORM === "mac" ? "⌥" : "AltGr",
    altgr: _KB_PLATFORM === "mac" ? "⌥" : "AltGr",
    shift: "Shift",
    // Same physical key, two vocabularies: it is Command on a Mac keyboard and
    // the Windows key on a PC one. Rendering "Win" on a Mac named a cap that
    // machine does not have, and a Command combo fell through to a raw "CMD".
    win: _KB_PLATFORM === "mac" ? "⌘" : "Win",
    window: _KB_PLATFORM === "mac" ? "⌘" : "Win",
    super: _KB_PLATFORM === "mac" ? "⌘" : "Win",
    meta: _KB_PLATFORM === "mac" ? "⌘" : "Win",
    cmd: "⌘",
    command: "⌘",
    space: "Space",
    // Mouse buttons, named the way the hardware and the browser do (X1/X2 are
    // Back/Forward in every application that uses them).
    mouse_middle: "Middle Click",
    mouse_x1: "Mouse Back",
    mouse_x2: "Mouse Fwd",
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
  paste_last: "settings_view.keybinds.paste_last_label",
};

/**
 * The localized sentence for each non-blocking caution.
 *
 * ``mouse_button`` deliberately shares the OS-shortcut sentence: a bound mouse
 * button is not swallowed either — whatever you are pointing at still gets its
 * click — which is exactly what that sentence promises. It stays a separate
 * reason so a dedicated string can be pointed at it later without touching the
 * rule that produces it.
 */
const CAUTION_KEY: Record<ComboCaution, string> = {
  modifier_only: "settings_view.keybinds.caution_modifier_only",
  os_shortcut: "settings_view.keybinds.caution_os_shortcut",
  mouse_button: "settings_view.keybinds.caution_os_shortcut",
  overlap: "settings_view.keybinds.caution_overlap",
  solo_typing_key: "settings_view.keybinds.validation.solo_typing_key",
  solo_nav: "settings_view.keybinds.validation.solo_nav",
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

/**
 * The localized live message for the combo being built, or null.
 *
 * A collision is the one BLOCKING message (the backend route rejects it with a
 * 400). Everything else is a caution: the combo is saveable, the sentence only
 * says what else will happen when it fires. Several cautions can apply at once
 * — they are joined into ONE line, because a second line makes the on-screen
 * keyboard below jump on every click.
 */
export function validationText(
  v: ComboValidation,
  t: (key: string) => string,
): string | null {
  if (v.status === "error" && v.reason === "collision" && v.conflict) {
    // The conflict carries the ACTION ID (unique); the message names it the way
    // the UI labels that row. An id the frontend does not know yet (a newer
    // backend) falls back to the raw id instead of rendering an empty name.
    const labelKey = ACTION_LABEL_KEY[v.conflict.action as KeybindAction];
    return t("settings_view.keybinds.validation.collision")
      .replace("{action}", labelKey ? t(labelKey) : v.conflict.action)
      .replace("{combo}", formatCombo(v.conflict.combo));
  }
  const cautions = v.cautions ?? [];
  if (cautions.length === 0) return null;
  // De-duplicated by SENTENCE, not by reason: two reasons deliberately share
  // one string, and printing it twice reads like a stutter.
  const sentences = [...new Set(cautions.map((c) => t(CAUTION_KEY[c])))];
  return sentences.join(" ");
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
  // "I picked Arrow Up and got a weird error message" experience). With the
  // Save button gone this line IS the feedback: a collision is what stops the
  // auto-save, so it has to explain itself on screen and not merely disable
  // something.
  const validation = useMemo(
    () => validateCombo(combo, otherCombos),
    [combo, otherCombos],
  );
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
    // Clicks arrive one at a time while a combo is being built, so this one is
    // probably not the last — wait for the clicking to stop before saving.
    editVia.current = "click";
    setEdits((n) => n + 1);
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

  // ------------------------------------------------------------------
  // Auto-save
  // ------------------------------------------------------------------
  // The combo IS the setting. A separate Save button could only add a step the
  // user has to remember — and forgetting it looks exactly like the shortcut
  // being broken, which is the one failure this surface must never produce.
  //
  // ``edits`` counts USER edits (a bumped counter, not the combo string, so
  // toggling a key off and back on still re-arms the timer). ``editVia`` says
  // how the edit arrived, which decides the delay: a physical chord and a
  // suggestion chip are complete gestures and save at once, while a click on
  // the on-screen keyboard is probably one of several and waits for the
  // clicking to settle — otherwise the half-built combo would be saved, and
  // the user's second click would be a second save.
  //
  // Nothing here needs to know about Esc, the initial config load, or a
  // refetch: ``persist`` refuses anything equal to what the server already
  // has, which covers all three by construction.
  const [edits, setEdits] = useState(0);
  const editVia = useRef<"chord" | "click">("chord");
  const savingRef = useRef(false);

  // Held in a ref so the timer effect below depends on the EDIT, not on the
  // identity of this function (which changes on every render).
  const persistRef = useRef<(next: string) => Promise<void>>(async () => {});
  persistRef.current = async (next: string) => {
    const trimmed = next.trim().toLowerCase();
    // Empty is not an auto-save: clearing a shortcut is what the X button is
    // for, and a combo being rebuilt from nothing passes through empty.
    if (!trimmed || trimmed === current) return;
    // A collision is the one thing the backend refuses (400). The inline
    // message already says so; firing a doomed request would only add a toast.
    if (validateCombo(trimmed, otherCombos).status === "error") return;
    if (savingRef.current) return;
    savingRef.current = true;
    setSaving(true);
    try {
      const res = await onSave(action, trimmed);
      setSaved(res.restart_required);
      // The gesture is over — leaving the recorder open would keep a stale
      // pre-recording snapshot that a later Esc would "restore".
      setCapturing(false);
      pushToast("success", t("settings_view.keybinds.saved"));
      await onSaved?.(trimmed);
    } catch (e) {
      // Rejected (collision the live check could not see, an unusable mouse
      // button on this host, …). Show the backend's own reason.
      pushToast("error", (e as Error).message);
    } finally {
      savingRef.current = false;
      setSaving(false);
    }
  };

  useEffect(() => {
    if (edits === 0) return;
    const delay = editVia.current === "click" ? _CLICK_SETTLE_MS : 0;
    const timer = setTimeout(() => {
      void persistRef.current(combo);
    }, delay);
    return () => clearTimeout(timer);
  }, [edits, combo]);

  useEffect(() => {
    if (!capturing) return;
    comboBeforeCapture.current = combo; // fallback when nothing is saved yet
    setPressedCodes(new Set()); // fresh highlight state for this gesture
    const held = new Set<string>(); // non-modifier key tokens seen this gesture
    const pressed = new Set<string>(); // physical event.codes currently down
    let pending: string | null = null; // fullest chord captured so far
    let idle: ReturnType<typeof setTimeout> | undefined; // rescue-commit timer

    function commit() {
      if (pending) {
        setCombo(pending);
        setSaved(false);
        setCapturing(false);
        // Letting go of the keys ends the gesture, so this saves immediately —
        // no settle delay, nothing left to press.
        editVia.current = "chord";
        setEdits((n) => n + 1);
      }
    }

    /**
     * Whether everything we still believe is down could plausibly be a
     * SWALLOWED keyup rather than a key the user is deliberately holding.
     *
     * Modifiers are excluded because they are the one thing a person holds
     * while thinking, and because they do not auto-repeat everywhere — a timer
     * cannot tell "still holding Ctrl" from "Ctrl's keyup went missing", so it
     * must not guess. Mouse buttons are excluded because their release is
     * delivered reliably; there is nothing to rescue.
     */
    function staleKeysOnly() {
      if (pressed.size === 0) return false;
      for (const code of pressed) {
        if (codeToModifierToken(code) !== null) return false;
        if (code.startsWith("MouseButton")) return false;
      }
      return true;
    }

    // Rescue timer for keys that never deliver a keyup — function keys
    // especially, and anything released while the window is losing focus.
    // Without it the "commit on full release" path below would hang forever
    // ("F5+F6 never records"). It re-arms on every key event INCLUDING
    // auto-repeat, so a genuinely held key keeps pushing it out of reach.
    function armRescue() {
      if (idle) clearTimeout(idle);
      idle = setTimeout(() => {
        if (!staleKeysOnly()) return; // really still held → keep waiting
        commit();
      }, _LOST_KEYUP_MS);
    }

    function onKeyDown(e: KeyboardEvent) {
      e.preventDefault();
      e.stopPropagation();
      if (e.key === "Escape") {
        if (idle) clearTimeout(idle); // cancel a pending rescue commit
        // Undo the live preview: back to the saved value (server truth).
        setCombo(currentRef.current || comboBeforeCapture.current);
        setCapturing(false);
        return;
      }
      // Auto-repeat carries no new information — it is the browser saying the
      // key is STILL physically down. That makes it exactly the proof the
      // rescue timer needs, and nothing else: re-render the whole row dozens of
      // times a second for an unchanged chord and the app crawls while the
      // recorder is open.
      if (e.repeat) {
        armRescue();
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
      armRescue();
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
        return;
      }
      armRescue();
    }

    /**
     * The window losing focus ends the gesture whether the keys came up or not:
     * the keyups are delivered to whatever took focus, so waiting for them
     * would leave the recorder armed forever. This is the honest end of the
     * one case a timer must not resolve — a held modifier whose release we will
     * never see (pressing the Windows key opens Start and takes focus with it).
     */
    function onWindowBlur() {
      if (idle) clearTimeout(idle);
      commit();
    }

    /**
     * Drop modifiers from the held set that a mouse event proves are already up.
     *
     * Every mouse event carries the TRUE modifier state, so this is a free,
     * continuous repair for a swallowed modifier keyup — and a held modifier is
     * exactly what suppresses the rescue timer, so without it the recorder
     * could sit armed with a phantom Ctrl and no way out but Esc.
     */
    function syncModifiers(e: MouseEvent) {
      let changed = false;
      for (const code of [...pressed]) {
        const stillDown = code.startsWith("Control")
          ? e.ctrlKey
          : code.startsWith("Alt")
            ? e.altKey
            : code.startsWith("Shift")
              ? e.shiftKey
              : code.startsWith("Meta")
                ? e.metaKey
                : true; // not a modifier — a mouse event says nothing about it
        if (!stillDown) {
          pressed.delete(code);
          changed = true;
        }
      }
      if (!changed) return;
      setPressedCodes(new Set(pressed));
      if (pressed.size === 0 && pending) {
        if (idle) clearTimeout(idle);
        commit();
      }
    }

    // A MouseEvent carries the same modifier flags a KeyboardEvent does, but no
    // `code` — the modifier reader only consults `code` to tell AltGr from a
    // plain Alt, which a mouse press cannot be.
    function asKeyEventLike(e: MouseEvent) {
      return {
        code: "",
        ctrlKey: e.ctrlKey,
        altKey: e.altKey,
        shiftKey: e.shiftKey,
        metaKey: e.metaKey,
        getModifierState: (k: string) => e.getModifierState(k),
      };
    }

    // Mouse buttons join the SAME held-set the keys use, so Ctrl + side button
    // records as one chord and the commit-on-full-release rule needs no special
    // case. The primary and secondary buttons are never captured: the recorder's
    // own controls (Save, the on-screen keys) have to stay clickable while it
    // is armed, and the OS "swap buttons" setting makes those two unreliable to
    // bind anyway.
    function onMouseDown(e: MouseEvent) {
      const tok = mouseButtonToToken(e.button);
      if (tok === null) return;
      e.preventDefault();
      e.stopPropagation();
      const code = mouseButtonCode(e.button);
      pressed.add(code);
      setPressedCodes(new Set(pressed));
      held.add(tok);
      const next = chordToCombo(asKeyEventLike(e), held);
      if (next) {
        pending = next;
        setCombo(next);
        setSaved(false);
      }
      armRescue();
    }

    function onMouseUp(e: MouseEvent) {
      const tok = mouseButtonToToken(e.button);
      if (tok === null) return;
      e.preventDefault();
      e.stopPropagation();
      pressed.delete(mouseButtonCode(e.button));
      setPressedCodes(new Set(pressed));
      if (pressed.size === 0 && pending) {
        if (idle) clearTimeout(idle);
        commit();
        return;
      }
      armRescue();
    }

    // The side buttons are Back/Forward and the middle button starts autoscroll;
    // suppressing the follow-up event keeps a recording gesture from navigating
    // the app out from under itself.
    function onAuxClick(e: MouseEvent) {
      if (mouseButtonToToken(e.button) === null) return;
      e.preventDefault();
      e.stopPropagation();
    }

    window.addEventListener("keydown", onKeyDown, true);
    window.addEventListener("keyup", onKeyUp, true);
    window.addEventListener("mousedown", onMouseDown, true);
    window.addEventListener("mouseup", onMouseUp, true);
    window.addEventListener("auxclick", onAuxClick, true);
    window.addEventListener("mousemove", syncModifiers, true);
    window.addEventListener("blur", onWindowBlur);
    return () => {
      if (idle) clearTimeout(idle);
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
      window.removeEventListener("mousedown", onMouseDown, true);
      window.removeEventListener("mouseup", onMouseUp, true);
      window.removeEventListener("auxclick", onAuxClick, true);
      window.removeEventListener("mousemove", syncModifiers, true);
      window.removeEventListener("blur", onWindowBlur);
    };
  }, [capturing]);

  // Clear the live highlight on the falling edge of capturing, so reopening the
  // picker never flashes the previous chord's keys before the first new press.
  useEffect(() => {
    if (!capturing) setPressedCodes(new Set());
  }, [capturing]);

  /** Set the combo from a one-shot control (chip / reset) and save it now. */
  function assign(next: string) {
    setCombo(next);
    setSaved(false);
    editVia.current = "chord"; // a single deliberate click — nothing follows it
    setEdits((n) => n + 1);
  }

  // Immediate, one-click unbind. Its own path rather than an auto-save,
  // because auto-save deliberately refuses an EMPTY combo: one being rebuilt
  // from scratch passes through empty, and that must never unbind the row.
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
        <span className={capturing ? "text-foreground" : "text-muted-foreground"}>
          {capturing
            ? // Say what to DO, not that a mode is on: the bare "recording"
              // state read as a broken field ("it just went empty").
              t("settings_view.keybinds.record_prompt")
            : loading
              ? "—"
              : t("settings_view.keybinds.unbound")}
        </span>
      )}
    </button>
  );

  // ONE stable status line: the blocking message when there is one, otherwise
  // the cautions, otherwise the recording hint. Two separately appearing lines
  // made the keyboard below jump vertically on every combo click.
  //
  // While keys are physically DOWN the chord is still being built, so the line
  // says so and nothing else. A caution about the half-built state ("a
  // modifier-only shortcut fires on any superset") judges something the user
  // has not decided yet — it belongs to the combo they let go of, not to the
  // one they are still assembling. A blocking collision keeps speaking, since
  // that is the one thing that will stop the save.
  const holding = capturing && pressedCodes.size > 0;
  const isError = !!validationMsg && validation.status === "error";
  const showValidation = !!validationMsg && (isError || !holding);
  const statusText = showValidation
    ? validationMsg
    : holding
      ? t("settings_view.keybinds.record_prompt_holding")
      : capturing
        ? t("settings_view.keybinds.record_prompt_hint")
        : null;
  const statusLine = statusText && (
    <p
      data-testid={showValidation ? `keybind-validation-${action}` : undefined}
      className={`mt-2 text-[11px] ${
        showValidation
          ? isError
            ? "text-destructive"
            : "text-amber-400"
          : "text-muted-foreground"
      }`}
    >
      {statusText}
    </p>
  );

  const keyboard = capturing && (
    <KeyboardMap
      pressedCodes={pressedCodes}
      selectedTokens={comboTokens(combo)}
      boundTokens={boundTokens}
      platform={_KB_PLATFORM}
      onToggleToken={onToggleToken}
      // Absent until the route serves the probe — see KeybindsConfig.
      mouseSupported={config?.mouse_buttons?.supported ?? true}
      mouseReason={config?.mouse_buttons?.reason}
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
                onClick={() => assign(s)}
                className="rounded-full border border-border bg-background px-2 py-0.5 font-mono text-[11px] text-muted-foreground transition-colors hover:border-primary/60 hover:text-foreground"
              >
                {formatCombo(s)}
              </button>
            ))}
          </div>
        )}

        {statusLine}

        {/* No Save button: the combo IS the setting and saves itself. What is
            left is the one control that changes it to something else. */}
        {showReset && (
          <div className="mt-3 flex flex-wrap items-center gap-2">
            <button
              type="button"
              className="text-[11px] text-muted-foreground underline hover:text-foreground"
              onClick={() => {
                if (def) assign(def);
              }}
            >
              {t("settings_view.keybinds.reset")}
            </button>
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
              if (def) assign(def);
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
