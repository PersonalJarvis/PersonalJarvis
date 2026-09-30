/**
 * The guided tour of the real app, as data.
 *
 * Each step points at one element that carries `data-tour="<anchor>"` — a
 * stable hook set for this purpose, never a CSS class or a test id (tests
 * rename those freely). A step may move the app before it points (`onEnter`)
 * or after the user moves on (`onExit`); the tour only ever NAVIGATES. It
 * never presses a control that could start something — the voice bar, for
 * one, opens a paid call, so the tour points at it and leaves the click to
 * the user.
 */
import type { MascotAction } from "@/components/MascotGigi";

/** What a step may do to the app. Every effect is plain navigation. */
export type TourEffect = "home-voice" | "open-agents" | "back-home";

export type TourPlacement = "right" | "left" | "below" | "above" | "inside";

export interface TourStep {
  /** Also the i18n key: `app_tour.steps.<id>`. */
  id: string;
  anchor: string;
  placement: TourPlacement;
  onEnter?: TourEffect;
  onExit?: TourEffect;
  /** Drop the step when its anchor never shows (e.g. a section this build lacks). */
  skipIfMissing?: boolean;
  mascot: MascotAction;
}

export const TOUR_STEPS: readonly TourStep[] = [
  { id: "voice", anchor: "voice-bar", placement: "below", onEnter: "home-voice", mascot: "wave" },
  { id: "new_chat", anchor: "new-chat", placement: "right", mascot: "look-right" },
  { id: "agents", anchor: "nav-agents", placement: "right", onExit: "open-agents", mascot: "look-left" },
  {
    id: "agents_world",
    anchor: "agents-page",
    placement: "inside",
    onEnter: "open-agents",
    onExit: "back-home",
    skipIfMissing: true,
    mascot: "jump",
  },
  { id: "voice_hub", anchor: "nav-dictation", placement: "right", onEnter: "back-home", mascot: "look-right" },
  { id: "artifacts", anchor: "nav-visualization", placement: "right", skipIfMissing: true, mascot: "look-left" },
  { id: "ide", anchor: "nav-agentic-ide", placement: "right", skipIfMissing: true, mascot: "spin" },
  { id: "plugins", anchor: "nav-plugins", placement: "right", skipIfMissing: true, mascot: "look-right" },
  { id: "settings", anchor: "settings", placement: "right", mascot: "look-left" },
  { id: "done", anchor: "voice-bar", placement: "below", onEnter: "home-voice", mascot: "jump" },
];

export function nextStepIndex(index: number): number | null {
  return index + 1 < TOUR_STEPS.length ? index + 1 : null;
}

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
