import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { ChatBubble } from "../../components/ChatBubble";
import { Kicker, Title } from "../../components/Text";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

/** Small "· from my wiki" tag under the assistant reply. */
const SourceTag: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        opacity: s,
        alignSelf: "flex-start",
        marginTop: 2,
        marginLeft: 8,
        display: "flex",
        alignItems: "center",
        gap: 8,
        fontFamily: FONT,
        fontSize: 18,
        fontWeight: 600,
        color: COLORS.primary,
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: 99, backgroundColor: COLORS.primary }} />
      read straight from my wiki
    </div>
  );
};

/** Recall: I search my own notes and answer from what I actually know. */
export const WikiRecall: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const q = line(scene, "recall_q").localStart;
  const a = line(scene, "recall_a").localStart;
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 24 }}>
        <Kicker>Recall</Kicker>
        <Title delay={6} size={54}>
          So I don&apos;t guess. I look it up.
        </Title>
        <div
          style={{
            width: 760,
            display: "flex",
            flexDirection: "column",
            gap: 16,
            marginTop: 8,
          }}
        >
          <ChatBubble from="user" delay={q} size={28}>
            What&apos;s BridgeMind again?
          </ChatBubble>
          <ChatBubble from="assistant" delay={a} size={28}>
            Your desktop app and Discord server.
          </ChatBubble>
          <SourceTag delay={a + 14} />
        </div>
      </div>
    </SceneWrap>
  );
};
