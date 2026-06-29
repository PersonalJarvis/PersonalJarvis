import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Title } from "../../components/Text";
import { BrandTile } from "../../components/BrandTile";
import { COLORS, FONT } from "../../theme";
import { line, TimelineScene } from "../timeline";

const BRANDS = [
  { slug: "gmail", label: "Gmail" },
  { slug: "googlecalendar", label: "Calendar" },
  { slug: "telegram", label: "Telegram" },
  { slug: "discord", label: "Discord" },
  { slug: "spotify", label: "Spotify" },
  { slug: "github", label: "GitHub" },
  { slug: "claude", label: "Claude" },
  { slug: "googlegemini", label: "Gemini" },
] as const;

/** A short breadth beat: real integrations, more arriving over time. */
export const PluginsYT: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const tilesAt = line(scene, "plug_2").localStart - 28;

  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 18, width: "100%" }}>
        <Kicker>Plugins</Kicker>
        <Title delay={8} size={50}>
          It plugs into everything
        </Title>

        <div
          style={{
            marginTop: 18,
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: "22px 40px",
            maxWidth: 560,
          }}
        >
          {BRANDS.map((b, i) => (
            <BrandTile key={b.slug} slug={b.slug} label={b.label} delay={tilesAt + i * 7} />
          ))}
          <MoreTile delay={tilesAt + BRANDS.length * 7} />
        </div>
      </div>
    </SceneWrap>
  );
};

const MoreTile: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.7 } });
  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        alignItems: "center",
        gap: 11,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [22, 0])}px) scale(${interpolate(s, [0, 1], [0.88, 1])})`,
      }}
    >
      <div
        style={{
          width: 82,
          height: 82,
          borderRadius: 20,
          backgroundColor: "rgba(255,214,10,0.08)",
          border: `2px dashed ${COLORS.primary}`,
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          fontSize: 40,
          fontWeight: 300,
          color: COLORS.primary,
        }}
      >
        +
      </div>
      <span style={{ fontFamily: FONT, fontSize: 18, fontWeight: 600, color: COLORS.textMuted }}>& more</span>
    </div>
  );
};
