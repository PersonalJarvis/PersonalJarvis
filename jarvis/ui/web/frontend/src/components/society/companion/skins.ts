/**
 * Companion designs ("skins"): a colour gradient with an optional looping
 * effect, worn instead of the flat companion colour. `companion.skin` in the
 * avatar JSON; `CompanionSkin` in `jarvis/society/companion.py` validates the
 * same shape (the shared fixture `tests/contract/fixtures/agent-companion.json`
 * pins both).
 *
 * The appearance keeps a plain `color` beside the skin, set to the design's
 * blended colour, so surfaces that know one colour (chat bubble tint, thinking
 * dots, accessory tints) still match the design.
 */
import { z } from "zod";

export const SKIN_PATTERNS = ["linear", "radial"] as const;
/** Effects drawn here. Saved ids stay open: an unknown one draws no effect. */
export const SKIN_EFFECTS = ["none", "stars", "shimmer", "flow", "glow", "sparkle"] as const;
export type SkinEffect = typeof SKIN_EFFECTS[number];

const HEX = /^#[0-9a-fA-F]{6}$/;
// eslint-disable-next-line no-control-regex
const PLAIN_TEXT = /^[^\x00-\x1f\x7f]*$/;

export const skinSchema = z.object({
  colors: z.array(z.string().regex(HEX)).min(2).max(4),
  pattern: z.enum(SKIN_PATTERNS).default("linear"),
  angle: z.number().int().min(0).max(359).default(135),
  effect: z.string().regex(/^[a-z]{1,20}$/).default("none"),
  name: z.string().max(40).regex(PLAIN_TEXT).default(""),
}).strict();
export type CompanionSkin = z.infer<typeof skinSchema>;

/** The effect to draw; an id from a newer build draws nothing. */
export function skinEffect(skin: CompanionSkin): SkinEffect {
  return (SKIN_EFFECTS as readonly string[]).includes(skin.effect) ? skin.effect as SkinEffect : "none";
}

export interface SkinPreset { id: string; skin: Omit<CompanionSkin, "name"> }

/** Ready-made designs; the dialog names them through `society.companion.design.presets.<id>`. */
export const SKIN_PRESETS: readonly SkinPreset[] = [
  { id: "galaxy", skin: { colors: ["#e05cc5", "#6a2fc2", "#160f3d"], pattern: "radial", angle: 135, effect: "stars" } },
  { id: "aurora", skin: { colors: ["#22e3a5", "#2bb3e0", "#8a5cf0"], pattern: "linear", angle: 160, effect: "flow" } },
  { id: "holo", skin: { colors: ["#ffc6ec", "#bdf3ff", "#d6c4ff", "#fff1b8"], pattern: "linear", angle: 120, effect: "shimmer" } },
  { id: "lava", skin: { colors: ["#ffd23f", "#ff5a1f", "#8c1010"], pattern: "radial", angle: 135, effect: "glow" } },
  { id: "neon", skin: { colors: ["#ff2fd6", "#2ff3ff"], pattern: "linear", angle: 90, effect: "glow" } },
  { id: "ocean", skin: { colors: ["#64e1ff", "#1f7ae0", "#0b2a6b"], pattern: "linear", angle: 180, effect: "flow" } },
  { id: "sunset", skin: { colors: ["#ffb347", "#ff5f6d", "#7b3fa0"], pattern: "linear", angle: 180, effect: "none" } },
  { id: "gold", skin: { colors: ["#fff1a8", "#e3a92b", "#8a5a12"], pattern: "linear", angle: 145, effect: "sparkle" } },
  { id: "frost", skin: { colors: ["#ffffff", "#bfe6ff", "#7aa8e8"], pattern: "linear", angle: 200, effect: "sparkle" } },
];

function rgb(hex: string): [number, number, number] {
  const n = parseInt(hex.slice(1), 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

function hex([r, g, b]: readonly number[]): string {
  return `#${[r, g, b].map(v => Math.round(Math.max(0, Math.min(255, v!))).toString(16).padStart(2, "0")).join("")}`;
}

/** Relative luminance, 0 (black) to 1 (white). */
export function luminance(color: string): number {
  const [r, g, b] = rgb(color).map(v => { const c = v / 255; return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4; });
  return 0.2126 * r! + 0.7152 * g! + 0.0722 * b!;
}

/** The one colour a design reads as: its stops averaged in linear light. */
export function skinBlend(colors: readonly string[]): string {
  const lin = colors.map(c => rgb(c).map(v => (v / 255) ** 2.2));
  const mean = [0, 1, 2].map(i => lin.reduce((sum, c) => sum + c[i]!, 0) / lin.length);
  return hex(mean.map(v => 255 * v ** (1 / 2.2)));
}

/** The brightest stop: what a glow or a sparkle shines in. */
export function skinHighlight(colors: readonly string[]): string {
  return [...colors].sort((a, b) => luminance(b) - luminance(a))[0]!;
}

/** A CSS background for a design's swatch. */
export function skinCss(skin: Pick<CompanionSkin, "colors" | "pattern" | "angle">): string {
  return skin.pattern === "radial"
    ? `radial-gradient(circle at 38% 34%, ${skin.colors.join(", ")})`
    : `linear-gradient(${skin.angle}deg, ${skin.colors.join(", ")})`;
}

/** Gradient end points in the unit box for a CSS angle (0 = up, 90 = right). */
export function angleVector(angle: number): [number, number, number, number] {
  const rad = angle * Math.PI / 180;
  const dx = Math.sin(rad) / 2;
  const dy = -Math.cos(rad) / 2;
  const r = (v: number) => Math.round(v * 1000) / 1000;
  return [r(0.5 - dx), r(0.5 - dy), r(0.5 + dx), r(0.5 + dy)];
}

/** Two designs look the same when colours, layout and effect match (names may differ). */
export function sameSkin(a: CompanionSkin | undefined, b: CompanionSkin | undefined): boolean {
  if (!a || !b) return a === b;
  return a.pattern === b.pattern && a.angle === b.angle && a.effect === b.effect
    && a.colors.length === b.colors.length && a.colors.every((c, i) => c.toLowerCase() === b.colors[i]!.toLowerCase());
}

// ---------------------------------------------------------------- import / export

const CODE_PREFIX = "jarvis-design:";

/** A short text anyone can paste into their own look dialog. */
export function encodeDesignCode(skin: CompanionSkin): string {
  const json = JSON.stringify({ v: 1, ...skin });
  const bytes = new TextEncoder().encode(json);
  let binary = "";
  bytes.forEach(b => { binary += String.fromCharCode(b); });
  return CODE_PREFIX + btoa(binary).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}

function decodeDesignCode(text: string): unknown {
  const body = text.slice(CODE_PREFIX.length).trim().replace(/-/g, "+").replace(/_/g, "/");
  const binary = atob(body + "=".repeat((4 - body.length % 4) % 4));
  return JSON.parse(new TextDecoder().decode(Uint8Array.from(binary, c => c.charCodeAt(0))));
}

function fromObject(value: unknown): CompanionSkin | null {
  if (!value || typeof value !== "object") return null;
  const { v: _version, ...rest } = value as Record<string, unknown>;
  // A whole exported companion (or agent template) carries the skin inside.
  const inner = (rest.companion as Record<string, unknown> | undefined)?.skin ?? rest.skin ?? rest;
  const parsed = skinSchema.safeParse(inner);
  return parsed.success ? { ...parsed.data, colors: parsed.data.colors.map(c => c.toLowerCase()) } : null;
}

const CSS_COLOR = /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3,4})\b|rgba?\(\s*\d{1,3}\s*,\s*\d{1,3}\s*,\s*\d{1,3}[^)]*\)/g;
const CSS_SIDES: Record<string, number> = {
  "to top": 0, "to top right": 45, "to right top": 45, "to right": 90, "to bottom right": 135, "to right bottom": 135,
  "to bottom": 180, "to bottom left": 225, "to left bottom": 225, "to left": 270, "to top left": 315, "to left top": 315,
};

function cssColorHex(token: string): string {
  if (token.startsWith("#")) {
    const h = token.slice(1);
    return `#${(h.length <= 4 ? h.slice(0, 3).split("").map(c => c + c).join("") : h.slice(0, 6))}`.toLowerCase();
  }
  return hex(token.match(/\d{1,3}/g)!.slice(0, 3).map(Number));
}

/** Up to four stops spread evenly over a longer list. */
function spread(colors: string[]): string[] {
  if (colors.length <= 4) return colors;
  return [0, 1, 2, 3].map(i => colors[Math.round(i * (colors.length - 1) / 3)]!);
}

function fromCss(text: string): CompanionSkin | null {
  const match = /(linear|radial|conic)-gradient\s*\(/i.exec(text);
  if (!match) return null;
  const colors = spread([...text.slice(match.index).matchAll(CSS_COLOR)].map(m => cssColorHex(m[0])));
  if (colors.length < 2) return null;
  const body = text.slice(match.index + match[0].length).toLowerCase();
  const degrees = /(-?\d+(?:\.\d+)?)deg/.exec(body);
  const side = Object.keys(CSS_SIDES).sort((a, b) => b.length - a.length).find(key => body.startsWith(key));
  const angle = degrees ? ((Math.round(Number(degrees[1])) % 360) + 360) % 360 : side ? CSS_SIDES[side]! : 180;
  return { colors, pattern: match[1]!.toLowerCase() === "radial" ? "radial" : "linear", angle, effect: "none", name: "" };
}

/**
 * Reads whatever a person pastes: a design code, an exported design or
 * companion as JSON, a CSS gradient from any gradient site, or just a list of
 * hex colours. Null when nothing usable is in it.
 */
export function parseDesignImport(text: string): CompanionSkin | null {
  const trimmed = text.trim();
  if (!trimmed) return null;
  try {
    if (trimmed.startsWith(CODE_PREFIX)) return fromObject(decodeDesignCode(trimmed));
    if (trimmed.startsWith("{")) return fromObject(JSON.parse(trimmed));
  } catch {
    return null; // A cut-off code or broken JSON: nothing to import, the dialog says so.
  }
  const css = fromCss(trimmed);
  if (css) return css;
  const colors = spread([...trimmed.matchAll(CSS_COLOR)].map(m => cssColorHex(m[0])));
  return colors.length >= 2 ? { colors, pattern: "linear", angle: 135, effect: "none", name: "" } : null;
}

// ---------------------------------------------------------------- from a picture

/**
 * The dominant, clearly different colours of a picture's pixels, darkest
 * last. Buckets the pixels by colour, then takes the most common buckets that
 * are far enough apart, so a galaxy wallpaper gives its violet, its pink and
 * its night blue rather than three shades of black.
 */
export function paletteFromPixels(data: Uint8ClampedArray, count = 3): string[] {
  const buckets = new Map<number, { n: number; r: number; g: number; b: number }>();
  for (let i = 0; i + 3 < data.length; i += 4) {
    if (data[i + 3]! < 128) continue;
    const r = data[i]!, g = data[i + 1]!, b = data[i + 2]!;
    const key = (r >> 4) << 8 | (g >> 4) << 4 | (b >> 4);
    const bucket = buckets.get(key) ?? { n: 0, r: 0, g: 0, b: 0 };
    bucket.n++; bucket.r += r; bucket.g += g; bucket.b += b;
    buckets.set(key, bucket);
  }
  const ranked = [...buckets.values()]
    .map(b => ({ n: b.n, c: [b.r / b.n, b.g / b.n, b.b / b.n] as const }))
    // Saturated colours carry a picture's character; weigh them above greys.
    .map(b => ({ ...b, score: b.n * (1 + 2 * (Math.max(...b.c) - Math.min(...b.c)) / 255) }))
    .sort((a, b) => b.score - a.score);
  const picked: (readonly number[])[] = [];
  for (const minDistance of [90, 60, 30, 0]) {
    for (const { c } of ranked) {
      if (picked.length >= count) break;
      if (picked.every(p => Math.hypot(p[0]! - c[0], p[1]! - c[1], p[2]! - c[2]) >= minDistance) && !picked.includes(c)) picked.push(c);
    }
    if (picked.length >= count) break;
  }
  return picked.map(hex).sort((a, b) => luminance(b) - luminance(a));
}

/** A design from a picture file: its palette, and stars when it is a dark sky. */
export async function skinFromImage(file: Blob): Promise<CompanionSkin | null> {
  const bitmap = await createImageBitmap(file);
  try {
    const canvas = document.createElement("canvas");
    canvas.width = 64; canvas.height = 64;
    const context = canvas.getContext("2d", { willReadFrequently: true });
    if (!context) return null;
    context.drawImage(bitmap, 0, 0, 64, 64);
    const colors = paletteFromPixels(context.getImageData(0, 0, 64, 64).data);
    if (colors.length < 2) return null;
    const dark = colors.reduce((sum, c) => sum + luminance(c), 0) / colors.length < 0.12;
    return { colors, pattern: dark ? "radial" : "linear", angle: 160, effect: dark ? "stars" : "none", name: "" };
  } finally {
    bitmap.close();
  }
}
