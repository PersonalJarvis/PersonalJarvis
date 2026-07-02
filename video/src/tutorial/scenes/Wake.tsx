import { SceneWrap } from "../../intro/components/SceneWrap";
import { SpokenCommand } from "../../intro/components/SpokenCommand";
import { AppShot } from "../../intro/onboarding/AppShot";
import { ChapterHeader } from "../components/ChapterHeader";
import { ProgressRail } from "../components/ProgressRail";
import { TimelineScene, line } from "../timeline";

/**
 * Step 03 — pick my name. The REAL wake-word setting, then the exchange that
 * proves it: you say the word, Jarvis answers.
 */
export const Wake: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const w2 = line(scene, "w2").localStart;
  const w3 = line(scene, "w3").localStart;
  const w4 = line(scene, "w4").localStart;

  return (
    <SceneWrap>
      <ChapterHeader num="03" title="Wake word" />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 26,
          marginTop: 58,
        }}
      >
        <AppShot
          src="shot-wake-crop.png"
          srcW={1578}
          width={760}
          highlight={{ x: 348, y: 92, w: 1192, h: 52 }}
          callout="Your wake phrase"
          calloutAt={{ x: 1000, y: 40 }}
          highlightDelay={w2}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 34 }}>
          <SpokenCommand text="Hey Jarvis." speaker="user" delay={w3} size={30} compact />
          <SpokenCommand
            text="Yes? I'm listening."
            speaker="jarvis"
            jarvisSrc="jarvis-logo.png"
            delay={w4}
            size={30}
            compact
          />
        </div>
      </div>
      <ProgressRail step={3} />
    </SceneWrap>
  );
};
