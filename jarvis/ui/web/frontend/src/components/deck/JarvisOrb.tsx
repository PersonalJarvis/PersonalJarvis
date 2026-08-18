import { motion, useReducedMotion, type TargetAndTransition } from "framer-motion";
import type { VoiceState } from "@/store/events";
import { cn } from "@/lib/utils";

/**
 * The Jarvis orb — the product's own artwork, in the middle of the deck.
 *
 * `/deck-orb.png` is the sphere cut out of `hero-orb.png` (the marketing
 * render: dark glass, a golden plasma core, ring echoes and sparks) with the
 * black plate keyed to transparency and a radial mask on the rest, so it sits
 * on the wallpaper as a thing, not as a picture of a thing. The maintainer
 * asked for exactly this on 2026-08-18: the orb IS a PNG, so bring the PNG.
 *
 * The mascot rides in the plasma core as a silhouette — the same language as
 * the wallpaper's black ghost on gold, and readable at any size because the
 * core is the brightest thing on the screen.
 *
 * Voice state moves it a little, honestly and cheaply: a slow breath at rest,
 * a brighter, quicker pulse while listening, quicker still while it speaks, a
 * steady heartbeat while it thinks, dimmed and drained on an error. Nothing
 * turns — a rotating highlight on a glass sphere reads as a broken image.
 * `prefers-reduced-motion` gets the still picture.
 */
const MOTION: Record<VoiceState, { animate: TargetAndTransition; duration: number }> = {
  idle: {
    animate: { scale: [1, 1.012, 1], filter: ["brightness(1)", "brightness(1.04)", "brightness(1)"] },
    duration: 6.5,
  },
  connecting: {
    animate: { scale: [1, 1.01, 1], filter: ["brightness(0.9)", "brightness(1.05)", "brightness(0.9)"] },
    duration: 1.4,
  },
  listening: {
    animate: { scale: [1, 1.03, 1], filter: ["brightness(1)", "brightness(1.22)", "brightness(1)"] },
    duration: 1.6,
  },
  thinking: {
    animate: { scale: [1, 1.02, 1], filter: ["brightness(1)", "brightness(1.12)", "brightness(1)"] },
    duration: 0.9,
  },
  speaking: {
    animate: { scale: [1, 1.035, 1], filter: ["brightness(1)", "brightness(1.3)", "brightness(1)"] },
    duration: 0.55,
  },
  paused: {
    animate: { scale: 1, filter: "brightness(0.85) saturate(0.7)" },
    duration: 0,
  },
  error: {
    animate: { scale: 1, filter: "brightness(0.6) saturate(0)" },
    duration: 0,
  },
};

/** Where the plasma core sits inside the cut-out (fractions of the image). */
const CORE_CENTRE_Y = 0.547;
const MASCOT_OF_ORB = 0.24;

export function JarvisOrb({
  size,
  voiceState,
  className,
}: {
  size: number;
  voiceState: VoiceState;
  className?: string;
}) {
  const reduced = useReducedMotion() ?? false;
  const spec = MOTION[voiceState] ?? MOTION.idle;
  const looping = spec.duration > 0;

  return (
    <div
      data-testid="jarvis-orb"
      data-voice={voiceState}
      className={cn("relative select-none", className)}
      style={{ width: size, height: size }}
    >
      <motion.img
        src="/deck-orb.png"
        alt=""
        draggable={false}
        className="absolute inset-0 h-full w-full"
        animate={reduced ? undefined : spec.animate}
        transition={
          reduced
            ? undefined
            : looping
              ? { duration: spec.duration, repeat: Infinity, ease: "easeInOut" }
              : { duration: 0.4 }
        }
      />
      {/* The mascot in the core. A silhouette on gold, like the wallpaper. */}
      <img
        src="/jarvis-gigi-256.png"
        alt=""
        draggable={false}
        aria-hidden
        className="pointer-events-none absolute left-1/2 -translate-x-1/2 -translate-y-1/2"
        style={{ top: `${CORE_CENTRE_Y * 100}%`, width: size * MASCOT_OF_ORB, height: size * MASCOT_OF_ORB }}
      />
    </div>
  );
}
