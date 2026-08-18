import { useRef, type CSSProperties, type ReactNode } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { revealDelayMs, type BoardSlot } from "@/lib/deckStandby";
import { cn } from "@/lib/utils";

/**
 * How a board instrument powers on when the board takes over from the standby
 * stage: a top-to-bottom wipe with a gold scan bar riding the front, timed
 * from the centre outward (`revealDelayMs`) so the board assembles around the
 * orb instead of popping in as one block.
 *
 * `reveal` is decided ONCE for the board's mount (MissionDeckView): a board
 * that mounts straight into a running session — a section change and back,
 * a reload mid-conversation — is simply there. Either way the wrapper is the
 * same element, so a card never remounts because the flag moved.
 *
 * Reduced motion: no wipe, no bar — the same board, at once.
 */
const WIPE_S = 0.42;
const EASE: [number, number, number, number] = [0.2, 0.8, 0.2, 1];

export function DeckReveal({
  slot,
  reveal,
  className,
  style,
  children,
}: {
  slot: BoardSlot;
  reveal: boolean;
  className?: string;
  style?: CSSProperties;
  children: ReactNode;
}) {
  const reduced = useReducedMotion() ?? false;
  const animate = reveal && !reduced;
  const delay = revealDelayMs(slot) / 1000;
  const ref = useRef<HTMLDivElement>(null);

  return (
    <motion.div
      ref={ref}
      className={cn("relative", className)}
      style={style}
      data-testid={`deck-slot-${slot}`}
      data-reveal={animate ? "true" : "false"}
      initial={animate ? { clipPath: "inset(0 0 100% 0)", opacity: 0.4 } : false}
      animate={animate ? { clipPath: "inset(0 0 0% 0)", opacity: 1 } : { opacity: 1 }}
      transition={{ delay, duration: WIPE_S, ease: EASE }}
      // The wipe done, the clip goes: a card must not stay clipped to its box
      // for the rest of the session (focus rings, the frame's halo).
      onAnimationComplete={() => {
        if (ref.current) ref.current.style.clipPath = "";
      }}
    >
      {children}
      {animate && (
        <motion.div
          aria-hidden
          className="deck-scan-bar"
          initial={{ top: "0%", opacity: 1 }}
          animate={{ top: "100%", opacity: 0 }}
          transition={{
            top: { delay, duration: WIPE_S, ease: EASE },
            opacity: { delay: delay + WIPE_S - 0.1, duration: 0.14 },
          }}
          style={{ translateY: "-100%" }}
        />
      )}
    </motion.div>
  );
}
