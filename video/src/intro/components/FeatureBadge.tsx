import { interpolate, spring, useCurrentFrame, useVideoConfig } from "remotion";
import { COLORS, FONT } from "../theme";
import { Icon } from "./Icons";

type IconName = React.ComponentProps<typeof Icon>["name"];

/** A pill with an icon + label that springs in at `delay`. */
export const FeatureBadge: React.FC<{
  icon: IconName;
  label: string;
  delay?: number;
}> = ({ icon, label, delay = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200, mass: 0.7 } });
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 14,
        padding: "18px 28px",
        borderRadius: 999,
        backgroundColor: COLORS.bgCard,
        border: `1px solid ${COLORS.border}`,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [20, 0])}px) scale(${interpolate(
          s,
          [0, 1],
          [0.9, 1],
        )})`,
      }}
    >
      <Icon name={icon} size={30} color={COLORS.primary} />
      <span
        style={{
          fontFamily: FONT,
          fontSize: 28,
          fontWeight: 700,
          color: COLORS.text,
          whiteSpace: "nowrap",
        }}
      >
        {label}
      </span>
    </div>
  );
};
