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
 * An **app** entry is an in-window chord stored per device (the quick
 * switcher, the zoom steps, the overview, the IDE key menu). It carries no keys
 * either: the renderer reads the live setting, including whether it is off.
 *
 * Entries are grouped by area so the overlay can render sections without a
 * second ordering table.
 */
import type { KeybindAction } from "@/hooks/useHotkey";
import type { AppChordId } from "@/lib/appChords";

export type ShortcutArea = "voice" | "workspace";

/**
 * Where a shortcut fires: anywhere on the computer (a global OS hotkey), only
 * while the Jarvis window is in front, or only inside an agent terminal.
 */
export type ShortcutScope = "global" | "window" | "terminal";

interface ShortcutBase {
  /** i18n key for the one-line description. */
  labelKey: string;
  area: ShortcutArea;
  scope: ShortcutScope;
}

export interface RebindableShortcut extends ShortcutBase {
  kind: "rebindable";
  /** Backend keybind action; the current combo is read live, never stored. */
  action: KeybindAction;
}

/**
 * An in-app shortcut whose chord is a per-device preference: the quick
 * switcher, the whole-app zoom, and the chords of ./appChords. Like a
 * rebindable entry it carries no keys — the overlay reads the live setting,
 * including whether the shortcut is switched off.
 */
export interface AppSettingShortcut extends ShortcutBase {
  kind: "app";
  setting: "quick_switch" | "app_zoom_in" | "app_zoom_out" | "app_zoom_reset" | AppChordId;
}

export type Shortcut = RebindableShortcut | AppSettingShortcut;

export const SHORTCUTS: readonly Shortcut[] = [
  // ── Voice — every one of these is rebindable in Settings ───────────────
  { kind: "rebindable", area: "voice", scope: "global", action: "dictate", labelKey: "shortcut_overlay.voice.dictate" },
  {
    kind: "rebindable",
    area: "voice",
    scope: "global",
    action: "dictate_toggle",
    labelKey: "shortcut_overlay.voice.dictate_toggle",
  },
  { kind: "rebindable", area: "voice", scope: "global", action: "call", labelKey: "shortcut_overlay.voice.call" },
  { kind: "rebindable", area: "voice", scope: "global", action: "hangup", labelKey: "shortcut_overlay.voice.hangup" },
  {
    kind: "rebindable",
    area: "voice",
    scope: "global",
    action: "paste_last",
    labelKey: "shortcut_overlay.voice.paste_last",
  },

  // ── Workspace — per-device chords, changed under Settings ───────────────
  {
    kind: "app",
    area: "workspace",
    scope: "window",
    setting: "quick_switch",
    labelKey: "shortcut_overlay.workspace.quick_switch",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "window",
    setting: "app_zoom_in",
    labelKey: "shortcut_overlay.workspace.app_zoom_in",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "window",
    setting: "app_zoom_out",
    labelKey: "shortcut_overlay.workspace.app_zoom_out",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "window",
    setting: "app_zoom_reset",
    labelKey: "shortcut_overlay.workspace.app_zoom_reset",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "window",
    setting: "shortcut_overlay",
    labelKey: "shortcut_overlay.workspace.open_overlay",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "terminal",
    setting: "terminal_zoom_in",
    labelKey: "shortcut_overlay.workspace.zoom_in",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "terminal",
    setting: "terminal_zoom_out",
    labelKey: "shortcut_overlay.workspace.zoom_out",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "terminal",
    setting: "terminal_zoom_reset",
    labelKey: "shortcut_overlay.workspace.zoom_reset",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "terminal",
    setting: "ide_menu",
    labelKey: "shortcut_overlay.workspace.ide_menu",
  },
  {
    kind: "app",
    area: "workspace",
    scope: "window",
    setting: "ide_commands",
    labelKey: "shortcut_overlay.workspace.ide_commands",
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
  // A saved combo spells modifiers the PC way; a Mac keycap prints a glyph.
  if (isMac) {
    const glyph = MAC_GLYPHS[token.toLowerCase()];
    if (glyph) return glyph;
  }
  return token;
}

const MAC_GLYPHS: Record<string, string> = {
  ctrl: "⌃",
  alt: "⌥",
  right_alt: "⌥",
  cmd: "⌘",
  command: "⌘",
  meta: "⌘",
  win: "⌘",
  super: "⌘",
};
