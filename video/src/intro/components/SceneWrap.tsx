import { AbsoluteFill, interpolate, useCurrentFrame, useVideoConfig } from "remotion";

/**
 * Wraps a scene so it cleanly fades in at the start and out at the end of its
 * own Sequence. `useCurrentFrame`/`durationInFrames` are scene-local inside a
 * Sequence, so this needs no per-scene timing math.
 */
export const SceneWrap: React.FC<{
  children: React.ReactNode;
  fade?: number;
  padding?: number;
}> = ({ children, fade = 16, padding = 96 }) => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const opacity = interpolate(
    frame,
    [0, fade, durationInFrames - fade, durationInFrames - 1],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  return (
    <AbsoluteFill
      style={{
        opacity,
        // Top inset reserves a strip for the persistent PrototypeBadge so no
        // scene's content (e.g. a Kicker) collides with it. Horizontal inset is
        // the `padding` prop; bottom is kept smaller to maximise content height.
        paddingTop: 76,
        paddingBottom: 44,
        paddingLeft: padding,
        paddingRight: padding,
        justifyContent: "center",
        alignItems: "center",
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
