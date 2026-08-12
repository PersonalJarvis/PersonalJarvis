/**
 * Let a full-screen TUI keep the pane's glass instead of painting over it.
 *
 * xterm is already clear (`allowTransparency` + a transparent theme
 * background in ./terminalThemes). Claude Code and a plain shell leave the
 * default background alone, so the wallpaper shows through the pane shell.
 * A ratatui TUI such as Grok Build paints every cell with its theme's
 * `bg_base` — a solid RGB, not the default — and that one colour is what
 * turns the pane into an opaque card.
 *
 * Those canvas fills are a known, small set: each bundled Grok theme's
 * `bg_base`, plus the RGB this pane reports over OSC 11 so a CLI that
 * paints "whatever the terminal said it was" still goes clear. Highlight,
 * diff, hover and prompt-box backgrounds are different RGBs and stay put.
 *
 * The rewrite is visual only. It runs on the bytes handed to xterm, not
 * on the PTY stream the recap or activity detectors read.
 */

import { themeFor, type TerminalAppearance } from "./terminalThemes";

/** Pack `r,g,b` into one integer for the membership set. */
function pack(r: number, g: number, b: number): number {
  return (r << 16) | (g << 8) | b;
}

function packedFromCssRgb(css: string | undefined): number | null {
  if (!css) return null;
  const match = css.match(/rgba?\(\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)/i);
  if (!match) return null;
  return pack(Number(match[1]), Number(match[2]), Number(match[3]));
}

/**
 * Full-pane canvas fills. Sources:
 *
 * * Grok Build `bg_base` slots from xai-grok-pager-render themes
 *   (GrokNight, GrokDay, TokyoNight Storm, Rose Pine Moon, Oscura Midnight).
 * * The RGB half of this pane's own transparent theme background — the
 *   number OSC 11 answers with (`jarvis/agentic_ide/terminal_input.py`).
 */
const CANVAS_FILLS = new Set<number>([
  pack(20, 20, 20), // GrokNight
  pack(238, 238, 238), // GrokDay
  pack(36, 40, 59), // TokyoNight Storm
  pack(35, 33, 54), // Rose Pine Moon
  pack(3, 3, 4), // Oscura Midnight
]);

for (const appearance of ["dark", "light"] as TerminalAppearance[]) {
  const packed = packedFromCssRgb(themeFor(appearance).background);
  if (packed !== null) CANVAS_FILLS.add(packed);
}

const SGR = /\x1b\[([0-9;:]*)m/g;

function isCanvas(r: number, g: number, b: number): boolean {
  return (
    Number.isInteger(r) &&
    Number.isInteger(g) &&
    Number.isInteger(b) &&
    r >= 0 &&
    r <= 255 &&
    g >= 0 &&
    g <= 255 &&
    b >= 0 &&
    b <= 255 &&
    CANVAS_FILLS.has(pack(r, g, b))
  );
}

/**
 * True when `token` is a colon-form `48:2:…` RGB background whose colour
 * is a canvas fill. Returns the replacement `"49"` or null.
 */
function rewriteColonBackground(token: string): string | null {
  if (!token.startsWith("48:2:")) return null;
  const parts = token.slice(5).split(":");
  // `48:2:r:g:b` or `48:2:<space>:r:g:b` (empty / 0 colorspace).
  let r: string;
  let g: string;
  let b: string;
  if (parts.length === 3) {
    [r, g, b] = parts;
  } else if (parts.length === 4) {
    [, r, g, b] = parts;
  } else {
    return null;
  }
  return isCanvas(Number(r), Number(g), Number(b)) ? "49" : null;
}

/**
 * How many tokens after `38`/`48`/`58` belong to that colour. Zero means
 * the sequence is incomplete or not a colour we understand — leave it.
 */
function colourPayloadLength(tokens: string[], index: number): number {
  const kind = tokens[index + 1];
  if (kind === "2" && index + 4 < tokens.length) return 4;
  if (kind === "5" && index + 2 < tokens.length) return 2;
  return 0;
}

/**
 * Walk one SGR parameter list. `48;2;r;g;b` (and the colon form) that
 * names a canvas fill becomes `49` — default background. Foreground
 * `38;2;…` and underline `58;2;…` are consumed as colour payloads so a
 * channel value of `48` cannot be mistaken for a background setter.
 */
export function rewriteCanvasFillSgr(params: string): string {
  if (!params.includes("48")) return params;
  const tokens = params.split(";");
  const out: string[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    const colon = rewriteColonBackground(token);
    if (colon !== null) {
      out.push(colon);
      continue;
    }
    if (token === "48") {
      const extra = colourPayloadLength(tokens, i);
      if (extra === 4) {
        const red = Number(tokens[i + 2]);
        const green = Number(tokens[i + 3]);
        const blue = Number(tokens[i + 4]);
        if (isCanvas(red, green, blue)) {
          out.push("49");
          i += extra;
          continue;
        }
      }
      out.push(token);
      if (extra > 0) {
        out.push(...tokens.slice(i + 1, i + 1 + extra));
        i += extra;
      }
      continue;
    }
    if (token === "38" || token === "58") {
      const extra = colourPayloadLength(tokens, i);
      out.push(token);
      if (extra > 0) {
        out.push(...tokens.slice(i + 1, i + 1 + extra));
        i += extra;
      }
      continue;
    }
    out.push(token);
  }
  return out.join(";");
}

/**
 * Replace canvas-fill cell backgrounds with the terminal default, so the
 * pane shell (and the wallpaper under it) shows through.
 *
 * Complete SGR sequences only — a CSI split across two PTY reads is left
 * for xterm, the same as every other escape this pane does not rewrite.
 */
export function clearTuiCanvasFill(text: string): string {
  if (!text.includes("\x1b[") || !text.includes("48")) return text;
  return text.replace(SGR, (full, params: string) => {
    const next = rewriteCanvasFillSgr(params);
    return next === params ? full : `\x1b[${next}m`;
  });
}
