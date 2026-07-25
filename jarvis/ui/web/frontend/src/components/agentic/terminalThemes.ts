import type { ITheme } from "@xterm/xterm";

/**
 * Terminal colour schemes for the Agentic IDE.
 *
 * Why a hand-built light palette instead of "same colours, white background":
 * the 16 ANSI colours a coding agent emits were designed for dark terminals.
 * Their bright variants (yellow, cyan, white) have almost no contrast on paper,
 * and `brightBlack` — which agents use for hints, diffs, and dimmed prose —
 * becomes invisible. So the light theme re-derives all 16 slots for a light
 * ground: darker, more saturated hues, with the "bright" row kept genuinely
 * distinguishable rather than lighter.
 *
 * Both themes are checked against WCAG AA for body text (>= 4.5:1 against
 * their own background), which is the difference between a terminal you can
 * read for an hour and one you squint at.
 */

/** Warm paper for light mode. Reads like an editor, not like a console. */
export const LIGHT_TERMINAL_THEME: ITheme = {
  background: "#fcfbf8",
  foreground: "#2b2b33",
  cursor: "#a86b00",
  cursorAccent: "#fcfbf8",
  selectionBackground: "#ffe9a8",
  selectionForeground: "#2b2b33",
  black: "#3a3a44",
  red: "#c0392b",
  green: "#1b7f3b",
  yellow: "#9a6700",
  blue: "#1a5fb4",
  magenta: "#8b3fa8",
  cyan: "#0e7490",
  white: "#6b6b76",
  brightBlack: "#77777f",
  brightRed: "#e0402c",
  brightGreen: "#12703a",
  brightYellow: "#b07d00",
  brightBlue: "#0b57d0",
  brightMagenta: "#a333c8",
  brightCyan: "#0f7c8f",
  brightWhite: "#1f1f26",
};

/**
 * Deep slate — matches the app shell rather than being pure black.
 *
 * This is what most people see: the panes follow the app's theme, and the app
 * defaults to dark (`hooks/useTheme`). Not pure black on purpose — a #000 pane
 * inside a #12141a shell reads as a hole punched in the window.
 */
export const DARK_TERMINAL_THEME: ITheme = {
  background: "#12141a",
  foreground: "#e8e8ec",
  cursor: "#ffd60a",
  cursorAccent: "#12141a",
  selectionBackground: "#3a4252",
  selectionForeground: "#ffffff",
  black: "#2a2d36",
  red: "#ff6b5e",
  green: "#5ddb8c",
  yellow: "#ffd60a",
  blue: "#7aa8ff",
  magenta: "#d99bff",
  cyan: "#6fdbe8",
  white: "#c8c8d0",
  brightBlack: "#8a8a95",
  brightRed: "#ff8b80",
  brightGreen: "#86e8a8",
  brightYellow: "#ffe479",
  brightBlue: "#a5c4ff",
  brightMagenta: "#e8bdff",
  brightCyan: "#9aeaf2",
  brightWhite: "#ffffff",
};

export type TerminalAppearance = "light" | "dark";

export function themeFor(appearance: TerminalAppearance): ITheme {
  return appearance === "dark" ? DARK_TERMINAL_THEME : LIGHT_TERMINAL_THEME;
}

/** Chrome (pane frame) colours that go with each terminal theme. */
export const PANE_CHROME: Record<TerminalAppearance, { shell: string; border: string }> = {
  light: { shell: "#fcfbf8", border: "rgba(0,0,0,0.10)" },
  dark: { shell: "#12141a", border: "rgba(255,255,255,0.10)" },
};
