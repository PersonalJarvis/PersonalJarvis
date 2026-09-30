/**
 * The CLI marks the desk monitors draw in their title bar, as bitmaps.
 *
 * A canvas cannot use AgentMark (a DOM component), so the same shipped brand
 * file is loaded once per agent and ink, and rendered the way AgentMark shows
 * it: an `ink` silhouette tinted with the pane's ink, a `dark` lockup on its
 * dark tile, an `any` lockup as it is. Until a mark has loaded — or when an
 * agent has none — the monitor draws the terminal glyph instead.
 */
import { useEffect, useState } from "react";
import { agentLogoAsset } from "@/components/agentic/AgentMark";

const SIZE = 64;
const marks = new Map<string, HTMLCanvasElement>();
const pending = new Map<string, Promise<HTMLCanvasElement | null>>();
const failedUrls = new Set<string>();

function render(image: HTMLImageElement, ground: "ink" | "dark" | "any", ink: string): HTMLCanvasElement | null {
  const canvas = document.createElement("canvas");
  canvas.width = SIZE; canvas.height = SIZE;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  if (ground === "dark") {
    ctx.fillStyle = "#18181b";
    ctx.beginPath(); ctx.roundRect(0, 0, SIZE, SIZE, 12); ctx.fill();
    ctx.drawImage(image, 8, 8, SIZE - 16, SIZE - 16);
    return canvas;
  }
  ctx.drawImage(image, 0, 0, SIZE, SIZE);
  if (ground === "ink") {
    // The silhouette takes the pane's ink, the way AgentMark masks it over the text colour.
    ctx.globalCompositeOperation = "source-in";
    ctx.fillStyle = ink;
    ctx.fillRect(0, 0, SIZE, SIZE);
  }
  return canvas;
}

function load(agent: string, ink: string): Promise<HTMLCanvasElement | null> {
  const asset = agentLogoAsset(agent);
  if (!asset || failedUrls.has(asset.url) || typeof document === "undefined") return Promise.resolve(null);
  const key = `${agent}|${ink}`;
  const known = pending.get(key);
  if (known) return known;
  const promise = new Promise<HTMLCanvasElement | null>((resolve) => {
    const image = new Image(SIZE, SIZE);
    image.onload = () => {
      const canvas = render(image, asset.ground, ink);
      if (canvas) marks.set(key, canvas);
      resolve(canvas);
    };
    image.onerror = () => {
      // The monitor keeps its terminal glyph; one line per broken file, never a retry loop.
      failedUrls.add(asset.url);
      console.warn(`Office monitor: the ${agent} mark could not be loaded from ${asset.url}`);
      resolve(null);
    };
    image.src = asset.url;
  });
  pending.set(key, promise);
  return promise;
}

/** The agent's mark tinted for `ink`, or null until it has loaded (or when there is none). */
export function useAgentMark(agent: string, ink: string): HTMLCanvasElement | null {
  const key = `${agent}|${ink}`;
  const [mark, setMark] = useState<HTMLCanvasElement | null>(() => marks.get(key) ?? null);
  useEffect(() => {
    const ready = marks.get(key);
    if (ready) { setMark(ready); return; }
    let alive = true;
    setMark(null);
    void load(agent, ink).then((canvas) => { if (alive) setMark(canvas); });
    return () => { alive = false; };
  }, [agent, ink, key]);
  return mark;
}
