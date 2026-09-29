/**
 * The office canvas's pixel ratio under a fixed pixel budget.
 *
 * Full resolution is cheap in the narrow side panel and ruinous when the same
 * scene fills a 4K window: shadows and MSAA are paid per pixel, so the frame
 * rate falls with the area. Holding the drawn pixel count near a budget keeps a
 * large view as smooth as a small one; a small view still gets the full ratio.
 */
import { useEffect, useState, type RefObject } from "react";

/** Physical pixels the scene is drawn at, at most (about 2.5 x 1.3 k). */
export const PIXEL_BUDGET = 3_200_000;
/** Never above this ratio, whatever the screen offers. */
export const MAX_DPR = 1.75;
/** Never below one CSS pixel per drawn pixel: below that text on the monitors blurs. */
const MIN_DPR = 1;

/** The ratio for a `width` x `height` CSS-pixel view on a `device`-ratio screen. Pure. */
export function budgetDpr(width: number, height: number, device: number): number {
  const top = Math.max(MIN_DPR, Math.min(MAX_DPR, device || 1));
  const area = width * height;
  if (area <= 0) return top;
  const fit = Math.sqrt(PIXEL_BUDGET / area);
  // Quarter steps: a window drag never reallocates the drawing buffer per frame.
  const stepped = Math.floor(fit * 4) / 4;
  return Math.max(MIN_DPR, Math.min(top, stepped));
}

export function useDprBudget(host: RefObject<HTMLElement | null>): number {
  const device = typeof window === "undefined" ? 1 : window.devicePixelRatio;
  const [dpr, setDpr] = useState(() => Math.min(MAX_DPR, Math.max(MIN_DPR, device)));
  useEffect(() => {
    const node = host.current;
    if (!node || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(([entry]) => {
      const { width, height } = entry.contentRect;
      setDpr(budgetDpr(width, height, window.devicePixelRatio));
    });
    observer.observe(node);
    return () => observer.disconnect();
  }, [host]);
  return dpr;
}
