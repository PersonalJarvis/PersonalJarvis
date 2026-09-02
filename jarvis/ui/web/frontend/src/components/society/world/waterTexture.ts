/**
 * A tiny pixel-art water tile, drawn on a canvas at runtime: flat surface with
 * a few lighter ripple strokes and rare foam dots. Nearest-filtered and
 * repeated over the sea plane, then scrolled slowly — the classic animated
 * pixel water. No asset file, no network.
 */
import { CanvasTexture, NearestFilter, RepeatWrapping, SRGBColorSpace } from "three";

import { WATER } from "./worldPalette";
import { hash2 } from "./islandLayout";

/** Texels per tile side. One tile of texture covers `WATER_TILE_M` metres. */
export const WATER_TEXELS = 32;
export const WATER_TILE_M = 8;

export function makeWaterTexture(): CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const canvas = document.createElement("canvas");
  canvas.width = WATER_TEXELS;
  canvas.height = WATER_TEXELS;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  ctx.fillStyle = WATER.surface;
  ctx.fillRect(0, 0, WATER_TEXELS, WATER_TEXELS);
  // Darker patches so the sea is not one flat sheet.
  ctx.fillStyle = WATER.deep;
  for (let i = 0; i < 14; i++) {
    const x = Math.floor(hash2(i, 1, 7) * WATER_TEXELS);
    const y = Math.floor(hash2(i, 2, 7) * WATER_TEXELS);
    const w = 3 + Math.floor(hash2(i, 3, 7) * 5);
    ctx.fillRect(x, y, w, 1);
  }
  // Ripple strokes: short horizontal light lines.
  ctx.fillStyle = WATER.ripple;
  for (let i = 0; i < 10; i++) {
    const x = Math.floor(hash2(i, 4, 7) * WATER_TEXELS);
    const y = Math.floor(hash2(i, 5, 7) * WATER_TEXELS);
    const w = 2 + Math.floor(hash2(i, 6, 7) * 4);
    ctx.fillRect(x, y, w, 1);
  }
  // Foam: single bright pixels.
  ctx.fillStyle = WATER.foam;
  for (let i = 0; i < 4; i++) {
    const x = Math.floor(hash2(i, 8, 7) * WATER_TEXELS);
    const y = Math.floor(hash2(i, 9, 7) * WATER_TEXELS);
    ctx.fillRect(x, y, 1, 1);
  }
  const tex = new CanvasTexture(canvas);
  tex.magFilter = NearestFilter;
  tex.minFilter = NearestFilter;
  tex.generateMipmaps = false;
  tex.wrapS = RepeatWrapping;
  tex.wrapT = RepeatWrapping;
  tex.colorSpace = SRGBColorSpace;
  return tex;
}
