/**
 * Service ribbons and medals: the striped silk bars worn above the left
 * breast pocket and the full-size medals hung from them. One table drives
 * the SVG icons, the canvas textures on the 3D ribbons and the medal discs,
 * so a ribbon reads the same in the studio, on a figure and in the hall.
 *
 * Stripes are listed left to right as [colour, width]; widths are relative.
 */

export interface RibbonSpec {
  id: string;
  stripes: readonly (readonly [string, number])[];
  /** The medal hung from this ribbon: its metal and shape. */
  medal: { metal: "bronze" | "gold" | "silver"; shape: "disc" | "star" | "cross" };
}

const R = "#b3202a", W = "#f4f1e8", B = "#1f3b7a", Y = "#e9b928", G = "#1e6b3a", K = "#1a1a1a", L = "#5b9bd5", O = "#d8752b";

/** In order of precedence, highest first: the rack fills from the top left. */
export const RIBBONS: readonly RibbonSpec[] = [
  { id: "distinguished", stripes: [[B, 3], [W, 1], [R, 6], [W, 1], [B, 3]], medal: { metal: "gold", shape: "star" } },
  { id: "meritorious", stripes: [[R, 2], [W, 1], [R, 6], [W, 1], [R, 2]], medal: { metal: "bronze", shape: "star" } },
  { id: "commendation", stripes: [[W, 1], [G, 3], [W, 1], [G, 2], [W, 1], [G, 3], [W, 1]], medal: { metal: "bronze", shape: "cross" } },
  { id: "achievement", stripes: [[W, 1], [G, 2], [W, 1], [B, 2], [W, 1], [G, 2], [W, 1]], medal: { metal: "bronze", shape: "disc" } },
  { id: "good_conduct", stripes: [[R, 2], [W, 0.6], [R, 2], [W, 0.6], [R, 2], [W, 0.6], [R, 2]], medal: { metal: "bronze", shape: "disc" } },
  { id: "national_defense", stripes: [[R, 2], [W, 0.6], [B, 0.6], [W, 0.6], [R, 0.6], [Y, 3], [R, 0.6], [W, 0.6], [B, 0.6], [W, 0.6], [R, 2]], medal: { metal: "bronze", shape: "disc" } },
  { id: "service", stripes: [[R, 1], [O, 1], [Y, 1], [G, 1], [L, 1], [B, 1], [K, 0.6], [B, 1], [L, 1], [G, 1], [Y, 1], [O, 1], [R, 1]], medal: { metal: "bronze", shape: "disc" } },
  { id: "overseas", stripes: [[B, 1], [Y, 1], [G, 1], [Y, 2], [R, 1], [Y, 2], [G, 1], [Y, 1], [B, 1]], medal: { metal: "bronze", shape: "disc" } },
  { id: "leadership", stripes: [[Y, 1], [B, 1], [W, 1], [R, 3], [W, 1], [B, 1], [Y, 1]], medal: { metal: "silver", shape: "disc" } },
];

/** A ribbon's stripes as [x, width, colour] spans across a bar `width` wide. */
export function ribbonSpans(spec: RibbonSpec, width: number): [number, number, string][] {
  const total = spec.stripes.reduce((sum, [, w]) => sum + w, 0);
  let x = 0;
  return spec.stripes.map(([colour, w]) => {
    const span: [number, number, string] = [x, (w / total) * width, colour];
    x += (w / total) * width;
    return span;
  });
}

/** Paints a ribbon onto a canvas, with a soft silk sheen and a dark edge. */
export function paintRibbon(ctx: CanvasRenderingContext2D, spec: RibbonSpec, w: number, h: number): void {
  for (const [x, sw, colour] of ribbonSpans(spec, w)) {
    ctx.fillStyle = colour;
    ctx.fillRect(Math.floor(x), 0, Math.ceil(sw) + 1, h);
  }
  const sheen = ctx.createLinearGradient(0, 0, 0, h);
  sheen.addColorStop(0, "rgba(255,255,255,0.28)");
  sheen.addColorStop(0.45, "rgba(255,255,255,0)");
  sheen.addColorStop(1, "rgba(0,0,0,0.25)");
  ctx.fillStyle = sheen;
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "rgba(0,0,0,0.45)";
  ctx.lineWidth = Math.max(1, h * 0.06);
  ctx.strokeRect(0, 0, w, h);
}

export const MEDAL_METALS: Record<RibbonSpec["medal"]["metal"], { face: string; edge: string }> = {
  bronze: { face: "#b9833f", edge: "#6d4a1e" },
  gold: { face: "#e2b54e", edge: "#80601a" },
  silver: { face: "#d6dbe2", edge: "#6f7784" },
};

/** How many ribbons each decoration wears, rows of three like a real rack. */
export const RACK_SIZE = { decoration_ribbon_bar: 3, decoration_ribbon_rack: 9, decoration_aiguillette: 9, decoration_medals: 9 } as const;
/** The full-size medals worn with the top decoration: the five highest. */
export const MEDAL_COUNT = 5;
