import { motion, useReducedMotion, type TargetAndTransition } from "framer-motion";
import { MascotGigi } from "@/components/MascotGigi";
import type { VoiceState } from "@/store/events";
import { cn } from "@/lib/utils";

/**
 * The centre of the deck: the mascot itself.
 *
 * It used to be `/deck-orb.png` — a sphere cut out of the marketing render.
 * A picture of a light, not a light: its halo was baked in as milky white
 * pixels, so on the dark deck it read as a grey box, and the reticle around
 * it sliced that box off at the ring. The maintainer asked repeatedly for the
 * PNG to go and for the mascot to carry the product instead (2026-08-20), so
 * both PNGs are gone from the tree and the middle is Gigi, drawn as vectors:
 * sharp at any size, no edge to cut, no bitmap to ship.
 *
 * The staging is a dark figure standing in golden light, and it all happens
 * BEHIND and BELOW it (DeckOrb.tsx: the backlight and the footlight) —
 * nothing is drawn over the figure and nothing rings it. A corona of turning
 * rays sat here for a day and went the same way as the bezel (2026-08-20):
 * rays fanning out of a circle are sphere furniture, and the mascot is not a
 * sphere. Gigi brings its own life — it blinks, the pupils drift, it waves
 * now and then, and with `reactToVoice` it listens, thinks and speaks along.
 *
 * Voice moves the whole figure, honestly and cheaply. At rest a slow breath
 * (framer, a gentle loop). While something is happening, the VOICE itself:
 * the deck orb computes one level — the real microphone while listening, a
 * speech-shaped envelope while the assistant speaks, a heartbeat while it
 * thinks — and writes it to `--orb-level`; the reactor wrapper here scales
 * and brightens Gigi with it, and the corona behind it comes up in that same
 * light (CSS, index.css). The framer loops are therefore tiny: the level is
 * the motion. Dimmed and drained on an error. `prefers-reduced-motion` gets
 * the still figure.
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
      {/* the reactor: the figure, sized and lit by the level */}
      <div className="deck-orb-reactor absolute inset-0">
        <motion.div
          className="grid h-full w-full place-items-center"
          animate={reduced ? undefined : spec.animate}
          transition={
            reduced
              ? undefined
              : looping
                ? { duration: spec.duration, repeat: Infinity, ease: "easeInOut" }
                : { duration: 0.4 }
          }
        >
          <MascotGigi size={size} reactToVoice enableComments={false} />
        </motion.div>
      </div>
    </div>
  );
}
