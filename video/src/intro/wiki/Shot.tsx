import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";

/** A real app screenshot in a clean framed window; children overlay on top. */
export const ShotFrame: React.FC<{
  src: string;
  width: number;
  delay?: number;
  children?: React.ReactNode;
}> = ({ src, width, delay = 0, children }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.8 } });
  return (
    <div
      style={{
        position: "relative",
        width,
        borderRadius: 14,
        overflow: "hidden",
        border: `1px solid ${COLORS.borderStrong}`,
        boxShadow: `0 30px 80px rgba(0,0,0,0.55), 0 0 46px ${COLORS.primaryGlow}`,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [30, 0])}px) scale(${interpolate(
          enter,
          [0, 1],
          [0.96, 1],
        )})`,
      }}
    >
      <Img src={staticFile(src)} style={{ width, display: "block" }} />
      {children}
    </div>
  );
};

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * A pulsing highlight ring over a source-pixel box (scaled to the displayed
 * screenshot), with an optional callout pill. `scale` = displayedWidth / srcW.
 */
export const Ring: React.FC<{
  box: Box;
  scale: number;
  delay?: number;
  label?: string;
  /** Source-pixel top-left of the callout pill. */
  labelAt?: { x: number; y: number };
}> = ({ box, scale, delay = 0, label, labelAt }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  const pulse = (Math.sin((frame - delay) / 12) + 1) / 2;
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: box.x * scale,
          top: box.y * scale,
          width: box.w * scale,
          height: box.h * scale,
          borderRadius: 8,
          border: `2px solid ${COLORS.primary}`,
          boxShadow: `0 0 ${10 + pulse * 16}px ${COLORS.primaryGlow}`,
          backgroundColor: "rgba(255,214,10,0.08)",
          opacity: s,
        }}
      />
      {label && labelAt && (
        <div
          style={{
            position: "absolute",
            left: labelAt.x * scale,
            top: labelAt.y * scale,
            padding: "6px 13px",
            borderRadius: 999,
            backgroundColor: COLORS.bgCard,
            border: `1px solid ${COLORS.primary}`,
            fontFamily: FONT,
            fontSize: 15,
            fontWeight: 700,
            color: COLORS.text,
            whiteSpace: "nowrap",
            opacity: s,
            transform: `translateY(${interpolate(s, [0, 1], [-8, 0])}px)`,
          }}
        >
          {label}
        </div>
      )}
    </>
  );
};

/** A small caption chip that springs in — used below a screenshot. */
export const Caption: React.FC<{ children: React.ReactNode; delay?: number }> = ({
  children,
  delay = 0,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [12, 0])}px)`,
        fontFamily: FONT,
        fontSize: 26,
        fontWeight: 600,
        color: COLORS.textMuted,
        textAlign: "center",
        maxWidth: 980,
      }}
    >
      {children}
    </div>
  );
};
