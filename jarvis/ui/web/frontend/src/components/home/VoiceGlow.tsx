import { useEffect, useRef } from "react";

import { cn } from "@/lib/utils";
import { readVoiceInputLevel } from "@/lib/voiceInputLevel";
import { voiceOutputLevelRef } from "@/lib/voiceOutputLevel";

/**
 * The soft light behind voice mode's composer — the Claude app's voice glow,
 * in this theme's one signal hue.
 *
 * Three overlapping pools of accent light rise from the bottom edge, each
 * drifting sideways on its own slow rhythm, so the light shimmers rather
 * than sitting still. At rest (no call) it is a faint, motionless wash.
 * While a call is open the pools drift gently; when someone speaks — you
 * (the microphone level) or the assistant (the playback level), whichever
 * is louder — they swell, brighten and sway wider, so the bottom of the
 * page visibly moves with the voice. The levels are read on animation
 * frames — no React state, no re-render per frame — and only while a call
 * is open. Reduced motion keeps the light and drops the movement.
 *
 * Purely decorative: no pointer events, hidden from screen readers.
 */
export function VoiceGlow({ active }: { active: boolean }) {
  const blobRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const blobs = blobRefs.current.filter((b): b is HTMLDivElement => b !== null);
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!active || reduced || typeof requestAnimationFrame === "undefined") {
      blobs.forEach((el) => {
        el.style.opacity = active ? "0.7" : "0.3";
        el.style.transform = "translateX(-50%)";
      });
      return;
    }
    let level = 0;
    let frame = 0;
    const tick = (now: number) => {
      const target = Math.max(voiceOutputLevelRef.current ?? 0, readVoiceInputLevel(now));
      // Rise fast, fall slowly — a voice, not a meter.
      level += (target - level) * (target > level ? 0.3 : 0.06);
      const t = now / 1000;
      BLOBS.forEach((b, i) => {
        const el = blobs[i];
        if (!el) return;
        // Sway: a slow drift always, wider and quicker while someone speaks.
        const sway = Math.sin(t * b.speed * (1 + level * 1.5) + b.phase) * (b.drift + level * 9);
        // Flicker: each pool breathes on its own, more so with the voice.
        const flicker = 0.5 + 0.5 * Math.sin(t * b.speed * 3.1 + b.phase * 2);
        el.style.opacity = String(Math.min(1, 0.45 + level * 0.5 + flicker * (0.08 + level * 0.25)));
        el.style.transform =
          `translateX(calc(-50% + ${sway.toFixed(2)}%)) ` +
          `scale(${(1 + level * 0.12).toFixed(3)}, ${(1 + level * b.lift).toFixed(3)})`;
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [active]);

  return (
    <div aria-hidden data-testid="voice-glow" className="pointer-events-none absolute inset-x-0 bottom-0 h-[38vh] overflow-hidden">
      {BLOBS.map((b, i) => (
        <div
          key={i}
          ref={(el) => {
            blobRefs.current[i] = el;
          }}
          // The fade only eases the rest <-> call hand-over. While a call runs
          // the frame loop writes opacity every frame, and a transition there
          // restarted a 500 ms fade per frame — the light lagged the voice and
          // the browser re-planned a transition 60 times a second.
          className={cn("absolute bottom-0 origin-bottom", !active && "transition-opacity duration-500")}
          style={{
            left: `${b.x}%`,
            width: b.width,
            height: b.height,
            transform: "translateX(-50%)",
            opacity: 0.3,
            background: `radial-gradient(ellipse 50% 70% at 50% 100%, rgb(var(--accent-rgb) / ${b.alpha}), rgb(var(--accent-rgb) / ${b.alpha / 3}) 45%, transparent 75%)`,
          }}
        />
      ))}
    </div>
  );
}

/** Three pools of light: one wide and calm in the middle, two smaller and livelier. */
const BLOBS = [
  { x: 50, width: "min(760px, 90%)", height: "100%", alpha: 0.26, speed: 0.35, phase: 0, drift: 2, lift: 0.3 },
  { x: 40, width: "min(420px, 55%)", height: "80%", alpha: 0.22, speed: 0.6, phase: 2.1, drift: 5, lift: 0.5 },
  { x: 60, width: "min(420px, 55%)", height: "75%", alpha: 0.2, speed: 0.75, phase: 4.2, drift: 5, lift: 0.55 },
] as const;
