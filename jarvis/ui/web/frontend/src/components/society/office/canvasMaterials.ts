/**
 * Canvas-drawn textures and the materials that show them, drawn once per key
 * and shared by every prop, sign and floor that uses them.
 */
import { CanvasTexture, MeshStandardMaterial, SRGBColorSpace, type Texture } from "three";

const textureCache = new Map<string, Texture | null>();

/**
 * A canvas-drawn texture, drawn once per key and cached. Returns null where no
 * 2D canvas exists (SSR, tests without canvas); callers fall back to a colour.
 */
export function cachedCanvasTexture(
  key: string, width: number, height: number, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void,
): Texture | null {
  if (textureCache.has(key)) return textureCache.get(key) ?? null;
  const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas?.getContext("2d") ?? null;
  } catch {
    // jsdom without the canvas package throws "not implemented": no texture, the colour fallback is correct.
    ctx = null;
  }
  let texture: Texture | null = null;
  if (canvas && ctx) {
    canvas.width = width;
    canvas.height = height;
    draw(ctx, width, height);
    const made = new CanvasTexture(canvas);
    made.colorSpace = SRGBColorSpace;
    made.anisotropy = 4;
    texture = made;
  }
  textureCache.set(key, texture);
  return texture;
}

const fontWaiters = new Set<string>();

/**
 * Redraw a cached canvas texture once its web fonts have loaded. Canvas text
 * drawn before a face arrives keeps the fallback face for the whole session,
 * because each texture is drawn once.
 */
export function redrawWhenFontsLoad(
  key: string, texture: Texture | null, fonts: readonly string[], draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void,
): void {
  if (!texture || fontWaiters.has(key) || typeof document === "undefined" || !("fonts" in document)) return;
  if (fonts.every((font) => document.fonts.check(font))) return;
  fontWaiters.add(key);
  Promise.all(fonts.map((font) => document.fonts.load(font))).then(() => {
    const canvas = texture.image as HTMLCanvasElement | undefined;
    const ctx = canvas?.getContext?.("2d");
    if (!canvas || !ctx) return;
    draw(ctx, canvas.width, canvas.height);
    texture.needsUpdate = true;
  }, () => {
    // A face that fails to load leaves the fallback face already drawn, which stays legible.
  });
}

const materialCache = new Map<string, MeshStandardMaterial>();

/** A material showing a cached canvas texture; `glow` > 0 makes it self-lit like a screen. */
export function canvasMaterial(
  key: string, width: number, height: number, draw: (ctx: CanvasRenderingContext2D, w: number, h: number) => void,
  { glow = 0, fallback = "#ffffff", roughness = 0.6 }: { glow?: number; fallback?: string; roughness?: number } = {},
): MeshStandardMaterial {
  let material = materialCache.get(key);
  if (!material) {
    const map = cachedCanvasTexture(key, width, height, draw);
    material = new MeshStandardMaterial({
      color: map ? "#ffffff" : fallback, map, roughness,
      emissive: glow > 0 ? "#ffffff" : "#000000", emissiveMap: glow > 0 ? map : null, emissiveIntensity: glow,
    });
    materialCache.set(key, material);
  }
  return material;
}
