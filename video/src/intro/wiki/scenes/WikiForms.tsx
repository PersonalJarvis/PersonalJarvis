import { spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Subtitle, Title } from "../../components/Text";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

const Stage: React.FC<{
  tag: string;
  title: string;
  sub: string;
  delay: number;
}> = ({ tag, title, sub, delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.7 } });
  return (
    <div
      style={{
        width: 340,
        opacity: s,
        transform: `translateY(${(1 - s) * 16}px)`,
        backgroundColor: COLORS.bgCard,
        border: `1px solid ${COLORS.border}`,
        borderRadius: 16,
        padding: "22px 26px",
      }}
    >
      <div
        style={{
          fontFamily: FONT,
          fontSize: 15,
          letterSpacing: 3,
          textTransform: "uppercase",
          fontWeight: 700,
          color: COLORS.primary,
          marginBottom: 10,
        }}
      >
        {tag}
      </div>
      <div style={{ fontFamily: FONT, fontSize: 27, fontWeight: 700, color: COLORS.text }}>
        {title}
      </div>
      <div style={{ fontFamily: FONT, fontSize: 19, color: COLORS.textMuted, marginTop: 8, lineHeight: 1.4 }}>
        {sub}
      </div>
    </div>
  );
};

const Arrow: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <span style={{ color: COLORS.primary, fontSize: 40, fontWeight: 800, opacity: s }}>→</span>
  );
};

/** How a memory forms: a cheap grab pass, then a slower judge pass — off-path. */
export const WikiForms: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const flow = line(scene, "forms_2").localStart;
  const sub = line(scene, "forms_3").localStart;
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30 }}>
        <Kicker>While you&apos;re away</Kicker>
        <Title delay={6} size={58}>
          New memories form in the background.
        </Title>
        <div style={{ display: "flex", alignItems: "center", gap: 22, marginTop: 4 }}>
          <Stage
            tag="Stage 1 · fast"
            title="Grab the facts"
            sub="A cheap pass on every turn — add-only, never destructive."
            delay={flow}
          />
          <Arrow delay={flow + 16} />
          <Stage
            tag="Stage 2 · slow"
            title="Keep, update, or drop"
            sub="A slower judge merges, replaces, or discards each fact."
            delay={flow + 26}
          />
        </div>
        <Subtitle delay={sub} size={27}>
          So I capture everything that matters — and never bury it in junk.
        </Subtitle>
      </div>
    </SceneWrap>
  );
};
