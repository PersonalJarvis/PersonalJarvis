/**
 * The fill and ink of a message bubble tinted with an agent's colour.
 *
 * An agent's colour is any hex a person picked, so the ink is chosen from the
 * colour itself instead of a fixed white: a light colour (most of the
 * companion palette) keeps its fill and gets dark ink, a dark one keeps its
 * fill and gets white ink. A mid-tone that reaches neither threshold is
 * darkened step by step, keeping its hue, until white ink reads on it. Every
 * result meets WCAG AA (4.5:1) for body text.
 */

export interface BubbleTint {
  background: string;
  color: string;
}

/** Near-black ink for light bubbles; softer than #000 on a saturated fill. */
export const DARK_INK = "#111114";
export const LIGHT_INK = "#ffffff";

/** WCAG AA for body text. */
const AA = 4.5;
/** Dark ink only where it reads clearly better than white could after darkening. */
const DARK_INK_MIN = 7;

function parseHex(hex: string): [number, number, number] | null {
  const match = /^#?([0-9a-f]{6})$/i.exec(hex.trim());
  if (!match) return null;
  const value = parseInt(match[1], 16);
  return [(value >> 16) & 255, (value >> 8) & 255, value & 255];
}

function toHex([r, g, b]: [number, number, number]): string {
  return `#${[r, g, b].map((c) => Math.round(c).toString(16).padStart(2, "0")).join("")}`;
}

function channel(c: number): number {
  const s = c / 255;
  return s <= 0.04045 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance(rgb: [number, number, number]): number {
  return 0.2126 * channel(rgb[0]) + 0.7152 * channel(rgb[1]) + 0.0722 * channel(rgb[2]);
}

export function contrast(a: string, b: string): number {
  const ra = parseHex(a);
  const rb = parseHex(b);
  if (!ra || !rb) return 1;
  const la = luminance(ra);
  const lb = luminance(rb);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

/** Null for anything that is not a #rrggbb colour; the caller keeps its default look. */
export function bubbleTint(hex: string | null | undefined): BubbleTint | null {
  const rgb = hex ? parseHex(hex) : null;
  if (!rgb) return null;
  const base = toHex(rgb);
  if (contrast(base, LIGHT_INK) >= AA) return { background: base, color: LIGHT_INK };
  if (contrast(base, DARK_INK) >= DARK_INK_MIN) return { background: base, color: DARK_INK };
  for (let factor = 0.95; factor > 0; factor -= 0.05) {
    const darker = toHex([rgb[0] * factor, rgb[1] * factor, rgb[2] * factor]);
    if (contrast(darker, LIGHT_INK) >= AA) return { background: darker, color: LIGHT_INK };
  }
  return { background: "#000000", color: LIGHT_INK };
}
