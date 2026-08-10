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
 * The xterm canvas itself stays transparent. The stable reading ground comes
 * from the translucent pane shell below it, so the desktop artwork remains
 * visible without stacking two dark fills into an effectively opaque panel.
 */

const TRANSPARENT_TERMINAL_BACKGROUND = "rgba(0, 0, 0, 0)";

/** Warm-paper contrast palette for light mode. */
export const LIGHT_TERMINAL_THEME: ITheme = {
  background: TRANSPARENT_TERMINAL_BACKGROUND,
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
 * Deep-slate contrast palette for dark mode.
 *
 * This is what most people see: the panes follow the app's theme, and the app
 * defaults to dark (`hooks/useTheme`). The canvas stays clear; the translucent
 * pane shell below it supplies the dark reading ground.
 */
export const DARK_TERMINAL_THEME: ITheme = {
  background: TRANSPARENT_TERMINAL_BACKGROUND,
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

/**
 * The lifecycle a pane's frame reports, mirroring `PaneStatus` in
 * ./AgenticTerminal.
 *
 * Declared here rather than imported so the colour module stays a leaf — the
 * terminal already imports this file, and a second edge would be a cycle. The
 * two unions are checked against each other structurally at every use site:
 * indexing `edge` with a `PaneStatus` stops compiling the moment one of them
 * grows a member the other does not have, which is the point.
 */
export type PaneEdgeState = "connecting" | "live" | "exited" | "error";

export interface PaneChrome {
  /** The translucent ground the pane — header AND terminal — is drawn on. */
  shell: string;
  /** The pane's resting edge, and the rule under its header. */
  border: string;
  /** The edge that says what this pane's agent is doing. */
  edge: Record<PaneEdgeState, string>;
}

/**
 * Chrome (pane frame) colours that go with each terminal theme.
 *
 * These alphas mirror the shared section-panel glass tokens. Keeping the tint
 * on this single layer lets xterm remain clear while preserving text contrast.
 *
 * ## Why the edge is a state and not a decoration
 *
 * A wall of twelve panes has one thing every reader is looking for: which of
 * them still needs them. The activity pill answers that per pane, but a pill is
 * something you READ — you have to land on the pane first. The edge is what the
 * eye can sweep, so it carries exactly one distinction and carries it quietly:
 *
 * * `connecting` / `live` — the resting edge. The overwhelming majority of
 *   panes are here, and a workspace where everything is marked marks nothing.
 * * `exited` — dimmer than resting. The agent is gone; the pane recedes rather
 *   than shouting, because a finished terminal is not a problem.
 * * `error` — the CLI's own red, at an alpha that reads across a room without
 *   turning the pane into an alert box.
 *
 * The red is the matching terminal theme's own `red` slot rather than the app's
 * `--destructive`, and that is deliberate: terminal appearance is a separate
 * setting from the app theme (a light pane inside a dark app is a supported
 * combination), so a token picked for the app lands on the wrong ground exactly
 * when the two disagree.
 */
export const PANE_CHROME: Record<TerminalAppearance, PaneChrome> = {
  light: {
    shell: "rgba(252, 251, 248, 0.68)",
    border: "rgba(0,0,0,0.10)",
    edge: {
      connecting: "rgba(0,0,0,0.10)",
      live: "rgba(0,0,0,0.10)",
      exited: "rgba(0,0,0,0.05)",
      error: "rgba(192,57,43,0.45)",
    },
  },
  dark: {
    shell: "rgba(10, 10, 10, 0.58)",
    border: "rgba(255,255,255,0.10)",
    edge: {
      connecting: "rgba(255,255,255,0.10)",
      live: "rgba(255,255,255,0.10)",
      exited: "rgba(255,255,255,0.05)",
      error: "rgba(255,107,94,0.50)",
    },
  },
};
