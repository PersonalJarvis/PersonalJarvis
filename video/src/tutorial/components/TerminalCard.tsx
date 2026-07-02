import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../../intro/theme";

const MONO =
  "ui-monospace, SFMono-Regular, Menlo, Consolas, 'Liberation Mono', monospace";

/**
 * A clean terminal window that types the real install command character by
 * character (starting at `typeAt`), then flips a green ready-line at `doneAt`.
 * Frame-deterministic: the caret blink and typing are pure functions of the
 * current frame.
 */
export const TerminalCard: React.FC<{
  command: string;
  doneLine: string;
  typeAt: number;
  doneAt: number;
  width?: number;
}> = ({ command, doneLine, typeAt, doneAt, width = 900 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.8 } });

  const typed = Math.max(0, Math.floor((frame - typeAt) * 1.6));
  const shown = command.slice(0, typed);
  const typingDone = typed >= command.length;
  const caretOn = Math.floor(frame / 14) % 2 === 0;
  const done = frame >= doneAt;
  const ds = spring({ frame: frame - doneAt, fps, config: { damping: 200 } });

  return (
    <div
      style={{
        width,
        borderRadius: 16,
        overflow: "hidden",
        backgroundColor: "#101010",
        border: `1px solid ${COLORS.borderStrong}`,
        boxShadow: `0 30px 80px rgba(0,0,0,0.55), 0 0 40px ${COLORS.primaryGlow}`,
        opacity: enter,
        transform: `translateY(${interpolate(enter, [0, 1], [26, 0])}px)`,
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "13px 18px",
          borderBottom: `1px solid ${COLORS.border}`,
          backgroundColor: "#161616",
        }}
      >
        {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => (
          <div key={c} style={{ width: 12, height: 12, borderRadius: "50%", backgroundColor: c }} />
        ))}
        <span
          style={{
            fontFamily: FONT,
            fontSize: 15,
            fontWeight: 600,
            color: COLORS.textFaint,
            marginLeft: 10,
          }}
        >
          terminal
        </span>
      </div>
      <div style={{ padding: "26px 30px 30px", fontFamily: MONO, fontSize: 23, lineHeight: 1.75 }}>
        <div style={{ color: COLORS.text, wordBreak: "break-all" }}>
          <span style={{ color: COLORS.primary, fontWeight: 700 }}>$ </span>
          {shown}
          {!typingDone && caretOn && frame >= typeAt ? (
            <span style={{ color: COLORS.primary }}>▍</span>
          ) : null}
        </div>
        {done && (
          <div
            style={{
              color: COLORS.good,
              opacity: ds,
              transform: `translateY(${interpolate(ds, [0, 1], [8, 0])}px)`,
            }}
          >
            ✓ {doneLine}
          </div>
        )}
      </div>
    </div>
  );
};
