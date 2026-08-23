/**
 * The list of keyboard shortcuts the app is willing to teach — the single
 * source the `?` overlay renders from.
 *
 * ## Two kinds of entry, and why they are different
 *
 * A **rebindable** entry carries no key at all, only the backend action name.
 * The voice keys (push-to-talk, hands-free, call, hang up, paste last) are
 * configurable in Settings, so writing their chord down here would be a copy
 * that goes stale the moment someone rebinds one — and an overlay that shows
 * the default after a rebind is worse than no overlay. The renderer resolves
 * these through `useKeybinds()`, which reads the live configuration, so what
 * the overlay shows is by construction what the backend has registered.
 *
 * A **fixed** entry carries its chord, because the chord is compiled into the
 * frontend and cannot be rebound. Those chords ARE duplicated from the modules
 * that implement them — there is no way to run a matcher backwards to recover
 * the keys it accepts. `shortcutRegistry.test.ts` closes that gap instead: it
 * feeds every chord declared here to the real matcher and asserts the real
 * matcher agrees. Change the implementation without changing this file and the
 * test fails, which is the drift protection the copy would otherwise lack.
 *
 * Entries are grouped by area so the overlay can render sections without a
 * second ordering table.
 */
import type { KeybindAction } from "@/hooks/useHotkey";

export type ShortcutArea = "voice" | "workspace";

interface ShortcutBase {
  /** i18n key for the one-line description. */
  labelKey: string;
  area: ShortcutArea;
}

export interface RebindableShortcut extends ShortcutBase {
  kind: "rebindable";
  /** Backend keybind action; the current combo is read live, never stored. */
  action: KeybindAction;
}

export interface FixedShortcut extends ShortcutBase {
  kind: "fixed";
  /**
   * Key tokens, in press order, as the overlay should draw them. `Mod` renders
   * as ⌘ on Apple keyboards and Ctrl everywhere else — the platform split the
   * implementing modules already make.
   */
  keys: string[];
  /**
   * Alternative spellings of the same chord on other layouts, drawn as a
   * secondary line. `+` is its own key on a German board but Shift+`=` on a US
   * one, and an overlay that names only one of them is wrong for half its
   * readers.
   */
  alternateKeys?: string[][];
}

export type Shortcut = RebindableShortcut | FixedShortcut;

export const SHORTCUTS: readonly Shortcut[] = [
  // ── Voice — every one of these is rebindable in Settings ───────────────
  { kind: "rebindable", area: "voice", action: "dictate", labelKey: "shortcut_overlay.voice.dictate" },
  {
    kind: "rebindable",
    area: "voice",
    action: "dictate_toggle",
    labelKey: "shortcut_overlay.voice.dictate_toggle",
  },
  { kind: "rebindable", area: "voice", action: "call", labelKey: "shortcut_overlay.voice.call" },
  { kind: "rebindable", area: "voice", action: "hangup", labelKey: "shortcut_overlay.voice.hangup" },
  {
    kind: "rebindable",
    area: "voice",
    action: "paste_last",
    labelKey: "shortcut_overlay.voice.paste_last",
  },

  // ── Workspace — fixed chords, verified against their matcher by the test ──
  {
    kind: "fixed",
    area: "workspace",
    keys: ["Mod", "+"],
    alternateKeys: [["Mod", "="]],
    labelKey: "shortcut_overlay.workspace.zoom_in",
  },
  {
    kind: "fixed",
    area: "workspace",
    keys: ["Mod", "-"],
    alternateKeys: [["Mod", "_"]],
    labelKey: "shortcut_overlay.workspace.zoom_out",
  },
  {
    kind: "fixed",
    area: "workspace",
    keys: ["Mod", "0"],
    labelKey: "shortcut_overlay.workspace.zoom_reset",
  },
  {
    kind: "fixed",
    area: "workspace",
    keys: ["?"],
    labelKey: "shortcut_overlay.workspace.open_overlay",
  },
] as const;

/** Areas in the order the overlay renders them. */
export const SHORTCUT_AREAS: readonly ShortcutArea[] = ["voice", "workspace"];

export function shortcutsForArea(area: ShortcutArea): Shortcut[] {
  return SHORTCUTS.filter((s) => s.area === area);
}

/**
 * Render one key token for display.
 *
 * Only `Mod` is platform-dependent; every other token is already the label a
 * reader expects to see on their keycap.
 */
export function keyLabel(token: string, isMac: boolean): string {
  if (token === "Mod") return isMac ? "⌘" : "Ctrl";
  return token;
}
