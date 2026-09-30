import { motion, useReducedMotion } from "framer-motion";
import { useEffect, useLayoutEffect, useRef, useState, type ReactNode } from "react";
import { createPortal } from "react-dom";
import { TOUR_LAYER_ATTR } from "../tourEvents";
import { EASE_OUT } from "../ui";
import { cutout, padded, placeCard, type Rect, type TourPlacement } from "./tourSteps";

const CARD_W = 320;

function useViewport(): { w: number; h: number } {
  const [view, setView] = useState(() => ({ w: window.innerWidth, h: window.innerHeight }));
  useEffect(() => {
    const onResize = () => setView({ w: window.innerWidth, h: window.innerHeight });
    window.addEventListener("resize", onResize);
    return () => window.removeEventListener("resize", onResize);
  }, []);
  return view;
}

/**
 * The tour's stage: the window dimmed, a hole over the element being shown,
 * a glowing ring around that hole, and the tour card beside it.
 *
 * On the first frame the hole is the whole window and then shrinks onto the
 * element, so the dark visibly closes in; between steps hole, ring and card
 * all GLIDE to the next place instead of popping. The dim never takes a
 * click (`pointer-events: none`) — the app stays usable, and clicking the
 * highlighted element is the same as pressing Next. Only the card is live.
 *
 * Sits at z-110: over every section, dock and dialog, under the window's
 * caption bar (z-120) so the frameless window can still be moved and closed.
 */
export function Spotlight({
  rect,
  placement,
  children,
  blocking = false,
  cardWidth = CARD_W,
}: {
  rect: Rect | null;
  placement: TourPlacement;
  children: ReactNode;
  /**
   * The dim takes clicks: only the hole (the element being set up) and the
   * card stay usable. The setup steps use it so nothing else in the app can
   * be pressed before the step is done; the app tour leaves the app free.
   */
  blocking?: boolean;
  cardWidth?: number;
}) {
  const view = useViewport();
  const reduced = useReducedMotion() ?? false;
  const [settled, setSettled] = useState(reduced);
  const cardRef = useRef<HTMLDivElement>(null);
  const [cardH, setCardH] = useState(160);

  useEffect(() => {
    if (settled) return;
    let inner = 0;
    const outer = requestAnimationFrame(() => {
      inner = requestAnimationFrame(() => setSettled(true));
    });
    return () => {
      cancelAnimationFrame(outer);
      cancelAnimationFrame(inner);
    };
  }, [settled]);

  useLayoutEffect(() => {
    const el = cardRef.current;
    if (!el) return;
    const measure = () => setCardH(el.offsetHeight || 160);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(el);
    return () => ro.disconnect();
  }, []);

  const hole = rect ? padded(rect) : null;
  const whole: Rect = { x: 0, y: 0, w: view.w, h: view.h };
  // No element (centred card): the hole collapses to a point in the middle,
  // so the same clip-path shape still animates.
  const point: Rect = { x: view.w / 2, y: view.h / 2, w: 0, h: 0 };
  const clip = cutout(!settled ? whole : hole ?? point);
  const width = Math.min(cardWidth, view.w - 24);
  const pos = placeCard(hole, placement, { w: width, h: cardH }, view);
  const glide = reduced ? "none" : "clip-path 320ms cubic-bezier(0.22,1,0.36,1)";
  const ringGlide = reduced
    ? "none"
    : "transform 320ms cubic-bezier(0.22,1,0.36,1), width 320ms cubic-bezier(0.22,1,0.36,1), height 320ms cubic-bezier(0.22,1,0.36,1)";

  return createPortal(
    <div className="pointer-events-none fixed inset-0 z-[110]" data-testid="tour-layer" {...{ [TOUR_LAYER_ATTR]: "" }}>
      <div
        aria-hidden
        className={blocking ? "pointer-events-auto absolute inset-0 bg-scrim/70" : "absolute inset-0 bg-scrim/60"}
        style={{ clipPath: clip, WebkitClipPath: clip, transition: glide }}
        data-testid="tour-dim"
      />
      {hole && settled && (
        <div
          aria-hidden
          className="absolute left-0 top-0 rounded-[14px]"
          data-testid="tour-ring"
          style={{
            width: hole.w,
            height: hole.h,
            transform: `translate(${hole.x}px, ${hole.y}px)`,
            transition: ringGlide,
            boxShadow:
              "0 0 0 2px hsl(var(--accent)), 0 0 0 6px rgb(var(--accent-rgb) / 0.22), 0 0 36px 6px rgb(var(--accent-rgb) / 0.28)",
          }}
        />
      )}
      <motion.div
        ref={cardRef}
        className="pointer-events-auto absolute left-0 top-0"
        style={{ width }}
        initial={false}
        animate={{ x: pos.x, y: pos.y }}
        transition={reduced ? { duration: 0 } : { duration: 0.32, ease: EASE_OUT }}
        data-tour-card=""
      >
        <motion.div
          initial={reduced ? false : { opacity: 0, scale: 0.94 }}
          animate={{ opacity: 1, scale: 1 }}
          transition={{ duration: 0.24, ease: EASE_OUT, delay: reduced ? 0 : 0.12 }}
        >
          {children}
        </motion.div>
      </motion.div>
    </div>,
    document.body,
  );
}
