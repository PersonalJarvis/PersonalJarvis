import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Kicker, Subtitle, Title } from "../components/Text";
import { BrandTile } from "../components/BrandTile";
import { COLORS, FONT } from "../theme";

const BRANDS = [
  { slug: "telegram", label: "Telegram" },
  { slug: "discord", label: "Discord" },
  { slug: "gmail", label: "Gmail" },
  { slug: "googlecalendar", label: "Calendar" },
  { slug: "spotify", label: "Spotify" },
  { slug: "github", label: "GitHub" },
  { slug: "claude", label: "Claude" },
  { slug: "googlegemini", label: "Gemini" },
] as const;

export const Integrations: React.FC = () => {
  return (
    <SceneWrap>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 20,
          width: "100%",
        }}
      >
        <Kicker>Plugins</Kicker>
        <Title delay={8} size={50}>
          A plugin for everything
        </Title>
        <Subtitle delay={18} size={23}>
          Connect your tools — and new plugins land all the time.
        </Subtitle>

        <div
          style={{
            marginTop: 22,
            display: "flex",
            flexWrap: "wrap",
            justifyContent: "center",
            gap: "20px 34px",
            maxWidth: 350,
          }}
        >
          {BRANDS.map((b, i) => (
            <BrandTile key={b.slug} slug={b.slug} label={b.label} delay={30 + i * 8} />
          ))}
          <MoreTile delay={30 + BRANDS.length * 8} />
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
        transform: `translateY(${interpolate(s, [0, 1], [22, 0])}px) scale(${interpolate(
          s,
          [0, 1],
          [0.88, 1],
        )})`,
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
      <span style={{ fontFamily: FONT, fontSize: 18, fontWeight: 600, color: COLORS.textMuted }}>
        & more
      </span>
    </div>
  );
};
