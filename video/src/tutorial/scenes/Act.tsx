import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../intro/components/SceneWrap";
import { SpokenCommand } from "../../intro/components/SpokenCommand";
import { Icon } from "../../intro/components/Icons";
import { COLORS, FONT } from "../../intro/theme";
import { ChapterHeader } from "../components/ChapterHeader";
import { ProgressRail } from "../components/ProgressRail";
import { TimelineScene, line } from "../timeline";

const STEPS = [
  { icon: "cursor" as const, label: "moves the cursor" },
  { icon: "globe" as const, label: "clicks through the browser" },
  { icon: "check" as const, label: "reads the result back" },
];

/**
 * Step 05 — computer use. Command docks on top; the three action chips land
 * one by one while the narration walks through them.
 */
export const Act: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const a2 = line(scene, "a2").localStart;
  const a3 = line(scene, "a3").localStart;

  return (
    <SceneWrap>
      <ChapterHeader num="05" title="Let it act" />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 40,
          marginTop: 70,
        }}
      >
        <SpokenCommand
          text="Open the browser and check my pull requests."
          speaker="user"
          wake="Hey Jarvis"
          delay={a2}
          size={36}
        />
        <div style={{ display: "flex", gap: 18 }}>
          {STEPS.map((step, i) => {
            const at = a3 + 8 + i * 22;
            const s = spring({
              frame: frame - at,
              fps,
              config: { damping: 200, mass: 0.7 },
            });
            return (
              <div
                key={step.label}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 12,
                  padding: "14px 24px",
                  borderRadius: 16,
                  backgroundColor: COLORS.bgCard,
                  border: `1px solid ${COLORS.border}`,
                  opacity: s,
                  transform: `translateY(${interpolate(s, [0, 1], [20, 0])}px)`,
                }}
              >
                <Icon name={step.icon} size={26} color={COLORS.primary} />
                <span
                  style={{
                    fontFamily: FONT,
                    fontSize: 22,
                    fontWeight: 700,
                    color: COLORS.text,
                  }}
                >
                  {step.label}
                </span>
              </div>
            );
          })}
        </div>
        <div
          style={{
            fontFamily: FONT,
            fontSize: 21,
            fontWeight: 600,
            color: COLORS.textMuted,
            opacity: spring({ frame: frame - (a3 + 80), fps, config: { damping: 200 } }),
          }}
        >
          You watch every step — on your screen, in real time.
        </div>
      </div>
      <ProgressRail step={5} />
    </SceneWrap>
  );
};
