import { SceneWrap } from "../../components/SceneWrap";
import { Kicker } from "../../components/Text";
import { Ring, ShotFrame } from "../Shot";
import { line, TimelineScene } from "../timeline";

const SRC_W = 3412;
const DW = 944;
const SCALE = DW / SRC_W;

/** Real screenshot: the Wiki Memory Map — the actual knowledge graph. */
export const WikiMap: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const countAt = line(scene, "map_2").localStart;
  const graphAt = line(scene, "map_3").localStart;
  return (
    <SceneWrap padding={80}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
        <Kicker>My memory — the real thing</Kicker>
        <ShotFrame src="shot-wiki-map.png" width={DW}>
          {/* page + link count */}
          <Ring
            box={{ x: 744, y: 162, w: 232, h: 38 }}
            scale={SCALE}
            delay={countAt}
            label="everything you've told me"
            labelAt={{ x: 992, y: 165 }}
          />
          {/* the central hub node */}
          <Ring
            box={{ x: 1636, y: 900, w: 240, h: 240 }}
            scale={SCALE}
            delay={graphAt}
            label="every link is a memory"
            labelAt={{ x: 1636, y: 1160 }}
          />
        </ShotFrame>
      </div>
    </SceneWrap>
  );
};
