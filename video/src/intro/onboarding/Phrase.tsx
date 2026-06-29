import { AbsoluteFill, interpolate, useCurrentFrame } from "remotion";

/**
 * A kinetic-typography slot: fades + lifts in at `start`, holds, then fades out
 * at `end` (both scene-local frames). Used to crossfade a sequence of headline
 * phrases in time with the voiceover lines that narrate them.
 */
export const Phrase: React.FC<{
  start: number;
  end: number;
  fade?: number;
  children: React.ReactNode;
}> = ({ start, end, fade = 12, children }) => {
  const frame = useCurrentFrame();
  const opacity = interpolate(
    frame,
    [start, start + fade, end - fade, end],
    [0, 1, 1, 0],
    { extrapolateLeft: "clamp", extrapolateRight: "clamp" },
  );
  const ty = interpolate(frame, [start, start + fade], [18, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  return (
    <AbsoluteFill
      style={{
        opacity,
        justifyContent: "center",
        alignItems: "center",
        transform: `translateY(${ty}px)`,
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
