import { useCurrentFrame } from "remotion";
import { COLORS } from "../theme";

/** Animated voice waveform — deterministic, frame-driven sine bars. */
export const WaveBars: React.FC<{
  bars?: number;
  width?: number;
  height?: number;
  active?: boolean;
}> = ({ bars = 11, width = 220, height = 64, active = true }) => {
  const frame = useCurrentFrame();
  const gap = 6;
  const barW = (width - gap * (bars - 1)) / bars;
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "center",
        gap,
        height,
        width,
      }}
    >
      {new Array(bars).fill(0).map((_, i) => {
        const phase = i * 0.6;
        const wobble = (Math.sin(frame / 5 + phase) + 1) / 2; // 0..1
        const center = 1 - Math.abs(i - (bars - 1) / 2) / ((bars - 1) / 2); // taper edges
        const h = active
          ? height * (0.18 + wobble * (0.55 * (0.4 + center * 0.6)))
          : height * 0.14;
        return (
          <div
            key={i}
            style={{
              width: barW,
              height: h,
              borderRadius: barW,
              backgroundColor: COLORS.primary,
              opacity: 0.85,
            }}
          />
        );
      })}
    </div>
  );
};
