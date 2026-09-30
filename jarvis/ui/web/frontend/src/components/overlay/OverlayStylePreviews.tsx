import { MascotGigi } from "@/components/MascotGigi";
import { VoiceOrb } from "@/components/agentic/VoiceOrb";
import type { OverlayStyle } from "@/hooks/useOverlayStyle";
import {
  PREVIEW_BAR_SPAN,
  PILL_CY,
  PILL_H,
  PILL_R,
  PILL_W,
  PILL_X,
  PILL_Y,
  PREVIEW_BAR_HEIGHTS,
  VIEW_H,
  VIEW_W,
  barWidth,
  evenlySpaced,
} from "./voiceBars";

/**
 * Shared visual previews for the on-screen overlay styles (Bar / Mascot /
 * Voice orb / Pet / None).
 *
 * Lifted out of ``views/settings/OverlayTaskbarGroup.tsx`` so both the Settings
 * panel and the onboarding "System Style" step can render the same graphics
 * without one view importing the other. The mascot reuses the real Gigi SVG.
 *
 * The bar thumbnail draws its geometry from ``voiceBars`` — the same module the
 * live ``VoiceWaveform`` uses — so what the picker shows is the shape the user
 * actually gets. The PALETTE is deliberately not shared: this thumbnail is a
 * portrait of the DESKTOP overlay, which is gold on near-black whatever theme
 * the web UI runs in, so it keeps the renderer's literal colours. The live
 * visualizer sits inside the themed app and follows the theme instead.
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
const PREVIEW_BG = "#0e0d0c";
const PREVIEW_RIM = "#4a4842";
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
  const w = barWidth(PREVIEW_BAR_HEIGHTS.length, PREVIEW_BAR_SPAN);
  return (
    <svg viewBox={`0 0 ${VIEW_W} ${VIEW_H}`} className="w-20" aria-hidden="true">
      <rect
        x={PILL_X}
        y={PILL_Y}
        width={PILL_W}
        height={PILL_H}
        rx={PILL_R}
        fill={PREVIEW_BG}
        stroke={PREVIEW_RIM}
        strokeWidth="1.6"
      />
      {evenlySpaced(VIEW_W / 2, PREVIEW_BAR_SPAN, PREVIEW_BAR_HEIGHTS.length).map((x, i) => {
        const h = PREVIEW_BAR_HEIGHTS[i];
        return (
          <rect
            key={`bar-${x}`}
            x={x - w / 2}
            y={PILL_CY - h / 2}
            width={w}
            height={h}
            rx={w / 2}
            fill={PREVIEW_BAR}
          />
        );
      })}
    </svg>
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
 * The pet style: a pixel companion above its control strip — the pen disc,
 * then the pill with microphone, talk orb and speaker. Same desktop palette as
 * the bar thumbnail, for the same reason.
 */
export function PetPreview() {
  const petW = PET_PIXELS[0].length * PET_PX;
  const petX = (56 - petW) / 2;
  return (
    <svg viewBox="0 0 56 46" className="h-14" aria-hidden="true">
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
      <circle cx="11" cy="37" r="5.5" fill={PREVIEW_BG} stroke={PREVIEW_RIM} strokeWidth="1" />
      <rect x="9.5" y="34.5" width="3" height="5" rx="0.8" fill={PREVIEW_BAR} transform="rotate(35 11 37)" />
      <rect x="19" y="31.5" width="33" height="11" rx="5.5" fill={PREVIEW_BG} stroke={PREVIEW_RIM} strokeWidth="1" />
      <rect x="25" y="34.5" width="3" height="5" rx="1.5" fill={PREVIEW_BAR} />
      <circle cx="35.5" cy="37" r="3.2" fill={PREVIEW_BAR} />
      <path d="M43 35.5h1.6l2-1.8v6.6l-2-1.8H43z" fill={PREVIEW_BAR} />
    </svg>
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
