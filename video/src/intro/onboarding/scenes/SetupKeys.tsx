import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Title } from "../../components/Text";
import { Icon } from "../../components/Icons";
import { COLORS, FONT } from "../../theme";
import { AppShot } from "../AppShot";
import { line, TimelineScene } from "../timeline";

/**
 * Step 1 of setup: paste your provider keys — shown on the REAL API Keys view.
 *
 * Jarvis takes four *kinds* of keys, not one: a brain that thinks, background
 * workers for the big jobs, a voice that speaks, and ears that listen — and for
 * each you pick any provider. The four category chips under the screenshot make
 * that "four sets, not one" point visually: they light up in signal-yellow one
 * by one as the narration names them.
 */
export const SetupKeys: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const namedAt = line(scene, "keys_3").localStart; // chips light up one by one
  const highlightAt = line(scene, "keys_4").localStart;
  const captionAt = line(scene, "keys_5").localStart;

  return (
    <SceneWrap padding={70}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14, width: "100%" }}>
        <Kicker>Step 1 · API keys</Kicker>
        <Title delay={8} size={38}>
          Connect your AI providers
        </Title>

        <AppShot
          src="shot-apikeys.png"
          srcW={1578}
          width={700}
          highlight={{ x: 314, y: 289, w: 1100, h: 48 }}
          callout="Paste your API key"
          calloutAt={{ x: 980, y: 214 }}
          highlightDelay={highlightAt}
        />

        <CategoryChips litAt={namedAt} />
        <Caption delay={captionAt} />
      </div>
    </SceneWrap>
  );
};

/** The four provider classes a user supplies keys for, in narration order. */
const CHIPS = [
  { icon: "brain", label: "Brain", sub: "thinks" },
  { icon: "robot", label: "Workers", sub: "do the big jobs" },
  { icon: "speaker", label: "Voice", sub: "speaks" },
  { icon: "mic", label: "Hearing", sub: "listens" },
] as const;

/** Four key-category chips that appear in sequence as the narration names them,
 *  each lighting up in signal-yellow — the visual proof that Jarvis takes four
 *  sets of keys, not just one for the brain. */
const CategoryChips: React.FC<{ litAt: number }> = ({ litAt }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  return (
    <div style={{ display: "flex", gap: 12 }}>
      {CHIPS.map((c, i) => {
        const s = spring({ frame: frame - (litAt + i * 14), fps, config: { damping: 200, mass: 0.7 } });
        return (
          <div
            key={c.label}
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "9px 15px",
              borderRadius: 14,
              backgroundColor: COLORS.bgCard,
              border: `1px solid rgba(255,214,10,${interpolate(s, [0, 1], [0.12, 0.55])})`,
              boxShadow: `0 0 ${interpolate(s, [0, 1], [0, 18])}px ${COLORS.primaryGlow}`,
              opacity: interpolate(s, [0, 1], [0, 1]),
              transform: `translateY(${interpolate(s, [0, 1], [14, 0])}px)`,
            }}
          >
            <Icon name={c.icon} size={22} color={COLORS.primary} />
            <div style={{ display: "flex", flexDirection: "column", lineHeight: 1.15 }}>
              <span style={{ fontFamily: FONT, fontWeight: 700, fontSize: 17, color: COLORS.text }}>
                {c.label}
              </span>
              <span style={{ fontFamily: FONT, fontWeight: 500, fontSize: 12, color: COLORS.textMuted }}>
                {c.sub}
              </span>
            </div>
          </div>
        );
      })}
    </div>
  );
};

const Caption: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 10,
        opacity: s,
        fontFamily: FONT,
        fontSize: 18,
        fontWeight: 600,
        color: COLORS.textMuted,
      }}
    >
      <Icon name="check" size={20} color={COLORS.good} />
      Bring your own keys — provider-agnostic, never locked in.
    </div>
  );
};
