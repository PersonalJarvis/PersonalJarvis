import { useEffect, useRef } from "react";

import { voiceInputLevelRef } from "@/lib/voiceInputLevel";
import { voiceOutputLevelRef } from "@/lib/voiceOutputLevel";

/**
 * The soft light behind voice mode's composer — the Claude app's voice glow,
 * in this theme's one signal hue.
 *
 * A wide ellipse of accent light rising from the bottom edge, behind the
 * composer. At rest it is a faint wash; while a call runs it swells with
 * whichever voice is louder, yours (the microphone level) or the
 * assistant's (the playback level), so the page visibly breathes with the
 * conversation. The level refs are read on animation frames — no React
 * state, no re-render per frame — and only while a call is open. Reduced
 * motion keeps the light and drops the movement.
 *
 * Purely decorative: no pointer events, hidden from screen readers.
 */
export function VoiceGlow({ active }: { active: boolean }) {
  const ref = useRef<HTMLDivElement | null>(null);

  useEffect(() => {
    const el = ref.current;
    if (!el) return;
    const reduced =
      typeof window.matchMedia === "function" &&
      window.matchMedia("(prefers-reduced-motion: reduce)").matches;
    if (!active || reduced || typeof requestAnimationFrame === "undefined") {
      el.style.opacity = active ? "0.75" : "0.35";
      el.style.transform = "translateX(-50%) scale(1)";
      return;
    }
    let level = 0;
    let frame = 0;
    const tick = () => {
      const target = Math.max(voiceOutputLevelRef.current ?? 0, voiceInputLevelRef.current);
      // Rise fast, fall slowly — a voice, not a meter.
      level += (target - level) * (target > level ? 0.35 : 0.08);
      el.style.opacity = String(0.55 + level * 0.45);
      el.style.transform = `translateX(-50%) scale(${1 + level * 0.18})`;
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [active]);

  return (
    <div
      ref={ref}
      aria-hidden
      data-testid="voice-glow"
      className="pointer-events-none absolute bottom-0 left-1/2 h-[34vh] w-[min(760px,90%)] origin-bottom transition-opacity duration-500"
      style={{
        transform: "translateX(-50%)",
        opacity: 0.35,
        background:
          "radial-gradient(ellipse 50% 70% at 50% 100%, rgb(var(--accent-rgb) / 0.32), rgb(var(--accent-rgb) / 0.10) 45%, transparent 75%)",
      }}
    />
  );
}
