import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../intro/components/SceneWrap";
import { COLORS, FONT } from "../../intro/theme";
import { ChapterHeader } from "../components/ChapterHeader";
import { ProgressRail } from "../components/ProgressRail";
import { TerminalCard } from "../components/TerminalCard";
import { TimelineScene, line } from "../timeline";

// Must match the README quick-install (source of truth: the website repo).
const INSTALL_CMD =
  'pipx install "git+https://github.com/PersonalJarvis/PersonalJarvis" && jarvis serve';

const PLATFORMS = ["Windows", "macOS", "Linux"] as const;

/** Step 01 — one real command, every platform, wizard takes over. */
export const Install: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const i2 = line(scene, "i2").localStart;
  const i3 = line(scene, "i3").localStart;

  return (
    <SceneWrap>
      <ChapterHeader num="01" title="Install" />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 30,
          marginTop: 64,
        }}
      >
        <TerminalCard
          command={INSTALL_CMD}
          doneLine="installed — the setup wizard takes it from here"
          typeAt={i2 + 4}
          doneAt={i3 + 6}
        />
        <div style={{ display: "flex", gap: 16 }}>
          {PLATFORMS.map((label, i) => {
            const s = spring({
              frame: frame - (i2 + 20 + i * 7),
              fps,
              config: { damping: 200, mass: 0.7 },
            });
            return (
              <div
                key={label}
                style={{
                  padding: "10px 24px",
                  borderRadius: 999,
                  backgroundColor: COLORS.bgCard,
                  border: `1px solid ${COLORS.border}`,
                  fontFamily: FONT,
                  fontSize: 21,
                  fontWeight: 700,
                  color: COLORS.text,
                  opacity: s,
                  transform: `translateY(${interpolate(s, [0, 1], [16, 0])}px)`,
                }}
              >
                {label}
              </div>
            );
          })}
        </div>
      </div>
      <ProgressRail step={1} />
    </SceneWrap>
  );
};
