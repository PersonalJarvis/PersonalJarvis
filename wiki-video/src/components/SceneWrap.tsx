import { AbsoluteFill, useCurrentFrame } from "remotion";
import { EASE, lerp, MARGIN } from "../theme";

/**
 * Wraps a scene's content with a standard crossfade: fade+rise in over the first
 * `fadeIn` frames, fade out over the last `fadeOut` frames of its own duration.
 * When consecutive scenes overlap in time, the outgoing fade-out and incoming
 * fade-in cross — so transitions are clean and there is no hanging/empty frame.
 */
export const SceneWrap: React.FC<{
  durationInFrames: number;
  fadeIn?: number;
  fadeOut?: number;
  pad?: boolean;
  children: React.ReactNode;
}> = ({ durationInFrames, fadeIn = 18, fadeOut = 20, pad = true, children }) => {
  const frame = useCurrentFrame();
  const inO = lerp(frame, [0, fadeIn], [0, 1], EASE.outExpo);
  const outO =
    fadeOut > 0
      ? lerp(frame, [durationInFrames - fadeOut, durationInFrames], [1, 0], EASE.inCubic)
      : 1;
  const opacity = Math.min(inO, outO);
  const y = lerp(frame, [0, fadeIn], [16, 0], EASE.outExpo);

  return (
    <AbsoluteFill
      style={{
        opacity,
        transform: `translateY(${y}px)`,
        padding: pad ? `${MARGIN.y}px ${MARGIN.x}px` : 0,
      }}
    >
      {children}
    </AbsoluteFill>
  );
};
