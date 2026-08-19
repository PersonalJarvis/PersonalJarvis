import {
  createContext,
  useContext,
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type CSSProperties,
  type ReactNode,
} from "react";
import { motion, useReducedMotion } from "framer-motion";
import {
  CARD_POWER_ON,
  revealDelayMs,
  revealWipeFor,
  type BoardSlot,
  type WipeDirection,
} from "@/lib/deckStandby";
import { cn } from "@/lib/utils";

/**
 * How a board instrument powers on when the board takes over from the standby
 * stage — one beat of the launch (`lib/deckStandby.ts::HANDOFF`), timed from
 * the centre outward (`revealDelayMs`) so the board assembles around the orb
 * as the shockwave reaches each slot:
 *
 *   1. a targeting frame draws itself around the slot — a hairline rectangle
 *      drawn from the corner nearest the orb, four brackets sliding in to the
 *      corners;
 *   2. a beat later the card MOUNTS and its content wipes in AWAY from the
 *      orb — the centre column top-down, the left cards from their right
 *      edge, the right cards from their left (`revealWipeFor`) — with a gold
 *      scan bar riding the front and a two-frame flicker, the way a hologram
 *      settles;
 *   3. the wipe lands with a lock flash, and the ghost frame fades once the
 *      card's own frame underneath is there. The slot is now POWERED
 *      (`useDeckSlotPowered`), which is when a card may start anything heavy.
 *
 * The cards mount one beat each, not all at once, on purpose: mounting the
 * whole board in the click's own task froze the main thread for most of a
 * second (measured 2026-08-19: the 3D map's WebGL probe, the frames' layout
 * measurements), and every JS-driven animation — the orb's travel above all —
 * simply jumped. Staggering the mounts keeps the launch's first second free,
 * and a card that is heavy (the wiki's WebGL scene) waits for `powered`.
 *
 * Two boxes, on purpose: the OUTER takes the slot's place in the board's grid
 * (`className`, `style` — its size and position there) and hosts the frame,
 * bar and flash; the INNER lays the cards out (`bodyClassName`) and is the
 * one the wipe clips. A frame inside the clipped box would be wiped in with
 * the content instead of drawing ahead of it. Everything that moves here is
 * opacity, clip-path or a whole `transform` string — the values the browser
 * animates off the main thread.
 *
 * `reveal` is decided ONCE for the board's mount (MissionDeckView): a board
 * that mounts straight into a running session — a section change and back,
 * a reload mid-conversation — is simply there, children and all. Either way
 * the wrapper is the same element, so a card never remounts because the flag
 * moved. The overlays unmount when the power-on is over: nothing of it stays
 * in the DOM for the rest of the session.
 *
 * Reduced motion: no frame, no wipe, no bar — the same board, at once.
 */
const EASE: [number, number, number, number] = [0.2, 0.8, 0.2, 1];
const BRACKET_PX = 14;

const DeckSlotContext = createContext<{ powered: boolean }>({ powered: true });

/**
 * Whether the slot a card sits in has finished powering on — true at once
 * for a board that did not animate in. A card with something heavy to start
 * (a WebGL scene, a big chunk) waits for this instead of hitting the launch.
 */
export function useDeckSlotPowered(): boolean {
  return useContext(DeckSlotContext).powered;
}

export function DeckReveal({
  slot,
  reveal,
  className,
  style,
  bodyClassName,
  children,
}: {
  slot: BoardSlot;
  reveal: boolean;
  /** The slot's place in the board's grid: sizing, shrink, min-height. */
  className?: string;
  style?: CSSProperties;
  /** How the cards are laid out inside the slot (flex / grid classes). */
  bodyClassName?: string;
  children: ReactNode;
}) {
  const reduced = useReducedMotion() ?? false;
  const animate = reveal && !reduced;
  const delay = revealDelayMs(slot) / 1000;
  const wipe = revealWipeFor(slot);
  const wipeDelay = delay + CARD_POWER_ON.wipeLeadS;
  const ghostEnd = delay + CARD_POWER_ON.ghostFadeDelayS + CARD_POWER_ON.ghostFadeS;
  const outerRef = useRef<HTMLDivElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  // The ghost frame, scan bar and lock flash live only for the power-on.
  const [powering, setPowering] = useState(animate);
  // The card mounts when its wipe begins — not in the click's own task.
  const [mounted, setMounted] = useState(!animate);
  useEffect(() => {
    if (mounted) return;
    const id = window.setTimeout(() => setMounted(true), wipeDelay * 1000);
    return () => window.clearTimeout(id);
  }, [mounted, wipeDelay]);
  // The slot's box, once, for the scan bar's travel in pixels (a transform,
  // so the bar rides the front off the main thread).
  const [box, setBox] = useState({ w: 0, h: 0 });
  useLayoutEffect(() => {
    if (!animate || !outerRef.current) return;
    setBox({ w: outerRef.current.offsetWidth, h: outerRef.current.offsetHeight });
  }, [animate]);

  return (
    <DeckSlotContext.Provider value={powering ? POWERING : POWERED}>
      <div
        ref={outerRef}
        className={cn("relative flex min-h-0 min-w-0 flex-col", className)}
        style={style}
        data-testid={`deck-slot-${slot}`}
        data-reveal={animate ? "true" : "false"}
        data-wipe={animate ? wipe : undefined}
        data-powered={powering ? "false" : "true"}
      >
        <motion.div
          ref={bodyRef}
          className={cn("min-h-0 min-w-0 flex-1", bodyClassName)}
          initial={animate ? { clipPath: clipFrom(wipe), opacity: 0.35 } : false}
          animate={
            animate
              ? { clipPath: "inset(0% 0% 0% 0%)", opacity: [0.35, 1, 0.7, 1] }
              : { opacity: 1 }
          }
          transition={{
            clipPath: { delay: wipeDelay, duration: CARD_POWER_ON.wipeS, ease: EASE },
            opacity: { delay: wipeDelay, duration: CARD_POWER_ON.wipeS, times: [0, 0.45, 0.6, 1] },
          }}
          // The wipe done, the clip goes: a card must not stay clipped to its
          // box for the rest of the session (focus rings, the frame's halo).
          onAnimationComplete={() => {
            if (bodyRef.current) bodyRef.current.style.clipPath = "";
          }}
        >
          {mounted && children}
        </motion.div>

        {powering && (
          <>
            {/* 1. the targeting frame: a hairline drawn from the orb's side,
                   and four brackets sliding in to the corners */}
            <motion.svg
              aria-hidden
              data-testid="deck-reveal-frame"
              className="pointer-events-none absolute inset-0 h-full w-full overflow-visible"
              viewBox="0 0 100 100"
              preserveAspectRatio="none"
              initial={{ opacity: 0.9 }}
              animate={{ opacity: 0 }}
              transition={{ delay: delay + CARD_POWER_ON.ghostFadeDelayS, duration: CARD_POWER_ON.ghostFadeS }}
              // The ghost fade is the last beat of the power-on: when it is
              // done, every overlay leaves the DOM and the slot is powered.
              onAnimationComplete={() => setPowering(false)}
            >
              <motion.path
                d={framePath(wipe)}
                fill="none"
                stroke="hsl(var(--primary))"
                strokeWidth={1.5}
                vectorEffect="non-scaling-stroke"
                initial={{ pathLength: 0 }}
                animate={{ pathLength: 1 }}
                transition={{ delay, duration: CARD_POWER_ON.frameS, ease: "easeOut" }}
              />
            </motion.svg>
            {BRACKETS.map((b) => (
              <motion.span
                key={b.key}
                aria-hidden
                className={cn("deck-reveal-bracket pointer-events-none absolute", b.className)}
                data-corner={b.key}
                style={{ width: BRACKET_PX, height: BRACKET_PX }}
                initial={{ transform: `translate(${b.x}px, ${b.y}px)`, opacity: 0 }}
                animate={{ transform: "translate(0px, 0px)", opacity: [0, 1, 1, 0] }}
                transition={{
                  transform: { delay, duration: 0.3, ease: EASE },
                  opacity: { delay, duration: ghostEnd - delay, times: [0, 0.2, 0.7, 1] },
                }}
              />
            ))}

            {/* 2. the scan bar on the wipe's front */}
            {box.w > 0 && box.h > 0 && (
              <motion.div
                aria-hidden
                className={cn("deck-scan-bar", wipe === "down" ? "deck-scan-bar-h" : "deck-scan-bar-v")}
                initial={{ transform: barFrom(wipe, box), opacity: 1 }}
                animate={{ transform: barTo(wipe, box), opacity: 0 }}
                transition={{
                  transform: { delay: wipeDelay, duration: CARD_POWER_ON.wipeS, ease: EASE },
                  opacity: { delay: wipeDelay + CARD_POWER_ON.wipeS - 0.1, duration: 0.14 },
                }}
              />
            )}

            {/* 3. the lock flash as the wipe lands */}
            <motion.div
              aria-hidden
              className="pointer-events-none absolute inset-0 bg-primary"
              initial={{ opacity: 0 }}
              animate={{ opacity: [0, 0.16, 0] }}
              transition={{
                delay: wipeDelay + CARD_POWER_ON.wipeS - 0.06,
                duration: CARD_POWER_ON.lockS,
                times: [0, 0.3, 1],
              }}
            />
          </>
        )}
      </div>
    </DeckSlotContext.Provider>
  );
}

const POWERING = { powered: false };
const POWERED = { powered: true };

/** The clip the content starts under: everything hidden, folded away from the orb. */
function clipFrom(wipe: WipeDirection): string {
  switch (wipe) {
    case "down":
      return "inset(0% 0% 100% 0%)";
    case "left":
      return "inset(0% 0% 0% 100%)";
    case "right":
      return "inset(0% 100% 0% 0%)";
  }
}

/** The ghost frame's outline, drawn from the corner nearest the orb. */
function framePath(wipe: WipeDirection): string {
  switch (wipe) {
    case "down":
      return "M 0 0 H 100 V 100 H 0 Z";
    case "left":
      return "M 100 0 H 0 V 100 H 100 Z";
    case "right":
      return "M 0 0 H 100 V 100 H 0 Z";
  }
}

/** Where the scan bar starts: on the orb's side of the slot. */
function barFrom(wipe: WipeDirection, box: { w: number; h: number }): string {
  switch (wipe) {
    case "down":
      return "translateY(0px)";
    case "left":
      return `translateX(${box.w}px)`;
    case "right":
      return "translateX(0px)";
  }
}

/** Where it ends: at the far edge. */
function barTo(wipe: WipeDirection, box: { w: number; h: number }): string {
  switch (wipe) {
    case "down":
      return `translateY(${box.h}px)`;
    case "left":
      return "translateX(0px)";
    case "right":
      return `translateX(${box.w}px)`;
  }
}

/** The four corner brackets: where they sit, and where they slide in from. */
const BRACKETS = [
  { key: "tl", className: "left-0 top-0", x: -10, y: -10 },
  { key: "tr", className: "right-0 top-0", x: 10, y: -10 },
  { key: "bl", className: "bottom-0 left-0", x: -10, y: 10 },
  { key: "br", className: "bottom-0 right-0", x: 10, y: 10 },
] as const;
