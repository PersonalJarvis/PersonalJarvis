import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";
import { COLORS, FONT } from "../../theme";
import { Mascot } from "../Mascot";
import { Subtitle, Title } from "../../components/Text";
import { Phrase } from "../Phrase";
import { line, TimelineScene } from "../timeline";

const IDS = [
  "concept_1",
  "concept_2",
  "concept_3",
  "concept_4",
  "concept_5",
  "concept_6",
  "concept_7",
] as const;

/** The pitch — six crossfading headlines, each timed to its narration line. */
export const Concept: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const span = (i: number) => {
    const start = line(scene, IDS[i]).localStart;
    const end = i < IDS.length - 1 ? line(scene, IDS[i + 1]).localStart : scene.dur;
    return { start, end };
  };

  return (
    <AbsoluteFill>
      <TopOrb dur={scene.dur} />

      <Phrase {...span(0)}>
        <Title size={62}>An AI agent that lives on your computer.</Title>
      </Phrase>

      <Phrase {...span(1)}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
          <Title size={66}>
            You talk to it. <Accent>It talks back.</Accent>
          </Title>
          <Subtitle size={28}>Completely hands-free.</Subtitle>
        </div>
      </Phrase>

      <Phrase {...span(2)}>
        <Title size={60}>More than a realtime voice model.</Title>
      </Phrase>

      <Phrase {...span(3)}>
        <Title size={84}>
          <Accent>A full agent orchestrator.</Accent>
        </Title>
      </Phrase>

      <Phrase {...span(4)}>
        <Title size={58}>
          It works in the background — and <Accent>gets things done.</Accent>
        </Title>
      </Phrase>

      <Phrase {...span(5)}>
        <Title size={58}>It runs everywhere — desktop, server, or a browser tab.</Title>
      </Phrase>

      <Phrase {...span(6)}>
        <Title size={64}>A home for you, on your machine.</Title>
      </Phrase>
    </AbsoluteFill>
  );
};

const Accent: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span style={{ color: COLORS.primary }}>{children}</span>
);

const TopOrb: React.FC<{ dur: number }> = ({ dur }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(frame, [0, 14, dur - 14, dur], [0, 1, 1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-start", paddingTop: 64, opacity }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
        <Mascot size={88} />
        <span
          style={{
            fontFamily: FONT,
            fontSize: 15,
            letterSpacing: 4,
            textTransform: "uppercase",
            fontWeight: 700,
            color: COLORS.textFaint,
          }}
        >
          Personal Jarvis
        </span>
      </div>
    </AbsoluteFill>
  );
};
