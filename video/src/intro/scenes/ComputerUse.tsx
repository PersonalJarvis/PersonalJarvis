import { interpolate, Sequence, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Kicker, Title } from "../components/Text";
import { SpokenCommand } from "../components/SpokenCommand";
import { WindowFrame } from "../components/WindowFrame";
import { Cursor } from "../components/Cursor";
import { COLORS, FONT } from "../theme";

const W = 900;
const H = 384;

export const ComputerUse: React.FC = () => {
  return (
    <SceneWrap>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 16,
          width: "100%",
        }}
      >
        <Kicker>Computer use</Kicker>
        <Title delay={8} size={52}>
          It can use your computer
        </Title>

        <Sequence from={32} layout="none">
          <SpokenCommand
            text="Open Chrome and find Elon Musk’s latest posts."
            speaker="user"
            size={26}
            compact
          />
        </Sequence>

        <Sequence from={66} layout="none">
          <Demo />
        </Sequence>
      </div>
    </SceneWrap>
  );
};

const Post: React.FC<{ time: string; lines: number[]; likes: string; reposts: string }> = ({
  time,
  lines,
  likes,
  reposts,
}) => (
  <div style={{ display: "flex", gap: 12, padding: "14px 0", borderTop: `1px solid ${COLORS.border}` }}>
    <div style={{ width: 38, height: 38, borderRadius: "50%", backgroundColor: "#333", flexShrink: 0 }} />
    <div style={{ flex: 1 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 7 }}>
        <span style={{ fontFamily: FONT, fontSize: 16, fontWeight: 700, color: COLORS.text }}>
          Elon Musk
        </span>
        <Verified />
        <span style={{ fontFamily: FONT, fontSize: 15, color: COLORS.textFaint }}>
          @elonmusk · {time}
        </span>
      </div>
      {lines.map((w, i) => (
        <div
          key={i}
          style={{
            height: 9,
            width: `${w}%`,
            borderRadius: 5,
            backgroundColor: "rgba(255,255,255,0.16)",
            marginBottom: 8,
          }}
        />
      ))}
      <div style={{ display: "flex", gap: 26, marginTop: 6 }}>
        <Metric label={`💬`} value="1.2K" />
        <Metric label={`🔁`} value={reposts} />
        <Metric label={`♥`} value={likes} />
      </div>
    </div>
  </div>
);

const Metric: React.FC<{ label: string; value: string }> = ({ label, value }) => (
  <span style={{ fontFamily: FONT, fontSize: 14, color: COLORS.textFaint }}>
    {label} {value}
  </span>
);

const Verified: React.FC = () => (
  <span
    style={{
      width: 17,
      height: 17,
      borderRadius: "50%",
      backgroundColor: "#1D9BF0",
      color: "#fff",
      fontSize: 11,
      fontWeight: 900,
      display: "inline-flex",
      alignItems: "center",
      justifyContent: "center",
    }}
  >
    ✓
  </span>
);

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
        marginTop: 18,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [26, 0])}px)`,
      }}
    >
      <WindowFrame title="" width={W} height={H} accent>
        {/* chrome toolbar */}
        <div
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 18px",
            borderBottom: `1px solid ${COLORS.border}`,
          }}
        >
          <div style={{ display: "flex", gap: 16, color: COLORS.textFaint, fontSize: 20 }}>
            <span>←</span>
            <span>→</span>
            <span>⟳</span>
          </div>
          <div
            style={{
              flex: 1,
              height: 34,
              borderRadius: 999,
              backgroundColor: "#0E0E0E",
              border: `1px solid ${COLORS.border}`,
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "0 16px",
              fontFamily: FONT,
              fontSize: 16,
              color: COLORS.textMuted,
            }}
          >
            <span style={{ fontSize: 13 }}>🔒</span> x.com/elonmusk
          </div>
        </div>
        {/* X profile */}
        <div style={{ padding: "16px 24px" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 6 }}>
            <div
              style={{
                width: 52,
                height: 52,
                borderRadius: "50%",
                background: "linear-gradient(135deg,#555,#222)",
              }}
            />
            <div>
              <div style={{ display: "flex", alignItems: "center", gap: 7 }}>
                <span style={{ fontFamily: FONT, fontSize: 21, fontWeight: 800, color: COLORS.text }}>
                  Elon Musk
                </span>
                <Verified />
              </div>
              <span style={{ fontFamily: FONT, fontSize: 15, color: COLORS.textFaint }}>
                @elonmusk · 200M followers
              </span>
            </div>
          </div>
          <Post time="2h" lines={[92, 64]} likes="148K" reposts="22K" />
          <Post time="5h" lines={[80, 96, 48]} likes="96K" reposts="11K" />
        </div>
      </WindowFrame>

      <Cursor
        keys={[
          { frame: 0, x: W - 70, y: 60 },
          { frame: 40, x: 250, y: 40 },
          { frame: 58, x: 250, y: 40, click: true },
          { frame: 90, x: 470, y: 250 },
        ]}
      />
    </div>
  );
};
