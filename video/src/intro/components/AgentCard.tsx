import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";
import { Icon } from "./Icons";

/**
 * A sub-agent card that springs in at `delay`, runs a progress bar, then flips
 * to a "done" check at `doneAt` (frames, scene-local).
 */
export const AgentCard: React.FC<{
  title: string;
  task: string;
  delay?: number;
  doneAt?: number;
  width?: number;
}> = ({ title, task, delay = 0, doneAt = 9999, width = 360 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.7 } });
  const local = frame - delay;
  const done = frame >= doneAt;
  const progress = done
    ? 1
    : interpolate(local, [6, doneAt - delay], [0, 0.92], {
        extrapolateLeft: "clamp",
        extrapolateRight: "clamp",
      });

  return (
    <div
      style={{
        width,
        padding: 24,
        borderRadius: 18,
        backgroundColor: COLORS.bgCard,
        border: `1px solid ${done ? COLORS.good : COLORS.border}`,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [26, 0])}px) scale(${interpolate(
          s,
          [0, 1],
          [0.92, 1],
        )})`,
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 14, marginBottom: 14 }}>
        <div
          style={{
            width: 44,
            height: 44,
            borderRadius: 12,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            backgroundColor: done ? "rgba(74,222,128,0.15)" : "rgba(255,214,10,0.12)",
          }}
        >
          <Icon name={done ? "check" : "robot"} size={26} color={done ? COLORS.good : COLORS.primary} />
        </div>
        <div style={{ flex: 1 }}>
          <div style={{ fontFamily: FONT, fontSize: 24, fontWeight: 700, color: COLORS.text }}>
            {title}
          </div>
          <div style={{ fontFamily: FONT, fontSize: 17, fontWeight: 500, color: COLORS.textMuted }}>
            {done ? "Done" : "Working…"}
          </div>
        </div>
      </div>
      <div style={{ fontFamily: FONT, fontSize: 18, fontWeight: 500, color: COLORS.textFaint, marginBottom: 16 }}>
        {task}
      </div>
      <div style={{ height: 8, borderRadius: 8, backgroundColor: "rgba(255,255,255,0.08)", overflow: "hidden" }}>
        <div
          style={{
            width: `${progress * 100}%`,
            height: "100%",
            borderRadius: 8,
            backgroundColor: done ? COLORS.good : COLORS.primary,
          }}
        />
      </div>
    </div>
  );
};
