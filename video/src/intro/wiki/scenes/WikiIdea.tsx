import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Subtitle, Title } from "../../components/Text";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

const STEPS = ["You tell me something", "I file it away", "As plain Markdown"];

const Step: React.FC<{ label: string; delay: number; last: boolean }> = ({
  label,
  delay,
  last,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.7 } });
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 18, opacity: s }}>
      <div
        style={{
          padding: "16px 26px",
          borderRadius: 14,
          backgroundColor: COLORS.bgCard,
          border: `1px solid ${COLORS.border}`,
          fontFamily: FONT,
          fontSize: 26,
          fontWeight: 700,
          color: COLORS.text,
          transform: `translateY(${(1 - s) * 14}px)`,
        }}
      >
        {label}
      </div>
      {!last && <span style={{ color: COLORS.primary, fontSize: 30, fontWeight: 800 }}>→</span>}
    </div>
  );
};

/** The idea: I keep a wiki, in plain Markdown, and I write it myself. */
export const WikiIdea: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const steps = line(scene, "idea_2").localStart;
  const sub = line(scene, "idea_3").localStart;
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30 }}>
        <Kicker>The idea</Kicker>
        <Title delay={6} size={62}>
          I keep a wiki — and I write it myself.
        </Title>
        <div style={{ display: "flex", alignItems: "center", gap: 18, marginTop: 6 }}>
          {STEPS.map((label, i) => (
            <Step key={label} label={label} delay={steps + i * 14} last={i === STEPS.length - 1} />
          ))}
        </div>
        <Subtitle delay={sub} size={28}>
          I don&apos;t re-discover you on every question. I just look it up.
        </Subtitle>
      </div>
    </SceneWrap>
  );
};
