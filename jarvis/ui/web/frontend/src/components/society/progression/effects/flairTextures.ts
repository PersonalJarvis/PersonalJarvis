/**
 * The few canvas textures every level effect shares: a soft glow, a
 * four-point star, a rune circle, a footprint and a vertical light beam.
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

export function starTexture(): Texture {
  return canvasTexture("star", 64, (ctx) => {
    const g = ctx.createRadialGradient(32, 32, 0, 32, 32, 12);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 64, 64);
    ctx.fillStyle = "rgba(255,255,255,0.95)";
    for (const [w, h] of [[3, 30], [30, 3]] as const) {
      ctx.beginPath();
      ctx.ellipse(32, 32, w, h, 0, 0, Math.PI * 2);
      ctx.fill();
    }
  });
}

/** A ring of rune ticks and dots on a thin circle, for the rune aura. */
export function runeTexture(): Texture {
  return canvasTexture("runes", 256, (ctx) => {
    ctx.translate(128, 128);
    ctx.strokeStyle = "rgba(255,255,255,0.95)";
    ctx.lineWidth = 3;
    ctx.beginPath(); ctx.arc(0, 0, 118, 0, Math.PI * 2); ctx.stroke();
    ctx.lineWidth = 1.5;
    ctx.beginPath(); ctx.arc(0, 0, 92, 0, Math.PI * 2); ctx.stroke();
    ctx.lineWidth = 3;
    for (let i = 0; i < 16; i++) {
      ctx.save();
      ctx.rotate((i / 16) * Math.PI * 2);
      ctx.translate(0, -105);
      ctx.beginPath();
      // A small glyph per slot: a stroke, a hook or a triangle in turn.
      if (i % 3 === 0) { ctx.moveTo(-5, -6); ctx.lineTo(5, 6); ctx.moveTo(5, -6); ctx.lineTo(0, 0); }
      else if (i % 3 === 1) { ctx.moveTo(0, -7); ctx.lineTo(0, 7); ctx.moveTo(0, -2); ctx.lineTo(6, -6); }
      else { ctx.moveTo(-6, 6); ctx.lineTo(0, -7); ctx.lineTo(6, 6); ctx.closePath(); }
      ctx.stroke();
      ctx.restore();
    }
  });
}

/** A rounded sole with four toe dots; drawn pointing up (+v). */
export function footprintTexture(): Texture {
  return canvasTexture("foot", 64, (ctx) => {
    ctx.fillStyle = "rgba(255,255,255,1)";
    ctx.beginPath(); ctx.ellipse(32, 38, 11, 17, 0, 0, Math.PI * 2); ctx.fill();
    for (const [x, y, r] of [[22, 15, 4], [29, 11, 4.2], [36, 11, 4], [42, 15, 3.6]] as const) {
      ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
    }
  });
}

/** Bright at the floor, fading out towards the top: the level-up column. */
export function beamTexture(): Texture {
  return canvasTexture("beam", 16, (ctx) => {
    const g = ctx.createLinearGradient(0, 128, 0, 0);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(0.35, "rgba(255,255,255,0.5)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 16, 128);
  }, 128);
}
