import { AbsoluteFill, useCurrentFrame } from "remotion";
import { Eyebrow, Gold, Headline } from "../components/type";
import { COLORS, EASE, INTER, lerp, MONO } from "../theme";

type Seg = { t: string; c: string };
// A trimmed but real entity page (frontmatter + sections + wikilinks).
const LINES: Seg[][] = [
  [{ t: "---", c: COLORS.faint }],
  [{ t: "type: ", c: COLORS.blue }, { t: "entity", c: COLORS.body }],
  [{ t: "entity_kind: ", c: COLORS.blue }, { t: "person", c: COLORS.body }],
  [{ t: "slug: ", c: COLORS.blue }, { t: "ruben", c: COLORS.body }],
  [{ t: "updated: ", c: COLORS.blue }, { t: "2026-07-01", c: COLORS.body }],
  [{ t: "---", c: COLORS.faint }],
  [{ t: "", c: COLORS.body }],
  [{ t: "# Ruben", c: COLORS.headline }],
  [{ t: "", c: COLORS.body }],
  [{ t: "## Facts", c: COLORS.gold }],
  [{ t: "- Maintainer of Personal Jarvis.", c: COLORS.body }],
  [{ t: "- Prefers 80s music; works reversibly.", c: COLORS.body }],
  [{ t: "", c: COLORS.body }],
  [{ t: "## Relationships", c: COLORS.gold }],
  [{ t: "- ", c: COLORS.body }, { t: "[[projects/personal-jarvis]]", c: COLORS.gold }],
  [{ t: "- ", c: COLORS.body }, { t: "[[concepts/llm-wiki]]", c: COLORS.gold }],
];

const CodeLine: React.FC<{ segs: Seg[]; delay: number }> = ({ segs, delay }) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 10], [0, 1], EASE.outExpo);
  const x = lerp(frame, [delay, delay + 12], [10, 0], EASE.outExpo);
  return (
    <div
      style={{
        opacity: o,
        transform: `translateX(${x}px)`,
        fontFamily: MONO,
        fontSize: 21,
        lineHeight: 1.5,
        whiteSpace: "pre",
        minHeight: 21 * 1.5,
      }}
    >
      {segs.map((s, i) => (
        <span key={i} style={{ color: s.c }}>
          {s.t}
        </span>
      ))}
    </div>
  );
};

const TypeChip: React.FC<{ label: string; delay: number }> = ({ label, delay }) => {
  const frame = useCurrentFrame();
  const o = lerp(frame, [delay, delay + 12], [0, 1], EASE.outExpo);
  const x = lerp(frame, [delay, delay + 14], [18, 0], EASE.outExpo);
  return (
    <div
      style={{
        fontFamily: MONO,
        fontSize: 20,
        color: COLORS.body,
        opacity: o,
        transform: `translateX(${x}px)`,
        display: "flex",
        alignItems: "center",
        gap: 12,
      }}
    >
      <span style={{ width: 7, height: 7, borderRadius: 2, background: COLORS.gold }} />
      {label}
    </div>
  );
};

export const S4Page: React.FC = () => {
  const frame = useCurrentFrame();
  const cardO = lerp(frame, [24, 42], [0, 1], EASE.outExpo);
  const cardY = lerp(frame, [24, 44], [24, 0], EASE.outExpo);
  const tagO = lerp(frame, [150, 172], [0, 1], EASE.outExpo);

  return (
    <AbsoluteFill style={{ justifyContent: "flex-start" }}>
      <Eyebrow delay={2}>The data model</Eyebrow>
      <div style={{ height: 20 }} />
      <Headline size={54} delay={8}>
        Every memory is a <Gold>Markdown page.</Gold>
      </Headline>

      <div style={{ height: 44 }} />

      <div style={{ display: "flex", gap: 64, alignItems: "flex-start" }}>
        {/* page card */}
        <div
          style={{
            opacity: cardO,
            transform: `translateY(${cardY}px)`,
            width: 860,
            background: COLORS.bgDeep,
            border: `1px solid ${COLORS.hairline}`,
            borderRadius: 16,
            boxShadow: "0 20px 60px rgba(0,0,0,0.45)",
            overflow: "hidden",
          }}
        >
          {/* editor title bar */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "14px 20px",
              borderBottom: `1px solid ${COLORS.hairline}`,
            }}
          >
            <span style={{ width: 12, height: 12, borderRadius: 99, background: "#3a3733" }} />
            <span style={{ width: 12, height: 12, borderRadius: 99, background: "#3a3733" }} />
            <span style={{ width: 12, height: 12, borderRadius: 99, background: "#3a3733" }} />
            <span
              style={{
                fontFamily: MONO,
                fontSize: 16,
                color: COLORS.faint,
                marginLeft: 10,
              }}
            >
              entities/ruben.md
            </span>
          </div>
          <div style={{ padding: "22px 26px" }}>
            {LINES.map((segs, i) => (
              <CodeLine key={i} segs={segs} delay={44 + i * 7} />
            ))}
          </div>
        </div>

        {/* right column: page types + tagline */}
        <div style={{ display: "flex", flexDirection: "column", gap: 18, paddingTop: 8 }}>
          <div
            style={{
              fontFamily: MONO,
              fontSize: 16,
              letterSpacing: 2,
              textTransform: "uppercase",
              color: COLORS.gold,
              opacity: lerp(frame, [60, 78], [0, 1], EASE.outExpo),
            }}
          >
            Page types
          </div>
          <TypeChip label="Entity" delay={72} />
          <TypeChip label="Concept" delay={84} />
          <TypeChip label="Project" delay={96} />
          <TypeChip label="Session" delay={108} />
          <TypeChip label="Person" delay={120} />

          <div
            style={{
              width: 240,
              height: 1,
              background: COLORS.hairline,
              margin: "10px 0",
              opacity: tagO,
            }}
          />
          <div
            style={{
              fontFamily: MONO,
              fontSize: 15,
              color: COLORS.faint,
              opacity: tagO,
              lineHeight: 1.5,
            }}
          >
            {"["}{"["}wikilinks{"]"}{"]"} build a graph.
            <br />
            Backlinks are the reverse index.
          </div>
          <div
            style={{
              fontFamily: INTER,
              fontWeight: 600,
              fontSize: 24,
              color: COLORS.headline,
              opacity: tagO,
              marginTop: 6,
            }}
          >
            Local. Git-diffable. Portable.
          </div>
        </div>
      </div>
    </AbsoluteFill>
  );
};
