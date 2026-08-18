import { useId } from "react";
import type { VoiceState } from "@/store/events";
import { cn } from "@/lib/utils";

/**
 * The mask inside the orb.
 *
 * The deck's centre used to be the whole mascot pasted onto a bright gold
 * ball — a sticker on a moon. Now the mask IS the orb's core: a round, dark
 * body sunk into the golden weather, with only the eyes and the mouth glowing
 * gold out of it (maintainer's pick, 2026-08-18: "only the eyes in the dark
 * core"). The silhouette is implied, never cut out, so nothing competes with
 * the big mask in the wallpaper.
 *
 * The face reacts to the real voice state through the `data-voice` attribute
 * — the CSS in index.css (`.deck-core*`) reuses the mascot's keyframes: it
 * blinks, the pupils drift, the eyes prick up while listening, the head cocks
 * while thinking, the mouth moves while speaking. Everything stops under
 * reduced motion.
 */
export function OrbCore({
  size,
  voiceState,
  className,
}: {
  size: number;
  voiceState: VoiceState;
  className?: string;
}) {
  const uid = useId();
  const id = (name: string) => `${uid}${name}`;
  return (
    <svg
      viewBox="0 0 256 256"
      width={size}
      height={size}
      className={cn("deck-core", className)}
      data-testid="orb-core"
      data-voice={voiceState}
      aria-hidden
    >
      <defs>
        <radialGradient id={id("deckCoreBody")} cx="50%" cy="38%" r="62%">
          <stop offset="0%" stopColor="#2a1d0a" />
          <stop offset="55%" stopColor="#120c04" />
          <stop offset="100%" stopColor="#070502" />
        </radialGradient>
        <linearGradient id={id("deckCoreGold")} x1="0%" y1="0%" x2="0%" y2="100%">
          <stop offset="0%" stopColor="#fff1b8" />
          <stop offset="55%" stopColor="#e7c46e" />
          <stop offset="100%" stopColor="#d29318" />
        </linearGradient>
        <filter id={id("deckCoreGlow")} x="-60%" y="-60%" width="220%" height="220%">
          <feGaussianBlur stdDeviation="4" result="b" />
          <feMerge>
            <feMergeNode in="b" />
            <feMergeNode in="SourceGraphic" />
          </feMerge>
        </filter>
        <filter id={id("deckCoreSoft")} x="-80%" y="-80%" width="260%" height="260%">
          <feGaussianBlur stdDeviation="9" />
        </filter>
      </defs>

      {/* The body: a dark disc with a hairline of gold where it meets the aura. */}
      <circle cx={128} cy={128} r={124} fill={`url(#${id("deckCoreBody")})`} />
      <circle
        cx={128}
        cy={128}
        r={124}
        fill="none"
        stroke="#e7c46e"
        strokeWidth={1.5}
        opacity={0.38}
      />

      <g className="deck-core-face">
        {/* Eye glow — the light the eyes throw onto the body. */}
        <ellipse cx={84} cy={112} rx={26} ry={34} fill="#f8e297" opacity={0.3} filter={`url(#${id("deckCoreSoft")})`} />
        <ellipse cx={172} cy={112} rx={26} ry={34} fill="#f8e297" opacity={0.3} filter={`url(#${id("deckCoreSoft")})`} />

        {/* Eyes — they blink; pupils and sparkle live inside so they blink along. */}
        <g className="deck-core-eyes">
          <ellipse cx={84} cy={112} rx={17} ry={24} fill={`url(#${id("deckCoreGold")})`} filter={`url(#${id("deckCoreGlow")})`} />
          <ellipse cx={172} cy={112} rx={17} ry={24} fill={`url(#${id("deckCoreGold")})`} filter={`url(#${id("deckCoreGlow")})`} />
          <g className="deck-core-pupils">
            <ellipse cx={87} cy={117} rx={6.5} ry={10} fill="#070502" />
            <ellipse cx={175} cy={117} rx={6.5} ry={10} fill="#070502" />
            <circle cx={90} cy={104} r={3.2} fill="#ffffff" />
            <circle cx={178} cy={104} r={3.2} fill="#ffffff" />
          </g>
        </g>

        {/* Mouth — breathes at rest, moves while speaking. */}
        <g className="deck-core-mouth">
          <ellipse cx={128} cy={170} rx={12} ry={17} fill={`url(#${id("deckCoreGold")})`} filter={`url(#${id("deckCoreGlow")})`} />
          <ellipse cx={128} cy={170} rx={5} ry={8.5} fill="#070502" />
        </g>
      </g>
    </svg>
  );
}
