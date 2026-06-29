import { MascotGigi } from "@/components/MascotGigi";
import type { OverlayStyle } from "@/hooks/useOverlayStyle";

/**
 * Shared visual previews for the on-screen overlay styles (Bar / Mascot / None).
 *
 * Lifted out of ``views/settings/OverlayTaskbarGroup.tsx`` so both the Settings
 * panel and the onboarding "System Style" step can render the same graphics
 * without one view importing the other. The mascot reuses the real Gigi SVG.
 */

/** Maps an overlay style to its preview graphic. */
export function StylePreview({ style }: { style: OverlayStyle }) {
  if (style === "mascot") {
    return <MascotGigi size={46} reactToVoice={false} enableComments={false} />;
  }
  if (style === "jarvis_bar") return <BarPreview />;
  return <NonePreview />;
}

export function BarPreview() {
  const heights = [6, 11, 15, 8, 14, 9, 7];
  return (
    <svg viewBox="0 0 100 40" className="w-20" aria-hidden="true">
      <rect
        x="6"
        y="11"
        width="88"
        height="18"
        rx="9"
        fill="#0e0d0c"
        stroke="#d7b669"
        strokeWidth="1.6"
      />
      {heights.map((h, i) => (
        <rect
          key={`bar-${i}`}
          x={24 + i * 8}
          y={20 - h / 2}
          width="3"
          height={h}
          rx="1.5"
          fill="#e7c46e"
        />
      ))}
    </svg>
  );
}

export function NonePreview() {
  return (
    <svg viewBox="0 0 100 40" className="w-20 opacity-50" aria-hidden="true">
      <rect
        x="6"
        y="11"
        width="88"
        height="18"
        rx="9"
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
