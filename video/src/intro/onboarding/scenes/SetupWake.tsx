import { Sequence, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker, Title } from "../../components/Text";
import { SpokenCommand } from "../../components/SpokenCommand";
import { Icon } from "../../components/Icons";
import { COLORS, FONT } from "../../theme";
import { AppShot } from "../AppShot";
import { line, TimelineScene } from "../timeline";

/** Step 2 of setup: the wake word — shown on the REAL Settings wake-word panel. */
export const SetupWake: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const highlightAt = line(scene, "wake_2").localStart;
  const userAt = line(scene, "wake_2").localStart;
  const replyAt = userAt + 46;
  const doneAt = line(scene, "wake_3").localStart;

  return (
    <SceneWrap padding={70}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14, width: "100%" }}>
        <Kicker>Step 2 · Wake word</Kicker>
        <Title delay={8} size={38}>
          Set your wake word
        </Title>

        <AppShot
          src="shot-wake-crop.png"
          srcW={1578}
          width={930}
          highlight={{ x: 348, y: 92, w: 1192, h: 52 }}
          callout="Your wake phrase"
          calloutAt={{ x: 1000, y: 40 }}
          highlightDelay={highlightAt}
        />

        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
          <Sequence from={userAt} layout="none">
            <SpokenCommand text="Hey Ruben" speaker="user" size={26} compact />
          </Sequence>
          <Sequence from={replyAt} layout="none">
            <SpokenCommand text="Yes? I'm listening." speaker="jarvis" size={26} compact jarvisSrc="jarvis-logo.png" />
          </Sequence>
        </div>

        <Done delay={doneAt} />
      </div>
    </SceneWrap>
  );
};

const Done: React.FC<{ delay: number }> = ({ delay }) => {
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
      <Icon name="check" size={19} color={COLORS.good} />
      That's the whole setup — about a minute.
    </div>
  );
};
