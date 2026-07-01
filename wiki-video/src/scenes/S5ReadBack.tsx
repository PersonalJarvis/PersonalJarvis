import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Eyebrow, Gold, Headline } from "../components/type";
import { COLORS, EASE, INTER, lerp, MONO } from "../theme";

const Arrow: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 12], [0, 1], EASE.outExpo);
  return (
    <div
      style={{
        fontFamily: MONO,
        fontSize: 30,
        color: COLORS.gold,
        opacity: o,
        flexShrink: 0,
      }}
    >
      →
    </div>
  );
};

const EngChip: React.FC<{ title: string; sub: string; delay: number }> = ({
  title,
  sub,
  delay,
}) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 14], [0, 1], EASE.outExpo);
  const y = lerp(frame, [delay, delay + 16], [16, 0], EASE.outExpo);
  return (
    <div
      style={{
        flex: 1,
        opacity: o,
        transform: `translateY(${y}px)`,
        background: COLORS.panel,
        border: `1px solid ${COLORS.hairline}`,
        borderRadius: 14,
        padding: "18px 22px",
      }}
    >
      <div
        style={{
          fontFamily: INTER,
          fontWeight: 600,
          fontSize: 24,
          color: COLORS.headline,
          marginBottom: 8,
          display: "flex",
          alignItems: "center",
          gap: 10,
        }}
      >
        <span style={{ width: 8, height: 8, borderRadius: 99, background: COLORS.gold }} />
        {title}
      </div>
      <div style={{ fontFamily: INTER, fontSize: 19, color: COLORS.faint, lineHeight: 1.45 }}>
        {sub}
      </div>
    </div>
  );
};

export const S5ReadBack: React.FC = () => {
  const frame = useCurrentFrame();
  const qO = lerp(frame, [26, 44], [0, 1], EASE.outExpo);
  const searchO = lerp(frame, [50, 66], [0, 1], EASE.outExpo);
  const panelO = lerp(frame, [78, 96], [0, 1], EASE.outExpo);
  const panelY = lerp(frame, [78, 98], [18, 0], EASE.outExpo);

  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: "100%", maxWidth: 1560 }}>
      <Eyebrow delay={2}>Recall — read back in milliseconds</Eyebrow>
      <div style={{ height: 20 }} />
      <Headline size={54} delay={8}>
        Searched by <Gold>keywords, never vectors.</Gold>
      </Headline>

      <div style={{ height: 44 }} />

      {/* recall flow */}
      <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
        {/* query */}
        <div
          style={{
            opacity: qO,
            width: 360,
            background: COLORS.panel,
            border: `1px solid ${COLORS.hairline}`,
            borderRadius: 14,
            padding: "16px 20px",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 15, color: COLORS.faint, marginBottom: 6 }}>
            you ask
          </div>
          <div style={{ fontFamily: INTER, fontSize: 22, color: COLORS.body }}>
            “What music does Ruben like?”
          </div>
        </div>

        <Arrow delay={48} />

        {/* FTS5 */}
        <div
          style={{
            opacity: searchO,
            background: COLORS.panelHi,
            border: `1px solid ${COLORS.hairlineGold}`,
            borderRadius: 14,
            padding: "16px 20px",
            textAlign: "center",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 22, color: COLORS.gold }}>FTS5 · BM25</div>
          <div style={{ fontFamily: MONO, fontSize: 14, color: COLORS.faint, marginTop: 4 }}>
            keyword index
          </div>
        </div>

        <Arrow delay={76} />

        {/* injected context */}
        <div
          style={{
            opacity: panelO,
            transform: `translateY(${panelY}px)`,
            flex: 1,
            background: COLORS.bgDeep,
            border: `1px solid ${COLORS.hairline}`,
            borderRadius: 14,
            padding: "18px 22px",
          }}
        >
          <div style={{ fontFamily: MONO, fontSize: 20, lineHeight: 1.6, whiteSpace: "pre-wrap" }}>
            <span style={{ color: COLORS.gold }}>## Wiki context</span>
            {"\n"}
            <span style={{ color: COLORS.body }}>- Ruben prefers 80s music. </span>
            <span style={{ color: COLORS.gold }}>[[entities/ruben]]</span>
            {"\n\n"}
            <span style={{ color: COLORS.faint }}>…prepended before the assistant answers.</span>
          </div>
        </div>
      </div>

      {/* engineered for the real world */}
      <div style={{ height: 46 }} />
      <div
        style={{
          fontFamily: MONO,
          fontSize: 16,
          letterSpacing: 2,
          textTransform: "uppercase",
          color: COLORS.faint,
          opacity: lerp(frame, [120, 140], [0, 1], EASE.outExpo),
          marginBottom: 16,
        }}
      >
        Engineered for the real world
      </div>
      <div style={{ display: "flex", gap: 22 }}>
        <EngChip
          title="Cheap model by default"
          sub="Background curation never bills your frontier chat model."
          delay={140}
        />
        <EngChip
          title="Loud on failure"
          sub="A dead pipeline logs and bumps a counter — it never fails silently."
          delay={156}
        />
        <EngChip
          title="Self-healing chain"
          sub="Crosses provider families when a key is dead, throttled, or out of credit."
          delay={172}
        />
      </div>
      </div>
    </AbsoluteFill>
  );
};
