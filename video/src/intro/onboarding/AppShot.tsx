import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";

interface Box {
  x: number;
  y: number;
  w: number;
  h: number;
}

/**
 * A REAL screenshot of the running desktop app, in a clean framed window, with
 * an optional pulsing highlight box + callout pinned to source-pixel coords
 * (so the callout always lands on the right UI element regardless of display
 * scale). Used to show genuine app surfaces — API keys, wake word, outputs —
 * instead of mockups.
 */
export const AppShot: React.FC<{
  src: string;
  srcW: number;
  width: number;
  highlight?: Box;
  callout?: string;
  /** Source-pixel top-left of the callout pill. */
  calloutAt?: { x: number; y: number };
  highlightDelay?: number;
}> = ({ src, srcW, width, highlight, callout, calloutAt, highlightDelay = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.8 } });
  const hs = spring({ frame: frame - highlightDelay, fps, config: { damping: 200 } });
  const pulse = (Math.sin((frame - highlightDelay) / 12) + 1) / 2;
  const scale = width / srcW;

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
        transform: `translateY(${interpolate(enter, [0, 1], [28, 0])}px) scale(${interpolate(
          enter,
          [0, 1],
          [0.96, 1],
        )})`,
      }}
    >
      <Img src={staticFile(src)} style={{ width, display: "block" }} />

      {highlight && (
        <div
          style={{
            position: "absolute",
            left: highlight.x * scale,
            top: highlight.y * scale,
            width: highlight.w * scale,
            height: highlight.h * scale,
            borderRadius: 8,
            border: `2px solid ${COLORS.primary}`,
            boxShadow: `0 0 ${10 + pulse * 14}px ${COLORS.primaryGlow}`,
            backgroundColor: "rgba(255,214,10,0.08)",
            opacity: hs,
          }}
        />
      )}

      {callout && calloutAt && (
        <div
          style={{
            position: "absolute",
            left: calloutAt.x * scale,
            top: calloutAt.y * scale,
            padding: "7px 14px",
            borderRadius: 999,
            backgroundColor: COLORS.bgCard,
            border: `1px solid ${COLORS.primary}`,
            fontFamily: FONT,
            fontSize: 16,
            fontWeight: 700,
            color: COLORS.text,
            whiteSpace: "nowrap",
            opacity: hs,
            transform: `translateY(${interpolate(hs, [0, 1], [-8, 0])}px)`,
          }}
        >
          {callout}
        </div>
      )}
    </div>
  );
};
