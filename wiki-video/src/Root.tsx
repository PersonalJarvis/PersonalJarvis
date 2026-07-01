import "./index.css";
import { Composition } from "remotion";
import { WikiVideo } from "./WikiVideo";
import { TOTAL_FRAMES, VIDEO } from "./theme";

export const RemotionRoot: React.FC = () => {
  return (
    <Composition
      id="WikiVideo"
      component={WikiVideo}
      durationInFrames={TOTAL_FRAMES}
      fps={VIDEO.fps}
      width={VIDEO.width}
      height={VIDEO.height}
    />
  );
};
