import { COLORS, FONT } from "../theme";

/** A mock app/browser window with a traffic-light title bar. */
export const WindowFrame: React.FC<{
  title?: string;
  width?: number;
  height?: number;
  children?: React.ReactNode;
  accent?: boolean;
}> = ({ title = "", width = 880, height = 460, children, accent = false }) => {
  return (
    <div
      style={{
        width,
        height,
        borderRadius: 16,
        overflow: "hidden",
        backgroundColor: COLORS.bgElevated,
        border: `1px solid ${accent ? COLORS.primary : COLORS.borderStrong}`,
        boxShadow: accent
          ? `0 30px 80px rgba(0,0,0,0.55), 0 0 50px ${COLORS.primaryGlow}`
          : "0 30px 80px rgba(0,0,0,0.55)",
        display: "flex",
        flexDirection: "column",
      }}
    >
      <div
        style={{
          height: 46,
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "0 18px",
          backgroundColor: COLORS.bgCard,
          borderBottom: `1px solid ${COLORS.border}`,
        }}
      >
        {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => (
          <div
            key={c}
            style={{ width: 13, height: 13, borderRadius: "50%", backgroundColor: c }}
          />
        ))}
        <div
          style={{
            fontFamily: FONT,
            fontSize: 18,
            fontWeight: 600,
            color: COLORS.textMuted,
            marginLeft: 14,
          }}
        >
          {title}
        </div>
      </div>
      <div style={{ flex: 1, position: "relative", overflow: "hidden" }}>{children}</div>
    </div>
  );
};
