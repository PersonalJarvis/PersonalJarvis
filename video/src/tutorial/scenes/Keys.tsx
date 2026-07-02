import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { SceneWrap } from "../../intro/components/SceneWrap";
import { AppShot } from "../../intro/onboarding/AppShot";
import { COLORS, FONT } from "../../intro/theme";
import { ChapterHeader } from "../components/ChapterHeader";
import { ProgressRail } from "../components/ProgressRail";
import { TimelineScene, line } from "../timeline";

const PROVIDERS = ["Claude", "OpenAI", "Gemini", "OpenRouter"] as const;

/**
 * Step 02 — bring your own key. Provider pills make the "any single key"
 * promise concrete, then the REAL API-Keys view (genuine screenshot) shows
 * exactly where it goes.
 */
export const Keys: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const k2 = line(scene, "k2").localStart;
  const k3 = line(scene, "k3").localStart;

  return (
    <SceneWrap>
      <ChapterHeader num="02" title="Your key" />
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          alignItems: "center",
          gap: 22,
          // Clears the chapter header vertically — the header occupies the
          // top-left ~190px and the pill row is horizontally centered.
          marginTop: 74,
        }}
      >
        <div style={{ display: "flex", gap: 14 }}>
          {PROVIDERS.map((label, i) => {
            const s = spring({
              frame: frame - (k2 + 6 + i * 8),
              fps,
              config: { damping: 200, mass: 0.7 },
            });
            return (
              <div
                key={label}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 10,
                  padding: "10px 22px",
                  borderRadius: 999,
                  backgroundColor: COLORS.bgCard,
                  border: `1px solid rgba(255,214,10,0.35)`,
                  fontFamily: FONT,
                  fontSize: 21,
                  fontWeight: 700,
                  color: COLORS.text,
                  opacity: s,
                  transform: `translateY(${interpolate(s, [0, 1], [16, 0])}px)`,
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    backgroundColor: COLORS.primary,
                  }}
                />
                {label}
              </div>
            );
          })}
          <div
            style={{
              alignSelf: "center",
              fontFamily: FONT,
              fontSize: 19,
              fontWeight: 600,
              color: COLORS.textMuted,
              opacity: spring({ frame: frame - (k2 + 40), fps, config: { damping: 200 } }),
            }}
          >
            — one is enough
          </div>
        </div>
        <AppShot
          src="shot-apikeys.png"
          srcW={1578}
          width={620}
          highlight={{ x: 314, y: 289, w: 1100, h: 48 }}
          callout="Paste your API key"
          calloutAt={{ x: 980, y: 214 }}
          highlightDelay={k3}
        />
      </div>
      <ProgressRail step={2} />
    </SceneWrap>
  );
};
