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
  // fadeIn/fadeOut may be explicitly 0 to disable that edge fade.
  const frame = useCurrentFrame();
  // fadeIn<=0 → no wrapper fade-in (the scene owns its entrance, so frame 0 is
  // never an empty fade-from-nothing). fadeOut<=0 → holds to the end.
  const inO = fadeIn > 0 ? lerp(frame, [0, fadeIn], [0, 1], EASE.outExpo) : 1;
  const outO =
    fadeOut > 0
      ? lerp(frame, [durationInFrames - fadeOut, durationInFrames], [1, 0], EASE.inCubic)
      : 1;
  const opacity = Math.min(inO, outO);
  const y = fadeIn > 0 ? lerp(frame, [0, fadeIn], [16, 0], EASE.outExpo) : 0;

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
