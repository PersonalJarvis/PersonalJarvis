import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker } from "../../components/Text";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

// app-home.png is 1920×1080; we display it at DW wide. The "Outputs" entry sits
// at the bottom of the sidebar — these are its pixel coords in the source image.
const SRC_W = 1920;
const DW = 920;
const SCALE = DW / SRC_W;
const OUTPUTS = { x: 14, y: 884, w: 244, h: 38 }; // source-pixel box of the sidebar row

/** A real screenshot of the actual desktop app, with the Outputs entry called out. */
export const RealApp: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.8 } });
  const highlightAt = line(scene, "app_2").localStart;

  return (
    <SceneWrap padding={70}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
        <Kicker>The real desktop app</Kicker>

        <div
          style={{
            position: "relative",
            width: DW,
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
          <Img src={staticFile("app-home.png")} style={{ width: DW, display: "block" }} />
          <OutputsCallout delay={highlightAt} />
        </div>
      </div>
    </SceneWrap>
  );
};

const OutputsCallout: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  const pulse = (Math.sin((frame - delay) / 12) + 1) / 2;
  const box = {
    left: OUTPUTS.x * SCALE,
    top: OUTPUTS.y * SCALE,
    width: OUTPUTS.w * SCALE,
    height: OUTPUTS.h * SCALE,
  };
  return (
    <>
      {/* highlight ring over the sidebar "Outputs" row */}
      <div
        style={{
          position: "absolute",
          left: box.left,
          top: box.top,
          width: box.width,
          height: box.height,
          borderRadius: 8,
          border: `2px solid ${COLORS.primary}`,
          boxShadow: `0 0 ${10 + pulse * 14}px ${COLORS.primaryGlow}`,
          backgroundColor: "rgba(255,214,10,0.10)",
          opacity: s,
        }}
      />
      {/* callout to the right */}
      <div
        style={{
          position: "absolute",
          left: box.left + box.width + 16,
          top: box.top + box.height / 2 - 19,
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 14px",
          borderRadius: 999,
          backgroundColor: COLORS.bgCard,
          border: `1px solid ${COLORS.primary}`,
          fontFamily: FONT,
          fontSize: 17,
          fontWeight: 700,
          color: COLORS.text,
          whiteSpace: "nowrap",
          opacity: s,
          transform: `translateX(${interpolate(s, [0, 1], [-10, 0])}px)`,
        }}
      >
        <span style={{ color: COLORS.primary, fontSize: 20 }}>←</span>
        Every result lands here
      </div>
    </>
  );
};
