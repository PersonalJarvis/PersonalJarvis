import { interpolate, Sequence, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Title } from "../../components/Text";
import { SpokenCommand } from "../../components/SpokenCommand";
import { WindowFrame } from "../../components/WindowFrame";
import { Cursor } from "../../components/Cursor";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

const W = 900;
const H = 340;

/** Feature 01 — speak a command, watch Jarvis drive the browser. */
export const ComputerUseYT: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const cmd = line(scene, "cu_3");
  const commandAt = cmd.localStart;
  const demoAt = cmd.localStart + cmd.dur - 10;

  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14, width: "100%" }}>
        <Kicker>Feature 01 · Computer Use</Kicker>
        <Title delay={8} size={50}>
          It uses your computer
        </Title>

        <Sequence from={commandAt} layout="none">
          <SpokenCommand text="Open Chrome and find Elon Musk's latest posts." speaker="user" size={24} compact wake="Hey Ruben" />
        </Sequence>

        <Steps demoAt={demoAt} />

        <Sequence from={demoAt} layout="none">
          <Demo />
        </Sequence>
      </div>
    </SceneWrap>
  );
};

const STEP_LABELS = ["Looking at the screen", "Reading UI elements", "Clicking", "Verifying"];

const Steps: React.FC<{ demoAt: number }> = ({ demoAt }) => {
  const frame = useCurrentFrame();
  const ats = [demoAt + 6, demoAt + 50, demoAt + 110, demoAt + 175];
  return (
    <div style={{ display: "flex", gap: 10, marginTop: 2 }}>
      {STEP_LABELS.map((label, i) => {
        const on = frame >= ats[i];
        return (
          <div
            key={label}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 7,
              padding: "6px 14px",
              borderRadius: 999,
              border: `1px solid ${on ? "rgba(255,214,10,0.4)" : COLORS.border}`,
              backgroundColor: on ? "rgba(255,214,10,0.10)" : "transparent",
              fontFamily: FONT,
              fontSize: 15,
              fontWeight: 600,
              color: on ? COLORS.primary : COLORS.textFaint,
              transition: "none",
            }}
          >
            <span
              style={{
                width: 7,
                height: 7,
                borderRadius: "50%",
                backgroundColor: on ? COLORS.primary : COLORS.textFaint,
              }}
            />
            {label}
          </div>
        );
      })}
    </div>
  );
};

const Demo: React.FC = () => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        position: "relative",
        width: W,
        height: H,
        marginTop: 12,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [26, 0])}px)`,
      }}
    >
      <WindowFrame title="" width={W} height={H} accent>
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "10px 18px",
            borderBottom: `1px solid ${COLORS.border}`,
          }}
        >
          <div style={{ display: "flex", gap: 16, color: COLORS.textFaint, fontSize: 18 }}>
            <span>←</span>
            <span>→</span>
            <span>⟳</span>
          </div>
          <div
            style={{
              flex: 1,
              height: 32,
              borderRadius: 999,
              backgroundColor: "#0E0E0E",
              border: `1px solid ${COLORS.border}`,
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "0 16px",
              fontFamily: FONT,
              fontSize: 15,
              color: COLORS.textMuted,
            }}
          >
            <span style={{ fontSize: 12 }}>🔒</span> x.com/elonmusk
          </div>
        </div>
        <div style={{ padding: "14px 24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 8 }}>
            <div style={{ width: 48, height: 48, borderRadius: "50%", background: "linear-gradient(135deg,#555,#222)" }} />
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <span style={{ fontFamily: FONT, fontSize: 20, fontWeight: 800, color: COLORS.text }}>Elon Musk</span>
                <Verified />
              </div>
              <span style={{ fontFamily: FONT, fontSize: 14, color: COLORS.textFaint }}>@elonmusk · 200M followers</span>
            </div>
          </div>
          <Post time="2h" lines={[92, 64]} likes="148K" reposts="22K" />
          <Post time="5h" lines={[80, 96, 48]} likes="96K" reposts="11K" />
        </div>
      </WindowFrame>

      <Cursor
        keys={[
          { frame: 0, x: W - 70, y: 50 },
          { frame: 36, x: 250, y: 34 },
          { frame: 50, x: 250, y: 34, click: true },
          { frame: 100, x: 470, y: 210 },
          { frame: 118, x: 470, y: 210, click: true },
          { frame: 150, x: 470, y: 210 },
        ]}
      />
    </div>
  );
};

const Post: React.FC<{ time: string; lines: number[]; likes: string; reposts: string }> = ({
  time,
  lines,
  likes,
  reposts,
}) => (
  <div style={{ display: "flex", gap: 12, padding: "11px 0", borderTop: `1px solid ${COLORS.border}` }}>
    <div style={{ width: 34, height: 34, borderRadius: "50%", backgroundColor: "#333", flexShrink: 0 }} />
    <div style={{ flex: 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 7 }}>
        <span style={{ fontFamily: FONT, fontSize: 15, fontWeight: 700, color: COLORS.text }}>Elon Musk</span>
        <Verified />
        <span style={{ fontFamily: FONT, fontSize: 14, color: COLORS.textFaint }}>@elonmusk · {time}</span>
      </div>
      {lines.map((w, i) => (
        <div
          key={i}
          style={{ height: 8, width: `${w}%`, borderRadius: 5, backgroundColor: "rgba(255,255,255,0.16)", marginBottom: 7 }}
        />
      ))}
      <div style={{ display: "flex", gap: 24, marginTop: 4 }}>
        <Metric label="💬" value="1.2K" />
        <Metric label="🔁" value={reposts} />
        <Metric label="♥" value={likes} />
      </div>
    </div>
  </div>
);

const Metric: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <span style={{ fontFamily: FONT, fontSize: 13, color: COLORS.textFaint }}>
    {label} {value}
  </span>
);

const Verified: React.FC = () => (
  <span
    style={{
      width: 16,
      height: 16,
      borderRadius: "50%",
      backgroundColor: "#1D9BF0",
      color: "#fff",
      fontSize: 10,
      fontWeight: 900,
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
    }}
  >
    ✓
  </span>
);
