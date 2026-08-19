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
 * The core carries NO mascot. It did for a day (a silhouette on the gold,
 * 2026-08-18); with the live mascot on the wallpaper that made two ghosts on
 * one stage, and the maintainer took it out (2026-08-19): the orb is the orb.
 *
 * Voice moves it, honestly and cheaply. At rest a slow breath (framer, a
 * gentle loop). While something is happening, the VOICE itself: the deck orb
 * (DeckOrb.tsx) computes one level — the real microphone while listening, a
 * speech-shaped envelope while the assistant speaks, a heartbeat while it
 * thinks — and writes it to `--orb-level`; the reactor wrapper here scales
 * and brightens the sun with it, and the corona behind it — slow-turning
 * rays — comes up in the light of it (CSS, index.css). The framer loops for
 * those states are therefore tiny: the level is the motion. Nothing in the
 * picture turns — a rotating highlight on a glass sphere reads as a broken
 * image; the corona turns BEHIND it. Dimmed and drained on an error.
 * `prefers-reduced-motion` gets the still picture.
 */
const MOTION: Record<VoiceState, { animate: TargetAndTransition; duration: number }> = {
  idle: {
    animate: { scale: [1, 1.012, 1], filter: ["brightness(1)", "brightness(1.04)", "brightness(1)"] },
    duration: 6.5,
  },
  connecting: {
    animate: { scale: [1, 1.006, 1], filter: ["brightness(0.94)", "brightness(1.02)", "brightness(0.94)"] },
    duration: 1.4,
  },
  listening: {
    animate: { scale: [1, 1.006, 1], filter: ["brightness(1)", "brightness(1.03)", "brightness(1)"] },
    duration: 2.2,
  },
  thinking: {
    animate: { scale: [1, 1.006, 1], filter: ["brightness(1)", "brightness(1.03)", "brightness(1)"] },
    duration: 1.8,
  },
  speaking: {
    animate: { scale: [1, 1.006, 1], filter: ["brightness(1)", "brightness(1.03)", "brightness(1)"] },
    duration: 2.2,
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
      {/* the corona: slow-turning rays behind the sun, lit by the level */}
      <div aria-hidden className="deck-orb-corona pointer-events-none absolute rounded-full" />
      {/* the reactor: the sun, sized and lit by the level */}
      <div className="deck-orb-reactor absolute inset-0">
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
      </div>
    </div>
  );
}
