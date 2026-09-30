/**
 * Small hooks shared by the minimap, the compass bar and the full map:
 * theme and pixel-ratio change signals, element size, and a throttled
 * requestAnimationFrame loop.
 */
import { useEffect, useRef, useState, type RefObject } from "react";
import type { MinimapAgentState } from "./minimap";

/** What the maps need to know about an agent; `SocietyAgent` satisfies it. */
export interface OfficeMapAgent {
  agentId: string;
  name: string;
  state: MinimapAgentState;
  palette?: { primary?: string } | null;
}

/** Token colour of an agent: its palette's primary colour, or a neutral slate. */
export function agentColour(agent: OfficeMapAgent): string {
  const primary = agent.palette?.primary?.trim();
  return primary ? primary : "#7c8aa5";
}

function pixelRatio(): number {
  return typeof window !== "undefined" && window.devicePixelRatio > 0 ? window.devicePixelRatio : 1;
}

/** Bumps whenever light/dark mode may have changed: OS preference, or the app's class/data-theme on <html>. */
export function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1);
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    media?.addEventListener?.("change", bump);
    const observer = typeof MutationObserver === "function" ? new MutationObserver(bump) : null;
    observer?.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme", "style"] });
    return () => {
      media?.removeEventListener?.("change", bump);
      observer?.disconnect();
    };
  }, []);
  return version;
}

/** The device pixel ratio, refreshed when the window moves to another screen or zooms. */
export function usePixelRatio(): number {
  const [dpr, setDpr] = useState(pixelRatio);
  useEffect(() => {
    const check = () => setDpr((prev) => (prev === pixelRatio() ? prev : pixelRatio()));
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);
  return dpr;
}

/** CSS size of an element, rounded to whole pixels; {0, 0} until measured. */
export function useElementSize(ref: RefObject<HTMLElement>, enabled = true): { width: number; height: number } {
  const [size, setSize] = useState({ width: 0, height: 0 });
  useEffect(() => {
    const el = ref.current;
    if (!enabled || !el) return;
    const measure = () => {
      const rect = el.getBoundingClientRect();
      const next = { width: Math.round(rect.width), height: Math.round(rect.height) };
      setSize((prev) => (prev.width === next.width && prev.height === next.height ? prev : next));
    };
    measure();
    if (typeof ResizeObserver !== "function") {
      window.addEventListener("resize", measure);
      return () => window.removeEventListener("resize", measure);
    }
    const observer = new ResizeObserver(measure);
    observer.observe(el);
    return () => observer.disconnect();
  }, [ref, enabled]);
  return size;
}

/**
 * Calls `draw(now)` at most once per `frameMs` from requestAnimationFrame while
 * `enabled`. The latest `draw` is read through a ref, so a re-render never
 * restarts the loop.
 *
 * Deliberately NOT gated on `document.hidden`: the desktop WebView reports an
 * on-screen window as hidden for minutes at a time (see useCanvasAwake), which
 * froze the minimap and compass. requestAnimationFrame already stops by itself
 * for a window that is really hidden.
 */
export function useThrottledFrames(draw: (now: number) => void, frameMs: number, enabled: boolean): void {
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useEffect(() => {
    if (!enabled || typeof requestAnimationFrame !== "function") return;
    let last = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (now - last < frameMs) return;
      last = now;
      drawRef.current(now);
    };
    let raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [frameMs, enabled]);
}

/** A theme token (raw "H S% L%") as a CSS colour, or the fallback when the token is missing. */
export function tokenColour(style: CSSStyleDeclaration | null, name: string, fallback: string, alpha?: number): string {
  const raw = style?.getPropertyValue(name).trim() ?? "";
  if (!raw) return fallback;
  return alpha === undefined ? `hsl(${raw})` : `hsl(${raw} / ${alpha})`;
}

/** An offscreen canvas of `cssW × cssH` CSS px at `ratio`, or null without a 2D canvas (headless test DOM). */
export function offscreenLayer(cssW: number, cssH: number, ratio: number): { canvas: HTMLCanvasElement; ctx: CanvasRenderingContext2D; ratio: number } | null {
  if (typeof document === "undefined" || cssW <= 0 || cssH <= 0) return null;
  // Keep a layer under ~4k px per side even on very dense screens.
  const k = Math.max(0.5, Math.min(ratio, 4096 / Math.max(cssW, cssH)));
  const canvas = document.createElement("canvas");
  canvas.width = Math.max(1, Math.round(cssW * k));
  canvas.height = Math.max(1, Math.round(cssH * k));
  let ctx: CanvasRenderingContext2D | null = null;
  try {
    ctx = canvas.getContext("2d");
  } catch {
    // jsdom without the canvas package throws "not implemented": no picture, the chrome still renders.
    ctx = null;
  }
  if (!ctx) return null;
  ctx.setTransform(k, 0, 0, k, 0, 0);
  return { canvas, ctx, ratio: k };
}

/** The 2D context of a visible canvas, or null (headless test DOM). */
export function context2d(canvas: HTMLCanvasElement | null): CanvasRenderingContext2D | null {
  if (!canvas) return null;
  try {
    return canvas.getContext("2d");
  } catch {
    // jsdom without the canvas package: nothing to paint on.
    return null;
  }
}
