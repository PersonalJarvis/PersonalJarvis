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
    // Two cascaded smoothers: an envelope (quick rise, slow fall — a voice,
    // not a meter) and a short glide on top of it, so the ~30 Hz level
    // samples never reach the light as steps. Both are time-based, so the
    // motion feels the same at 60 Hz, 144 Hz or after a dropped frame.
    let envelope = 0;
    let level = 0;
    let last = -1;
    let frame = 0;
    // Each pool's sway position is ACCUMULATED, never computed as
    // sin(time * speed(level)). That form multiplied the level by the time
    // since page load (thousands of seconds), so the smallest change in
    // loudness threw every pool to a random spot on each frame — the
    // jumping, glitching light. Accumulating keeps the path continuous: the
    // voice only changes how fast the pools travel, never where they are.
    const sway = BLOBS.map((b) => b.phase);
    const breath = BLOBS.map((b) => b.phase * 2);
    const tick = (now: number) => {
      const dt = last < 0 ? 0 : Math.min(0.1, (now - last) / 1000);
      last = now;
      const target = Math.max(voiceOutputLevelRef.current ?? 0, readVoiceInputLevel(now));
      envelope += (target - envelope) * (1 - Math.exp(-dt / (target > envelope ? 0.06 : 0.45)));
      level += (envelope - level) * (1 - Math.exp(-dt / 0.12));
      // Lift quiet speech so a normal voice visibly moves the light.
      const voice = Math.min(1, Math.pow(level, 0.7));
      BLOBS.forEach((b, i) => {
        const el = blobs[i];
        if (!el) return;
        // Sway: a slow drift always, a little quicker and wider with the voice.
        sway[i] += dt * b.speed * (1 + voice * 1.2);
        breath[i] += dt * b.speed * 1.3;
        const x = Math.sin(sway[i]) * (b.drift + voice * 5);
        // Breathing: each pool brightens and dims softly on its own.
        const glow = 0.5 + 0.5 * Math.sin(breath[i]);
        el.style.opacity = String(Math.min(1, 0.5 + voice * 0.45 + glow * 0.08).toFixed(3));
        el.style.transform =
          `translateX(calc(-50% + ${x.toFixed(2)}%)) ` +
          `scale(${(1 + voice * 0.1).toFixed(3)}, ${(1 + voice * b.lift).toFixed(3)})`;
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
          // The fade only eases the rest <-> call hand-over (light AND shape, so
          // a hang-up mid-word settles instead of snapping back). While a call runs
          // the frame loop writes opacity every frame, and a transition there
          // restarted a 500 ms fade per frame — the light lagged the voice and
          // the browser re-planned a transition 60 times a second.
          className={cn(
            "absolute bottom-0 origin-bottom will-change-[transform,opacity]",
            !active && "transition-[opacity,transform] duration-700 ease-out",
          )}
          style={{
            left: `${b.x}%`,
            width: b.width,
            height: b.height,
            transform: "translateX(-50%)",
            opacity: 0.3,
            background: glowGradient(b.alpha),
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

/**
 * One pool's light: a bell-shaped falloff from the bottom edge. With only a
 * bright core and a hard stop the ellipse's rim showed as an arc and the
 * pools read as shapes; many gently decaying stops blend into a haze.
 */
function glowGradient(alpha: number): string {
  const stops = [
    [1, 0],
    [0.86, 12],
    [0.66, 24],
    [0.45, 36],
    [0.27, 48],
    [0.14, 59],
    [0.06, 69],
    [0.02, 78],
    [0, 88],
  ]
    .map(([k, at]) => `rgb(var(--accent-rgb) / ${(alpha * k).toFixed(3)}) ${at}%`)
    .join(", ");
  return `radial-gradient(ellipse 50% 70% at 50% 100%, ${stops})`;
}
