import { AbsoluteFill, Sequence } from "remotion";
import { Background } from "./components/Background";
import { SceneWrap } from "./components/SceneWrap";
import { S1Intro } from "./scenes/S1Intro";
import { S2Idea } from "./scenes/S2Idea";
import { S3Architecture } from "./scenes/S3Architecture";
import { S4Page } from "./scenes/S4Page";
import { S5ReadBack } from "./scenes/S5ReadBack";
import { S6Outro } from "./scenes/S6Outro";
import { SCENES, TOTAL_FRAMES } from "./theme";

// Crossfade overlap: each scene (except the last) extends this many frames into
// the next, and SceneWrap fades out over that window while the next fades in.
const OVERLAP = 20;

const order = [
  { key: "intro", comp: <S1Intro />, pad: false },
  { key: "idea", comp: <S2Idea /> },
  { key: "arch", comp: <S3Architecture /> },
  { key: "page", comp: <S4Page /> },
  { key: "read", comp: <S5ReadBack /> },
  { key: "outro", comp: <S6Outro />, pad: false },
] as const;

export const WikiVideo: React.FC = () => {
  return (
    <AbsoluteFill>
      <Background />
      {order.map((s, i) => {
        const slot = SCENES[s.key];
        const isLast = i === order.length - 1;
        const dur = slot.dur + (isLast ? 0 : OVERLAP);
        return (
          <Sequence key={s.key} from={slot.from} durationInFrames={dur}>
            <SceneWrap durationInFrames={dur} pad={s.pad ?? true}>
              {s.comp}
            </SceneWrap>
          </Sequence>
        );
      })}
    </AbsoluteFill>
  );
};

export const WIKI_TOTAL_FRAMES = TOTAL_FRAMES;
