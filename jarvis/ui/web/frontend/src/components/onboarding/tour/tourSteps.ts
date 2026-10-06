/**
 * Geometry for the guide's spotlight: the dim with a hole over one real
 * element, the ring around it, and where the speech bubble or card sits.
 *
 * Every element the guide points at carries `data-tour="<anchor>"` — a
 * stable hook set for this purpose, never a CSS class or a test id (tests
 * rename those freely).
 */

export type TourPlacement = "right" | "left" | "below" | "above" | "inside";

export interface Rect {
  x: number;
  y: number;
  w: number;
  h: number;
}

/** Space kept between the highlighted element and the ring around it. */
export const PAD = 8;

/**
 * The dim layer's clip path: the whole window minus a hole over `r`. Both
 * shapes have the same number of points, so a hole that starts as the whole
 * window and shrinks onto the element is a smooth transition — the dark
 * closes in on what is being shown.
 */
export function cutout(r: Rect): string {
  const x1 = Math.round(r.x);
  const y1 = Math.round(r.y);
  const x2 = Math.round(r.x + r.w);
  const y2 = Math.round(r.y + r.h);
  return `polygon(evenodd, 0 0, 100% 0, 100% 100%, 0 100%, 0 0, ${x1}px ${y1}px, ${x1}px ${y2}px, ${x2}px ${y2}px, ${x2}px ${y1}px, ${x1}px ${y1}px)`;
}

/** The anchor's rect grown by the ring padding. */
export function padded(r: Rect, pad = PAD): Rect {
  return { x: r.x - pad, y: r.y - pad, w: r.w + pad * 2, h: r.h + pad * 2 };
}

/**
 * Keep a highlight rect inside the window, `margin` from every edge. An
 * element that fills the window (a whole page) would otherwise have its
 * padded ring drawn off-screen, leaving no visible frame at all; clamped,
 * the ring sits just inside the window edge instead.
 */
export function fitToView(r: Rect, view: { w: number; h: number }, margin = 6): Rect {
  const x1 = Math.max(r.x, margin);
  const y1 = Math.max(r.y, margin);
  const x2 = Math.min(r.x + r.w, view.w - margin);
  const y2 = Math.min(r.y + r.h, view.h - margin);
  return { x: x1, y: y1, w: Math.max(0, x2 - x1), h: Math.max(0, y2 - y1) };
}

/**
 * Where the tour card goes for a highlighted rect, kept fully inside the
 * window (`margin` from every edge). `inside` sits in the element's top-right
 * corner — for a whole page. With no rect the card is centred.
 */
export function placeCard(
  rect: Rect | null,
  placement: TourPlacement,
  card: { w: number; h: number },
  view: { w: number; h: number },
  gap = 14,
  margin = 12,
): { x: number; y: number } {
  const clampX = (x: number) => Math.min(Math.max(x, margin), Math.max(margin, view.w - card.w - margin));
  const clampY = (y: number) => Math.min(Math.max(y, margin), Math.max(margin, view.h - card.h - margin));
  if (!rect) return { x: clampX((view.w - card.w) / 2), y: clampY((view.h - card.h) / 2) };
  switch (placement) {
    case "right": {
      const x = rect.x + rect.w + gap;
      // No room on the right: fall back below the element.
      if (x + card.w + margin > view.w) {
        return { x: clampX(rect.x), y: clampY(rect.y + rect.h + gap) };
      }
      return { x: clampX(x), y: clampY(rect.y + rect.h / 2 - card.h / 2) };
    }
    case "left": {
      const x = rect.x - gap - card.w;
      // No room on the left: lie over the element's left edge instead.
      return { x: clampX(x < margin ? rect.x + 16 : x), y: clampY(rect.y + 24) };
    }
    case "below": {
      const y = rect.y + rect.h + gap;
      if (y + card.h + margin > view.h) {
        return { x: clampX(rect.x + rect.w / 2 - card.w / 2), y: clampY(rect.y - gap - card.h) };
      }
      return { x: clampX(rect.x + rect.w / 2 - card.w / 2), y: clampY(y) };
    }
    case "above":
      return { x: clampX(rect.x + rect.w / 2 - card.w / 2), y: clampY(rect.y - gap - card.h) };
    case "inside":
      return { x: clampX(rect.x + rect.w - card.w - 24), y: clampY(rect.y + 24) };
  }
}
