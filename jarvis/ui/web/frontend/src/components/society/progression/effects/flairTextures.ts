/**
 * The soft glow texture the level effects and the Level Hall share.
 * Drawn once per page on first use; three.js re-uploads them from their
 * canvas after a lost WebGL context, so they are never disposed.
 */
import { CanvasTexture, SRGBColorSpace, type Texture } from "three";

type Draw = (ctx: CanvasRenderingContext2D, size: number) => void;

const cache = new Map<string, Texture>();

function canvasTexture(key: string, size: number, draw: Draw, height = size): Texture {
  const hit = cache.get(key);
  if (hit) return hit;
  const canvas = document.createElement("canvas");
  canvas.width = size;
  canvas.height = height;
  const ctx = canvas.getContext("2d");
  if (ctx) draw(ctx, size);
  const texture = new CanvasTexture(canvas);
  texture.colorSpace = SRGBColorSpace;
  cache.set(key, texture);
  return texture;
}

export function glowTexture(): Texture {
  return canvasTexture("glow", 64, (ctx) => {
    const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 32);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.3, "rgba(255,255,255,0.55)");
    g.addColorStop(0.65, "rgba(255,255,255,0.14)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 64, 64);
  });
}
