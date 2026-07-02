import "./index.css";
import { AbsoluteFill, Composition } from "remotion";
import { IntroVideo } from "./intro/IntroVideo";
import { OnboardingVideo } from "./intro/OnboardingVideo";
import { WikiTutorialVideo } from "./intro/WikiTutorialVideo";
import { Background } from "./intro/components/Background";
import { MorningOverview } from "./intro/scenes/MorningOverview";
import { COLORS, TOTAL_FRAMES, VIDEO } from "./intro/theme";
import { TL } from "./intro/onboarding/timeline";
import { TL_WIKI } from "./intro/wiki/timeline";
import { TutorialVideo } from "./tutorial/TutorialVideo";
import { TL_TUT } from "./tutorial/timeline";
import { PromoVideo } from "./intro/PromoVideo";
import { TL_PROMO } from "./intro/promo/timeline";

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
        id="JarvisTutorial"
        component={TutorialVideo}
        durationInFrames={TL_TUT.totalFrames}
        fps={VIDEO.fps}
        width={VIDEO.width}
        height={VIDEO.height}
      />
      <Composition
        id="ReadmePromo"
        component={PromoVideo}
        durationInFrames={TL_PROMO.totalFrames}
        fps={VIDEO.fps}
        width={VIDEO.width}
        height={VIDEO.height}
      />
      <Composition
        id="OnboardingYT"
        component={OnboardingVideo}
        durationInFrames={TL.totalFrames}
        fps={VIDEO.fps}
        width={VIDEO.width}
        height={VIDEO.height}
      />
      <Composition
        id="WikiTutorial"
        component={WikiTutorialVideo}
        durationInFrames={TL_WIKI.totalFrames}
        fps={VIDEO.fps}
        width={VIDEO.width}
        height={VIDEO.height}
      />
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
