/**
 * Re-ink a dark-themed CLI for a light pane.
 *
 * A coding agent paints most of its interface in 24-bit truecolor chosen for
 * the theme IT is set to, and nearly every one ships set to dark. In a light
 * pane that leaves two defects the 16-slot palette in ./terminalThemes cannot
 * reach:
 *
 * * dark cell BACKGROUNDS — Claude Code's prompt bar (`48;2;55;55;55`), diff
 *   rows, selection rows — land as near-black slabs on paper;
 * * light FOREGROUNDS — white body text, pale blue links, mid-grey hints — are
 *   rescued by xterm's minimum-contrast nudge only as far as the 4.5:1 floor,
 *   which leaves washed-out grey and lavender where the CLI meant ink.
 *
 * So on a light pane every truecolor (and 256-colour) setter is mirrored in
 * lightness the way the CLI's own light theme would have drawn it: a dark
 * ground becomes a pale tint of the same hue, a light ink becomes a dark ink
 * of the same hue. Colours that already work on paper — dark ink, pale grounds
 * — pass through untouched, so a CLI already set to its light theme is left
 * exactly as it is. The 16 ANSI slots are the palette's job and are never
 * touched here.
 *
 * Like ./terminalGlass, this rewrites only the bytes handed to xterm, never
 * the PTY stream the recap and activity detectors read. Output written while
 * the pane was dark keeps its colours after a switch; the contrast floor still
 * keeps it readable until the agent redraws.
 */

type Rgb = readonly [number, number, number];

/** The light pane's paper, `LIGHT_TERMINAL_THEME.background` in RGB. */
const PAPER: Rgb = [252, 251, 248];

/** Ink that already clears WCAG AA on paper is left as the CLI chose it. */
const INK_FLOOR = 4.5;

/** A ground darker than this relative luminance is a dark-theme slab. */
const DARK_GROUND = 0.2;

function channel(v: number): number {
  const c = v / 255;
  return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function luminance([r, g, b]: Rgb): number {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a: Rgb, b: Rgb): number {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

function toHsl([r, g, b]: Rgb): [number, number, number] {
  const rn = r / 255;
  const gn = g / 255;
  const bn = b / 255;
  const max = Math.max(rn, gn, bn);
  const min = Math.min(rn, gn, bn);
  const l = (max + min) / 2;
  if (max === min) return [0, 0, l];
  const d = max - min;
  const s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
  let h: number;
  if (max === rn) h = (gn - bn) / d + (gn < bn ? 6 : 0);
  else if (max === gn) h = (bn - rn) / d + 2;
  else h = (rn - gn) / d + 4;
  return [h / 6, s, l];
}

function toRgb(h: number, s: number, l: number): Rgb {
  if (s === 0) {
    const v = Math.round(l * 255);
    return [v, v, v];
  }
  const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
  const p = 2 * l - q;
  const hue = (t: number) => {
    let x = t;
    if (x < 0) x += 1;
    if (x > 1) x -= 1;
    if (x < 1 / 6) return p + (q - p) * 6 * x;
    if (x < 1 / 2) return q;
    if (x < 2 / 3) return p + (q - p) * (2 / 3 - x) * 6;
    return p;
  };
  return [
    Math.round(hue(h + 1 / 3) * 255),
    Math.round(hue(h) * 255),
    Math.round(hue(h - 1 / 3) * 255),
  ];
}

function clamp(v: number, lo: number, hi: number): number {
  return Math.min(hi, Math.max(lo, v));
}

/** A greyish colour keeps the neutral ink ladder; a hue keeps its hue. */
const NEUTRAL_SATURATION = 0.12;

/**
 * The ink a light theme would have used for `rgb`, or `rgb` itself when it
 * already reads on paper.
 *
 * Lightness is mirrored (L → 1 − L), so the CLI's own hierarchy survives the
 * flip: its brightest text becomes the darkest ink and its dim hints stay a
 * step lighter. The clamp keeps a neutral off pure black (the pane's own
 * foreground is a soft near-black) and keeps a hue dark enough to read
 * without collapsing into black.
 */
export function lightInkFor(rgb: Rgb): Rgb {
  if (contrast(rgb, PAPER) >= INK_FLOOR) return rgb;
  const [h, s, l] = toHsl(rgb);
  const mirrored = 1 - l;
  const next =
    s < NEUTRAL_SATURATION
      ? clamp(mirrored, 0.1, 0.36)
      : clamp(mirrored, 0.22, 0.34);
  return toRgb(h, s, next);
}

/**
 * The ground a light theme would have used for `rgb`, or `rgb` itself when it
 * is not a dark slab.
 */
export function lightGroundFor(rgb: Rgb): Rgb {
  if (luminance(rgb) >= DARK_GROUND) return rgb;
  const [h, s, l] = toHsl(rgb);
  const mirrored = 1 - l;
  const next =
    s < NEUTRAL_SATURATION
      ? clamp(mirrored, 0.91, 0.95)
      : clamp(mirrored, 0.86, 0.93);
  return toRgb(h, s, next);
}

/** xterm's 256-colour cube and grey ramp; 0-15 are the palette's, not ours. */
function xterm256(index: number): Rgb | null {
  if (!Number.isInteger(index) || index < 16 || index > 255) return null;
  if (index >= 232) {
    const v = 8 + (index - 232) * 10;
    return [v, v, v];
  }
  const n = index - 16;
  const step = (c: number) => (c === 0 ? 0 : 55 + c * 40);
  return [step(Math.floor(n / 36)), step(Math.floor(n / 6) % 6), step(n % 6)];
}

function validRgb(r: number, g: number, b: number): Rgb | null {
  const ok = [r, g, b].every((v) => Number.isInteger(v) && v >= 0 && v <= 255);
  return ok ? [r, g, b] : null;
}

const cache = new Map<number, Rgb>();

function adapt(kind: "38" | "48", rgb: Rgb): Rgb {
  const key = (kind === "48" ? 1 << 24 : 0) | (rgb[0] << 16) | (rgb[1] << 8) | rgb[2];
  let hit = cache.get(key);
  if (!hit) {
    hit = kind === "48" ? lightGroundFor(rgb) : lightInkFor(rgb);
    cache.set(key, hit);
  }
  return hit;
}

function emit(kind: "38" | "48", rgb: Rgb): string {
  return `${kind};2;${rgb[0]};${rgb[1]};${rgb[2]}`;
}

/** `38:2::r:g:b`, `38:2:r:g:b` or `38:5:n` (and the `48` forms), else null. */
function rewriteColon(token: string): string | null {
  const parts = token.split(":");
  const kind = parts[0];
  if (kind !== "38" && kind !== "48") return null;
  let rgb: Rgb | null = null;
  if (parts[1] === "2" && (parts.length === 5 || parts.length === 6)) {
    const [r, g, b] = parts.slice(-3).map(Number);
    rgb = validRgb(r, g, b);
  } else if (parts[1] === "5" && parts.length === 3) {
    rgb = xterm256(Number(parts[2]));
  }
  return rgb ? emit(kind, adapt(kind, rgb)) : null;
}

/**
 * Walk one SGR parameter list and re-ink every truecolor / 256-colour
 * foreground and background. Underline colour (`58`) is consumed so its
 * channel values are never read as setters, and otherwise left alone.
 */
export function rewriteLightInkSgr(params: string): string {
  const tokens = params.split(";");
  const out: string[] = [];
  for (let i = 0; i < tokens.length; i += 1) {
    const token = tokens[i];
    if (token.includes(":")) {
      out.push(rewriteColon(token) ?? token);
      continue;
    }
    if (token !== "38" && token !== "48" && token !== "58") {
      out.push(token);
      continue;
    }
    const mode = tokens[i + 1];
    if (mode === "2" && i + 4 < tokens.length) {
      const payload = tokens.slice(i + 1, i + 5);
      const rgb = validRgb(Number(payload[1]), Number(payload[2]), Number(payload[3]));
      out.push(token === "58" || !rgb ? [token, ...payload].join(";") : emit(token, adapt(token, rgb)));
      i += 4;
      continue;
    }
    if (mode === "5" && i + 2 < tokens.length) {
      const rgb = xterm256(Number(tokens[i + 2]));
      out.push(token === "58" || !rgb ? `${token};5;${tokens[i + 2]}` : emit(token, adapt(token, rgb)));
      i += 2;
      continue;
    }
    out.push(token);
  }
  return out.join(";");
}

const SGR = /\x1b\[([0-9;:]*)m/g;

/**
 * Re-ink complete SGR sequences in `text` for a light pane.
 *
 * A CSI split across two PTY reads is left for xterm, the same as
 * ./terminalGlass does; the contrast floor covers that rare chunk.
 */
export function inkForLightPane(text: string): string {
  if (!text.includes("\x1b[")) return text;
  return text.replace(SGR, (full, params: string) => {
    if (!params.includes("38") && !params.includes("48")) return full;
    const next = rewriteLightInkSgr(params);
    return next === params ? full : `\x1b[${next}m`;
  });
}
