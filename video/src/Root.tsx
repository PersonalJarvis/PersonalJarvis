import "./index.css";
import { AbsoluteFill, Composition } from "remotion";
import { IntroVideo } from "./intro/IntroVideo";
import { Background } from "./intro/components/Background";
import { MorningOverview } from "./intro/scenes/MorningOverview";
import { COLORS, TOTAL_FRAMES, VIDEO } from "./intro/theme";

/**
 * Standalone preview of a single tutorial scene, wrapped with the same backdrop
 * the full video gives every scene — so a still/MP4 render of the scene alone
 * looks exactly as it will in context.
 */
const MorningOverviewPreview: React.FC = () => (
  <AbsoluteFill style={{ backgroundColor: COLORS.bg }}>
    <Background />
    <MorningOverview />
  </AbsoluteFill>
);

export const RemotionRoot: React.FC = () => {
  return (
    <>
      <Composition
        id="IntroVideo"
        component={IntroVideo}
        durationInFrames={TOTAL_FRAMES}
        fps={VIDEO.fps}
        width={VIDEO.width}
        height={VIDEO.height}
      />
      <Composition
        id="MorningOverview"
        component={MorningOverviewPreview}
        durationInFrames={330}
        fps={VIDEO.fps}
        width={VIDEO.width}
        height={VIDEO.height}
      />
    </>
  );
};
