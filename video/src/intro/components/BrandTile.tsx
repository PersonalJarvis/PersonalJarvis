import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";

/**
 * A real brand logo (from public/logos/<slug>.svg, official Simple Icons SVG in
 * its brand colour) on a light rounded tile so even dark logos stay legible on
 * the dark video background. Springs in at `delay`.
 */
export const BrandTile: React.FC<{ slug: string; label: string; delay?: number }> = ({
  slug,
  label,
  delay = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.7 } });
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 11,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [22, 0])}px) scale(${interpolate(
          s,
          [0, 1],
          [0.88, 1],
        )})`,
      }}
    >
      <div
        style={{
          width: 82,
          height: 82,
          borderRadius: 20,
          backgroundColor: "#FFFFFF",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxShadow: "0 12px 32px rgba(0,0,0,0.45)",
        }}
      >
        <Img src={staticFile(`logos/${slug}.svg`)} style={{ width: 46, height: 46 }} />
      </div>
      <span style={{ fontFamily: FONT, fontSize: 18, fontWeight: 600, color: COLORS.textMuted }}>
        {label}
      </span>
    </div>
  );
};
