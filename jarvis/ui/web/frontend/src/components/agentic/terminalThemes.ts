import type { ITheme } from "@xterm/xterm";

/**
 * Terminal colour schemes for the Agentic IDE.
 *
 * ## Why this file holds literal colours when nothing else may
 *
 * The design system forbids a literal colour anywhere in the product; every
 * surface names a token. This module is its one sanctioned exception, for two
 * reasons that cannot be designed away. xterm's `ITheme` takes resolved colour
 * strings — it cannot read `hsl(var(--card))`, and the minimum-contrast maths
 * below needs real numbers to compute a ratio from. And a pane's appearance is
 * a SEPARATE setting from the app theme: a light pane inside a dark app is a
 * supported combination, so a pane that read app tokens would paint its chrome
 * for the wrong ground exactly when the two disagree.
 *
 * So the rule holds in a different shape: the 16 ANSI slots are the terminal's
 * own palette (one of the few places hue legitimately survives), and everything
 * else in this file — `PANE_CHROME`, `PANE_BRAND` — is the app's surface and
 * ink ladder re-derived for each appearance. No value here is invented; each
 * one names the step it stands for in its comment.
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
  cursor: "#0a0a0a",
  cursorAccent: "#fcfbf8",
  selectionBackground: "#dedcd4",
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
  cursor: "#ffffff",
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
  /**
   * The pane's float step: a tooltip or menu that leaves the plane.
   *
   * Opaque, unlike `shell`. A card that can land on top of another pane's
   * output must not be read THROUGH, and it is small enough that lift costs
   * the room nothing (see the "lift scales inversely with area" rule).
   */
  float: string;
  /** The edge that says what this pane's agent is doing. */
  edge: Record<PaneEdgeState, string>;
}

/**
 * Chrome (pane frame) colours that go with each terminal theme.
 *
 * Every value below is one of the app's own surface-ladder steps, expressed as
 * an alpha over the pane's ground so it still composites correctly when a
 * wallpaper is showing through the shell. On a flat ground they resolve to the
 * tokens by name: `border` lands on `--border`, `edge.exited` a step below it,
 * `PaneBrand.chip` on `--secondary`, `PaneBrand.accentSoft` on
 * `--border-strong`. The table exists because a pane's appearance is a
 * SEPARATE setting from the app theme — a light pane inside a dark app is a
 * supported combination — so the pane resolves the ladder against its own
 * ground rather than reading `hsl(var(--…))`, which would be the app's.
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
 * * `error` — the fault hue, at an alpha that reads across a room without
 *   turning the pane into an alert box.
 *
 * The red used to be the matching terminal theme's own `red` slot. It is now
 * the app's fault hue re-derived per appearance — the same `#E8574C` the dark
 * theme paints `--destructive` with, and the darker `#C72E23` light mode uses —
 * because "this pane failed" is one status across the whole product and must
 * not change hue with a per-pane preference. Re-deriving it here rather than
 * reading the token keeps the ground correct when the two settings disagree.
 */
/**
 * Ink and accent for a pane's title bar, resolved against the PANE's own ground.
 *
 * Not read from `hsl(var(--…))`, for the same reason `NOTICE_TONE` exists in
 * ./AgenticTerminal: terminal appearance is a separate setting from the app
 * theme, and an app token lands on the wrong ground exactly when the two
 * disagree.
 *
 * The values are the app's ink scale, not a second palette: `ink` is the body
 * step, `inkMuted` the meta step, `inkFaint` the placeholder step, and `accent`
 * is the fill — white on a dark pane, warm near-black on a light one. It used
 * to be a signal-yellow "brand" accent in two voices; the brand hue was retired
 * (Ink & Paper), and a title bar is chrome rather than a place to spend colour.
 * Hue now survives on a pane only where it means something: the terminal's own
 * 16 ANSI slots, the activity pill, and a failed edge.
 */
export interface PaneBrand {
  /** The fill — the focused pane's call-sign plate, and the header hairline. */
  accent: string;
  /** Ink ON the filled plate. The pane's ground, so the plate reads as a hole. */
  onAccent: string;
  /** The pane's `--border-strong`: field rims and the hairline's fade. */
  accentSoft: string;
  /** Primary ink — the resting call-sign, the headline under the pointer. */
  ink: string;
  /** Secondary ink — the recap headline at rest, and the seat chip. */
  inkMuted: string;
  /** Placeholder and disabled ink only. Never information. */
  inkFaint: string;
  /** The pane's `--secondary`: the resting call-sign chip, and hover grounds. */
  chip: string;
}

export const PANE_BRAND: Record<TerminalAppearance, PaneBrand> = {
  light: {
    accent: "#26251e",
    onAccent: "#f7f7f4",
    accentSoft: "rgba(38,37,30,0.24)",
    ink: "#26251e",
    inkMuted: "#6e6b61",
    inkFaint: "#918d82",
    chip: "rgba(38,37,30,0.08)",
  },
  dark: {
    accent: "#ffffff",
    onAccent: "#0a0a0a",
    accentSoft: "rgba(255,255,255,0.23)",
    ink: "#e6e6e6",
    inkMuted: "#949494",
    inkFaint: "#707070",
    chip: "rgba(255,255,255,0.17)",
  },
};

export const PANE_CHROME: Record<TerminalAppearance, PaneChrome> = {
  light: {
    shell: "rgba(252, 251, 248, 0.68)",
    border: "rgba(38,37,30,0.115)",
    float: "#ffffff",
    edge: {
      connecting: "rgba(38,37,30,0.115)",
      live: "rgba(38,37,30,0.115)",
      exited: "rgba(38,37,30,0.055)",
      error: "rgba(199,46,35,0.45)",
    },
  },
  dark: {
    shell: "rgba(10, 10, 10, 0.58)",
    border: "rgba(255,255,255,0.10)",
    float: "#3d3d3d",
    edge: {
      connecting: "rgba(255,255,255,0.10)",
      live: "rgba(255,255,255,0.10)",
      exited: "rgba(255,255,255,0.05)",
      error: "rgba(232,87,76,0.55)",
    },
  },
};
