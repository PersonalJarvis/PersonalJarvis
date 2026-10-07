import { useEffect, useRef } from "react";

import { chordToCombo, codeToKeyToken, codeToModifierToken } from "@/hooks/useHotkey";
import { mouseButtonCode, mouseButtonToToken } from "@/views/settings/keyboardLayout";

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
 * to prevent. Modifier releases are recovered a different way: every keyboard
 * and mouse event carries the true flag word, and while capturing we also poll
 * the OS snapshot at ``GET /api/settings/keybinds/held``. The gesture ends when
 * the user lets go of everything — or when the window loses focus, which ends
 * it whether the keys came up or not.
 */
const _LOST_KEYUP_MS = 900;

/**
 * How often the recorder asks the OS which modifiers are actually down.
 *
 * Short enough that a lone Option/Command whose DOM keyup never arrives still
 * commits as part of the same gesture; long enough that a Settings panel with
 * the recorder closed does not poll at all (the interval only runs while
 * capturing). A host that answers ``available: false`` is asked once and then
 * left alone.
 */
const _NATIVE_HELD_MS = 80;

export interface ChordCaptureHandlers {
  /** The chord so far, on every change while keys go down. */
  onPreview: (combo: string) => void;
  /** Every key (and mouse button) let go: the finished chord. */
  onCommit: (combo: string) => void;
  /** Escape: the recording is abandoned. */
  onCancel: () => void;
  /** The physical codes held right now, for a live keyboard highlight. */
  onPressed?: (codes: Set<string>) => void;
}

/**
 * Records one key chord while `capturing` is true: "hold your keys, then let
 * go". Shared by the Settings keybind rows and the first-run setup, so every
 * place that records a shortcut has the same rescue paths for swallowed key
 * releases. The owner decides what a preview, a commit and a cancel mean.
 */
export function useChordCapture(capturing: boolean, onEvents: ChordCaptureHandlers): void {
  // Held in a ref so the listeners live for exactly one gesture and always
  // call the owner's current callbacks.
  const handlers = useRef(onEvents);
  handlers.current = onEvents;

  useEffect(() => {
    if (!capturing) return;
    handlers.current.onPressed?.(new Set()); // fresh highlight state for this gesture
    const held = new Set<string>(); // non-modifier key tokens seen this gesture
    const pressed = new Set<string>(); // physical event.codes currently down
    let pending: string | null = null; // fullest chord captured so far
    let idle: ReturnType<typeof setTimeout> | undefined; // rescue-commit timer

    function commit() {
      // Letting go of the keys ends the gesture: the owner saves at once.
      if (pending) handlers.current.onCommit(pending);
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

    function familyOfCode(code: string): "ctrl" | "alt" | "shift" | "meta" | null {
      if (code.startsWith("Control")) return "ctrl";
      if (code.startsWith("Alt")) return "alt";
      if (code.startsWith("Shift")) return "shift";
      if (code.startsWith("Meta")) return "meta";
      return null;
    }

    function codesForFamily(family: "ctrl" | "alt" | "shift" | "meta"): string[] {
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

    function canonicalCode(family: "ctrl" | "alt" | "shift" | "meta"): string {
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

    type FlagWord = { ctrl: boolean; alt: boolean; shift: boolean; meta: boolean };

    function flagsFromEvent(e: {
      ctrlKey: boolean;
      altKey: boolean;
      shiftKey: boolean;
      metaKey: boolean;
    }): FlagWord {
      return {
        ctrl: e.ctrlKey,
        alt: e.altKey,
        shift: e.shiftKey,
        meta: e.metaKey,
      };
    }

    function dropReleasedModifiers(flags: FlagWord): boolean {
      let changed = false;
      for (const code of [...pressed]) {
        const family = familyOfCode(code);
        if (family === null) continue;
        if (!flags[family]) {
          pressed.delete(code);
          changed = true;
        }
      }
      return changed;
    }

    function ensureFlaggedModifiers(flags: FlagWord): boolean {
      let changed = false;
      for (const family of ["ctrl", "alt", "shift", "meta"] as const) {
        if (!flags[family]) continue;
        if (codesForFamily(family).some((c) => pressed.has(c))) continue;
        pressed.add(canonicalCode(family));
        changed = true;
      }
      return changed;
    }

    function previewFromPressed() {
      const next = chordToCombo(
        {
          code: "",
          ctrlKey: codesForFamily("ctrl").some((c) => pressed.has(c)),
          altKey: codesForFamily("alt").some((c) => pressed.has(c)),
          shiftKey: codesForFamily("shift").some((c) => pressed.has(c)),
          metaKey: codesForFamily("meta").some((c) => pressed.has(c)),
        },
        held,
      );
      if (next) {
        pending = next;
        handlers.current.onPreview(next);
      }
    }

    function finishIfReleased(): boolean {
      if (pressed.size === 0 && pending) {
        if (idle) clearTimeout(idle);
        commit();
        return true;
      }
      return false;
    }

    // Families the OS snapshot has reported as down at least once this
    // gesture. We only DROP a modifier from that snapshot after it has been
    // seen down: a lagging first poll that says "nothing held" must not
    // commit Ctrl+Alt out from under someone who just pressed them.
    const nativeSeen = new Set<"ctrl" | "alt" | "shift" | "meta">();
    let nativeUnavailable = false;

    function applyNativeTokens(tokens: string[]) {
      const set = new Set(tokens);
      const nativeFlags: FlagWord = {
        ctrl: set.has("ctrl"),
        alt: set.has("alt"),
        shift: set.has("shift"),
        meta:
          set.has("cmd") ||
          set.has("win") ||
          set.has("command") ||
          set.has("window") ||
          set.has("meta") ||
          set.has("super"),
      };
      let changed = false;
      for (const family of ["ctrl", "alt", "shift", "meta"] as const) {
        if (nativeFlags[family]) {
          nativeSeen.add(family);
          if (!codesForFamily(family).some((c) => pressed.has(c))) {
            pressed.add(canonicalCode(family));
            changed = true;
          }
        } else if (nativeSeen.has(family)) {
          nativeSeen.delete(family);
          for (const code of codesForFamily(family)) {
            if (pressed.delete(code)) changed = true;
          }
        }
      }
      if (!changed) return;
      handlers.current.onPressed?.(new Set(pressed));
      previewFromPressed();
      finishIfReleased();
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
        // Undo the live preview: the owner restores its saved value.
        handlers.current.onCancel();
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
      const flags = flagsFromEvent(e);
      ensureFlaggedModifiers(flags);
      dropReleasedModifiers(flags);
      handlers.current.onPressed?.(new Set(pressed)); // live keyboard highlight
      const tok = codeToKeyToken(e.code);
      if (tok) held.add(tok);
      const next = chordToCombo(e, held);
      if (next) {
        pending = next;
        handlers.current.onPreview(next); // live preview as the chord grows
      }
      armRescue();
    }

    function onKeyUp(e: KeyboardEvent) {
      e.preventDefault();
      e.stopPropagation();
      pressed.delete(e.code);
      // A letter's keyup carries the TRUE modifier flags. If Option/Command
      // was released and its own keyup was swallowed, this is the moment we
      // find out — without waiting for a mouse move.
      dropReleasedModifiers(flagsFromEvent(e));
      handlers.current.onPressed?.(new Set(pressed)); // live keyboard highlight
      // Fast path: commit the instant EVERY key is released. `pending` holds
      // the fullest chord seen during the gesture, so the release order never
      // matters and early-lifted keys are not lost.
      if (finishIfReleased()) return;
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
      if (!dropReleasedModifiers(flagsFromEvent(e))) return;
      handlers.current.onPressed?.(new Set(pressed));
      finishIfReleased();
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
      handlers.current.onPressed?.(new Set(pressed));
      held.add(tok);
      const next = chordToCombo(asKeyEventLike(e), held);
      if (next) {
        pending = next;
        handlers.current.onPreview(next);
      }
      armRescue();
    }

    function onMouseUp(e: MouseEvent) {
      const tok = mouseButtonToToken(e.button);
      if (tok === null) return;
      e.preventDefault();
      e.stopPropagation();
      pressed.delete(mouseButtonCode(e.button));
      handlers.current.onPressed?.(new Set(pressed));
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

    // OS snapshot of the modifier keys. Covers the case WKWebView never
    // delivers a modifier event at all (lone Command on macOS): the probe
    // sees the flags word, the recorder lights the key, and the matching
    // "up" snapshot commits. A host that cannot tell is asked once.
    async function tickNative() {
      if (nativeUnavailable) return;
      try {
        const res = await fetch("/api/settings/keybinds/held");
        if (!res.ok) return;
        const data: { available?: boolean; tokens?: string[] } = await res.json();
        if (nativeUnavailable) return;
        if (data.available !== true) {
          nativeUnavailable = true;
          return;
        }
        applyNativeTokens(Array.isArray(data.tokens) ? data.tokens : []);
      } catch {
        // A single failed probe is not "unavailable"; the next tick retries.
      }
    }
    const nativeTimer = setInterval(() => {
      void tickNative();
    }, _NATIVE_HELD_MS);
    void tickNative();

    return () => {
      if (idle) clearTimeout(idle);
      clearInterval(nativeTimer);
      nativeUnavailable = true;
      window.removeEventListener("keydown", onKeyDown, true);
      window.removeEventListener("keyup", onKeyUp, true);
      window.removeEventListener("mousedown", onMouseDown, true);
      window.removeEventListener("mouseup", onMouseUp, true);
      window.removeEventListener("auxclick", onAuxClick, true);
      window.removeEventListener("mousemove", syncModifiers, true);
      window.removeEventListener("blur", onWindowBlur);
    };
  }, [capturing]);
}
