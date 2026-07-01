import { Easing, Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";

/**
 * A framed screenshot that slowly zooms toward a focal point — establishes the
 * real UI, then pushes in so the meaningful region fills the frame (so its text
 * is physically large and survives platform re-compression). `focal` is 0..1 of
 * the source; `zoomWindow` are scene-local frames.
 */
export const ShotZoom: React.FC<{
  src: string;
  srcW: number;
  srcH: number;
  displayW: number;
  zoomTo: number;
  focal: { x: number; y: number };
  zoomWindow: [number, number];
  delay?: number;
}> = ({ src, srcW, srcH, displayW, zoomTo, focal, zoomWindow, delay = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.8 } });
  const displayH = (displayW * srcH) / srcW;
  const z = interpolate(frame, zoomWindow, [1, zoomTo], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: Easing.inOut(Easing.ease),
  });
  return (
    <div
      style={{
        width: displayW,
        height: displayH,
        borderRadius: 14,
        overflow: "hidden",
        border: `1px solid ${COLORS.borderStrong}`,
        boxShadow: `0 30px 80px rgba(0,0,0,0.55), 0 0 46px ${COLORS.primaryGlow}`,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [28, 0])}px) scale(${interpolate(
          enter,
          [0, 1],
          [0.97, 1],
        )})`,
      }}
    >
      <Img
        src={staticFile(src)}
        style={{
          width: displayW,
          height: displayH,
          display: "block",
          transform: `scale(${z})`,
          transformOrigin: `${focal.x * 100}% ${focal.y * 100}%`,
        }}
      />
    </div>
  );
};

/**
 * A framed crop of a screenshot (source-pixel region), scaled to `displayW`, so
 * one part of the UI fills the frame at readable size. Optional slow vertical
 * pan (`panBy` source px over `panWindow` frames) to read down a column.
 */
export const ShotCrop: React.FC<{
  src: string;
  srcW: number;
  crop: { x: number; y: number; w: number; h: number };
  displayW: number;
  delay?: number;
  panBy?: number;
  panWindow?: [number, number];
}> = ({ src, srcW, crop, displayW, delay = 0, panBy = 0, panWindow = [0, 1] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.8 } });
  const scale = displayW / crop.w;
  const displayH = crop.h * scale;
  const p = panBy
    ? interpolate(frame, panWindow, [0, 1], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
        easing: Easing.inOut(Easing.ease),
      })
    : 0;
  const offY = crop.y + p * panBy;
  return (
    <div
      style={{
        position: "relative",
        width: displayW,
        height: displayH,
        borderRadius: 14,
        overflow: "hidden",
        border: `1px solid ${COLORS.borderStrong}`,
        boxShadow: `0 30px 80px rgba(0,0,0,0.55), 0 0 46px ${COLORS.primaryGlow}`,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [24, 0])}px) scale(${interpolate(
          enter,
          [0, 1],
          [0.97, 1],
        )})`,
      }}
    >
      <Img
        src={staticFile(src)}
        style={{
          position: "absolute",
          width: srcW * scale,
          maxWidth: "none",
          left: -crop.x * scale,
          top: -offY * scale,
        }}
      />
    </div>
  );
};

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

/** A screen-fixed callout pill that springs in (position it via a wrapper). */
export const Pill: React.FC<{
  children: React.ReactNode;
  delay?: number;
  tone?: "gold" | "card";
  size?: number;
}> = ({ children, delay = 0, tone = "card", size = 26 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  const gold = tone === "gold";
  return (
    <div
      style={{
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [-8, 0])}px)`,
        padding: "9px 18px",
        borderRadius: 999,
        backgroundColor: gold ? "rgba(255,214,10,0.12)" : COLORS.bgCard,
        border: `1px solid ${gold ? COLORS.primary : COLORS.borderStrong}`,
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 700,
        color: gold ? COLORS.primary : COLORS.text,
        whiteSpace: "nowrap",
        boxShadow: "0 8px 30px rgba(0,0,0,0.45)",
      }}
    >
      {children}
    </div>
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
