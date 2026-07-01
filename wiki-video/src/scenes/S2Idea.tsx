import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Body, Eyebrow, Gold, Headline } from "../components/type";
import { COLORS, EASE, INTER, lerp, MONO, TYPE } from "../theme";

const MAP: { a: string; b: string }[] = [
  { a: "The assistant", b: "is the editor" },
  { a: "Markdown files", b: "are the codebase" },
  { a: "Obsidian", b: "is the IDE" },
];

const MapRow: React.FC<{ a: string; b: string; delay: number }> = ({ a, b, delay }) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 16], [0, 1], EASE.outExpo);
  const x = lerp(frame, [delay, delay + 18], [26, 0], EASE.outExpo);
  return (
    <div
      style={{
        display: "flex",
        alignItems: "baseline",
        gap: 18,
        opacity: o,
        transform: `translateX(${x}px)`,
      }}
    >
      <div
        style={{
          fontFamily: MONO,
          fontSize: 30,
          color: COLORS.gold,
          minWidth: 300,
        }}
      >
        {a}
      </div>
      <div style={{ fontFamily: INTER, fontSize: 30, color: COLORS.body }}>{b}</div>
    </div>
  );
};

export const S2Idea: React.FC = () => {
  return (
    <AbsoluteFill style={{ justifyContent: "center", alignItems: "center" }}>
      <div style={{ width: "100%", maxWidth: 1240 }}>
        <Eyebrow delay={2}>The idea — Karpathy's LLM Wiki</Eyebrow>
        <div style={{ height: 24 }} />
        <Headline size={TYPE.h1} delay={8}>
          Your assistant <Gold>edits its own memory.</Gold>
        </Headline>
        <div style={{ height: 46 }} />

        <div style={{ display: "flex", flexDirection: "column", gap: 20 }}>
          {MAP.map((m, i) => (
            <MapRow key={m.a} a={m.a} b={m.b} delay={40 + i * 22} />
          ))}
        </div>

        <div style={{ height: 46 }} />
        <Body delay={124} maxWidth={1180} color={COLORS.faint}>
          Knowledge is compiled once and maintained continuously — not retrieved and
          re-derived on every single question.
        </Body>
      </div>
    </AbsoluteFill>
  );
};
