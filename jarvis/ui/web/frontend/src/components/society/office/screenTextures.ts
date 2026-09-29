/**
 * Monitor faces, drawn once on a 2D canvas and shared by every desk.
 *
 * The screen tells the agent's state at a glance, like the status pill:
 * code lines while working, a friendly face while idle, an amber prompt while
 * waiting for the person, a dark glass when paused or when the desk is empty.
 */
import { CanvasTexture, SRGBColorSpace, type Texture } from "three";
import type { AgentRunState } from "../data";

export type ScreenFace = AgentRunState | "empty";

const cache = new Map<ScreenFace, Texture | null>();

function draw(face: ScreenFace, ctx: CanvasRenderingContext2D, w: number, h: number): void {
  ctx.fillStyle = face === "paused" || face === "empty" ? "#15181e" : face === "waiting" ? "#2a2413" : "#1b2a44";
  ctx.fillRect(0, 0, w, h);
  if (face === "working") {
    const colours = ["#7dd3fc", "#a7f3d0", "#fcd34d", "#f9a8d4", "#c4b5fd"];
    for (let line = 0; line < 9; line += 1) {
      const indent = [0, 1, 1, 2, 2, 1, 0, 1, 2][line] * 10;
      const length = 30 + ((line * 37) % 60);
      ctx.fillStyle = colours[line % colours.length];
      ctx.fillRect(10 + indent, 9 + line * 12, length, 5);
    }
  } else if (face === "idle") {
    // The friendly "^ ^" face of an idle machine.
    ctx.strokeStyle = "#9cc8ff";
    ctx.lineWidth = 6;
    ctx.lineCap = "round";
    for (const cx of [w * 0.36, w * 0.64]) {
      ctx.beginPath();
      ctx.moveTo(cx - 9, h * 0.48);
      ctx.lineTo(cx, h * 0.38);
      ctx.lineTo(cx + 9, h * 0.48);
      ctx.stroke();
    }
    ctx.beginPath();
    ctx.arc(w / 2, h * 0.58, 12, 0.15 * Math.PI, 0.85 * Math.PI);
    ctx.stroke();
  } else if (face === "waiting") {
    ctx.fillStyle = "#fbbf24";
    for (const dx of [-18, 0, 18]) {
      ctx.beginPath();
      ctx.arc(w / 2 + dx, h / 2, 5, 0, Math.PI * 2);
      ctx.fill();
    }
  }
}

/** The shared texture for a face; null where no 2D canvas exists (tests, bare hosts). */
export function screenTexture(face: ScreenFace): Texture | null {
  if (cache.has(face)) return cache.get(face) ?? null;
  let texture: Texture | null = null;
  const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
  const ctx = canvas?.getContext("2d") ?? null;
  if (canvas && ctx) {
    canvas.width = 128;
    canvas.height = 128;
    draw(face, ctx, canvas.width, canvas.height);
    const made = new CanvasTexture(canvas);
    made.colorSpace = SRGBColorSpace;
    texture = made;
  }
  cache.set(face, texture);
  return texture;
}
