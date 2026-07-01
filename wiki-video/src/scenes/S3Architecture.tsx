import { AbsoluteFill, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { Eyebrow, Gold, Headline } from "../components/type";
import { COLORS, EASE, INTER, lerp, MONO } from "../theme";

const Node: React.FC<{
  title: string;
  sub: string;
  delay: number;
  accent?: boolean;
  width?: number;
}> = ({ title, sub, delay, accent = false, width = 236 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 18, mass: 0.6, stiffness: 180 } });
  const o = lerp(frame, [delay, delay + 12], [0, 1], EASE.outExpo);
  return (
    <div
      style={{
        width,
        opacity: o,
        transform: `scale(${0.85 + s * 0.15})`,
        background: accent ? COLORS.panelHi : COLORS.panel,
        border: `1px solid ${accent ? COLORS.hairlineGold : COLORS.hairline}`,
        borderRadius: 16,
        padding: "20px 22px",
        boxShadow: accent ? "0 8px 40px rgba(231,196,110,0.10)" : "0 8px 30px rgba(0,0,0,0.35)",
        position: "relative",
      }}
    >
      {accent && (
        <div
          style={{
            position: "absolute",
            top: 0,
            left: 18,
            right: 18,
            height: 3,
            borderRadius: 3,
            background: `linear-gradient(90deg, transparent, ${COLORS.gold}, transparent)`,
          }}
        />
      )}
      <div
        style={{
          fontFamily: INTER,
          fontWeight: 600,
          fontSize: 26,
          color: accent ? COLORS.headline : COLORS.body,
          marginBottom: 8,
        }}
      >
        {title}
      </div>
      <div style={{ fontFamily: MONO, fontSize: 16, color: COLORS.faint, lineHeight: 1.35 }}>
        {sub}
      </div>
    </div>
  );
};

/** Connector line that fills left→right with a gold pulse dot leading the fill. */
const Connector: React.FC<{ delay: number; width?: number }> = ({ delay, width = 44 }) => {
  const frame = useCurrentFrame();
  const p = lerp(frame, [delay, delay + 16], [0, 1], EASE.outQuint);
  return (
    <div style={{ width, height: 3, position: "relative", flexShrink: 0 }}>
      <div style={{ position: "absolute", inset: 0, background: COLORS.hairline, borderRadius: 3 }} />
      <div
        style={{
          position: "absolute",
          top: 0,
          left: 0,
          height: 3,
          width: `${p * 100}%`,
          background: COLORS.gold,
          borderRadius: 3,
          boxShadow: `0 0 8px ${COLORS.gold}`,
        }}
      />
    </div>
  );
};

const VERDICTS: { label: string; color: string }[] = [
  { label: "ADD", color: COLORS.green },
  { label: "UPDATE", color: COLORS.gold },
  { label: "NOOP", color: COLORS.faint },
  { label: "INVALIDATE", color: COLORS.red },
];

const Verdict: React.FC<{ label: string; color: string; delay: number }> = ({
  label,
  color,
  delay,
}) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 12], [0, 1], EASE.outExpo);
  const y = lerp(frame, [delay, delay + 14], [10, 0], EASE.outExpo);
  return (
    <div
      style={{
        fontFamily: MONO,
        fontSize: 16,
        color,
        border: `1px solid ${color}`,
        borderRadius: 999,
        padding: "5px 14px",
        opacity: o,
        transform: `translateY(${y}px)`,
        background: "rgba(0,0,0,0.25)",
      }}
    >
      {label}
    </div>
  );
};

export const S3Architecture: React.FC = () => {
  const frame = useCurrentFrame();
  const noteO = lerp(frame, [30, 50], [0, 1], EASE.outExpo);
  return (
    <AbsoluteFill style={{ justifyContent: "flex-start" }}>
      <Eyebrow delay={2}>Architecture — the sleep-time curator</Eyebrow>
      <div style={{ height: 22 }} />
      <Headline size={58} delay={8}>
        Two stages, <Gold>off the voice path.</Gold>
      </Headline>

      <div style={{ height: 88 }} />

      {/* pipeline row */}
      <div style={{ display: "flex", alignItems: "center", gap: 0 }}>
        <Node title="Conversation" sub="voice · chat" delay={26} width={188} />
        <Connector delay={40} />
        <Node title="Stage 1 · Extractor" sub={"cheap model\nADD-only"} delay={54} accent />
        <Connector delay={74} />
        <Node title="Journal" sub={"SQLite\nsurvives restart"} delay={88} width={188} />
        <Connector delay={104} />
        <Node title="Stage 2 · Consolidator" sub={"body-aware judge\nk-nearest pages"} delay={118} accent />
        <Connector delay={150} />
        <Node title="Vault" sub={"atomic write\nMarkdown"} delay={164} width={188} />
      </div>

      {/* verdict chips under stage 2 */}
      <div style={{ height: 40 }} />
      <div style={{ display: "flex", alignItems: "center", gap: 12, paddingLeft: 6 }}>
        <div style={{ fontFamily: MONO, fontSize: 16, color: COLORS.faint, marginRight: 6 }}>
          per fact →
        </div>
        {VERDICTS.map((v, i) => (
          <Verdict key={v.label} label={v.label} color={v.color} delay={140 + i * 14} />
        ))}
      </div>

      <div style={{ height: 44 }} />
      <div
        style={{
          fontFamily: INTER,
          fontSize: 28,
          color: COLORS.faint,
          opacity: noteO,
          maxWidth: 1220,
          lineHeight: 1.5,
        }}
      >
        A cheap, ADD-only pass runs on every turn; the expensive judgment is batched
        later — so a bad extractor can never corrupt what you already know.
      </div>
    </AbsoluteFill>
  );
};
