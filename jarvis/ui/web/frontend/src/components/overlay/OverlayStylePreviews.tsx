import { MascotGigi } from "@/components/MascotGigi";
import { VoiceOrb } from "@/components/agentic/VoiceOrb";
import { PetControlStripPreview } from "@/components/pets/PetControlStripPreview";
import type { OverlayStyle } from "@/hooks/useOverlayStyle";
import {
  PILL_H,
  PILL_R,
  PILL_W,
  PILL_X,
  PILL_Y,
  VIEW_H,
  VIEW_W,
} from "./voiceBars";

/**
 * Shared visual previews for the on-screen overlay styles (Bar / Mascot /
 * Voice orb / Pet / None).
 *
 * Lifted out of ``views/settings/OverlayTaskbarGroup.tsx`` so both the Settings
 * panel and the onboarding "System Style" step can render the same graphics
 * without one view importing the other. The mascot reuses the real Gigi SVG.
 *
 * The canonical bar uses the existing Pet control-strip preview, including
 * its write, microphone, blue talk sphere, speaker and phone controls.
 *
 * These are still previews, not instruments: they animate nothing. Three
 * thumbnails oscillating side by side on a settings screen is noise, and with
 * no microphone behind them any motion here would be invented.
 */

/*
 * This thumbnail paints its own ground rather than the app's, because it is a
 * portrait of the desktop overlay — which has no page behind it. So the values
 * are literals, not tokens, and they follow the same rule as the rest of the
 * monochrome pass: the frame recedes and only the content lifts.
 */
const PREVIEW_BAR = "#c9c6bd";

/** Maps an overlay style to its preview graphic. */
export function StylePreview({ style }: { style: OverlayStyle }) {
  if (style === "mascot") {
    return <MascotGigi size={46} reactToVoice={false} enableComments={false} />;
  }
  // The voice orb is its own portrait: the very renderer the desktop overlay
  // runs, at thumbnail size. Held at "idle" so the picker shows the calm
  // resting look rather than pretending a session is live.
  if (style === "voice_orb") return <VoiceOrb state="idle" size={46} />;
  if (style === "pet") return <PetPreview />;
  if (style === "jarvis_bar") return <BarPreview />;
  return <NonePreview />;
}

export function BarPreview() {
  return (
    <div className="flex h-10 w-24 shrink-0 items-center justify-center" data-testid="jarvis-bar-preview">
      <PetControlStripPreview size="sm" className="shrink-0 scale-[0.52]" />
    </div>
  );
}

/*
 * A pixel pet, one row per string: "#" is a filled pixel. Drawn here rather
 * than cut from a real sprite sheet because the picker renders before (and
 * without) the pets API; the page that picks the actual pet shows the real one.
 */
const PET_PIXELS = [
  "..####..",
  ".######.",
  "########",
  "##.##.##",
  "########",
  "########",
  "#..##..#",
];
const PET_PX = 3;

/**
 * The pet retains its notification bell and audio indicator, plus the phone.
 */
export function PetPreview() {
  const petW = PET_PIXELS[0].length * PET_PX;
  const petX = (56 - petW) / 2;
  return (
    <div className="flex h-14 w-24 shrink-0 flex-col items-center" aria-hidden="true">
    <svg viewBox="0 0 56 27" className="h-7 shrink-0">
      {PET_PIXELS.flatMap((line, y) =>
        [...line].map((cell, x) =>
          cell === "#" ? (
            <rect
              key={`px-${x}-${y}`}
              x={petX + x * PET_PX}
              y={2 + y * PET_PX}
              width={PET_PX}
              height={PET_PX}
              fill={PREVIEW_BAR}
            />
          ) : null,
        ),
      )}
    </svg>
    <PetControlStripPreview size="sm" companion className="shrink-0 scale-[0.52]" />
    </div>
  );
}

export function NonePreview() {
  return (
    <svg
      viewBox={`0 0 ${VIEW_W} ${VIEW_H}`}
      className="w-20 opacity-50"
      aria-hidden="true"
    >
      <rect
        x={PILL_X}
        y={PILL_Y}
        width={PILL_W}
        height={PILL_H}
        rx={PILL_R}
        fill="none"
        stroke="#7c766b"
        strokeWidth="1.6"
        strokeDasharray="4 3"
      />
      {/* Diagonal "disabled" strike — kept inside the dashed box (y 11..29)
          and symmetric about its centre (50, 20) so it never juts out as a
          stub above/below the pill. */}
      <line x1="25" y1="25" x2="75" y2="15" stroke="#7c766b" strokeWidth="1.6" strokeLinecap="round" />
    </svg>
  );
}
