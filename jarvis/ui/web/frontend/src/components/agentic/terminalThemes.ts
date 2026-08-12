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
 * A TUI that still paints its own `bg_base` on every cell (Grok Build's
 * fullscreen themes do) is cleared on the way into xterm — see
 * ./terminalGlass — so that fill becomes the same default background Claude
 * Code already leaves alone.
 *
 * ## Why a palette alone cannot make every pane readable
 *
 * The 16 slots below only catch CLIs that speak classic ANSI. A modern coding
 * agent draws most of its UI in 24-bit truecolor — exact RGB values chosen for
 * the theme IT is configured for — and those bytes bypass this palette
 * entirely. A CLI set to its dark theme paints near-white text into a light
 * pane, and no slot remap can intercept that. `MINIMUM_CONTRAST_RATIO` is the
 * floor under that hole: xterm nudges ANY foreground (truecolor included)
 * toward black or white until it reaches the ratio against the background.
 * For that computation to run against the pane's REAL ground, each theme's
 * transparent `background` carries the shell's RGB at alpha 0 — invisible on
 * screen, but the number the contrast maths reads.
 */

/**
 * WCAG AA for body text, and the same default VS Code ships for its
 * integrated terminal. High enough that dark-theme truecolor becomes readable
 * on a light pane, low enough that a CLI's deliberate dim/bright hierarchy
 * survives.
 */
export const MINIMUM_CONTRAST_RATIO = 4.5;

/** Warm-paper contrast palette for light mode. */
export const LIGHT_TERMINAL_THEME: ITheme = {
  // Alpha 0 = still transparent; the RGB is the light shell's paper tone so
  // the minimum-contrast maths measures against the ground actually shown.
  background: "rgba(252, 251, 248, 0)",
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
  // #12141a at alpha 0 — the deep-slate ground the backend also reports to the
  // CLI (jarvis/agentic_ide/terminal_input.py); see the light theme's note.
  background: "rgba(18, 20, 26, 0)",
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
/**
 * Brand ink for a pane's title bar, resolved against the PANE's own ground.
 *
 * Not the app's `--primary` token, for the same reason `NOTICE_TONE` exists in
 * ./AgenticTerminal: terminal appearance is a separate setting from the app
 * theme (a light pane inside a dark app is a supported combination), and the
 * app's accent lands on the wrong ground exactly when the two disagree —
 * signal-yellow on paper is 1.4:1, a call-sign nobody can read. Each
 * appearance therefore carries its own accent, and it is the same hue that
 * appearance's terminal theme already uses for the cursor: the dark panes'
 * signal-yellow, the light panes' gold. The title bar reads as part of the
 * terminal it crowns, in the brand's two voices.
 */
export interface PaneBrand {
  /** The brand accent on this ground — the focused pane's call-sign plate. */
  accent: string;
  /** Ink ON the filled accent plate (black on yellow, white on gold). */
  onAccent: string;
  /** Translucent accent for hairlines, tints, and hover grounds. */
  accentSoft: string;
  /** A whisper of accent — the wash across the focused title bar. */
  accentWash: string;
  /** Primary ink — the resting call-sign, the headline under the pointer. */
  ink: string;
  /** Secondary ink — the recap headline at rest. */
  inkMuted: string;
  /** Tertiary ink — the CLI label and the seat chip. */
  inkFaint: string;
  /** Quiet chip ground behind the resting call-sign and the seat chip. */
  chip: string;
}

export const PANE_BRAND: Record<TerminalAppearance, PaneBrand> = {
  light: {
    accent: "#a86b00",
    onAccent: "#ffffff",
    accentSoft: "rgba(168,107,0,0.18)",
    accentWash: "rgba(168,107,0,0.05)",
    ink: "#2b2b33",
    inkMuted: "#54545d",
    inkFaint: "#77777f",
    chip: "rgba(0,0,0,0.055)",
  },
  dark: {
    accent: "#ffd60a",
    onAccent: "#0a0a0a",
    accentSoft: "rgba(255,214,10,0.18)",
    accentWash: "rgba(255,214,10,0.05)",
    ink: "#f2f2f5",
    inkMuted: "#a8a8b4",
    inkFaint: "#8a8a95",
    chip: "rgba(255,255,255,0.07)",
  },
};

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
