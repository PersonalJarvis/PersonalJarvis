import { SceneWrap } from "../../components/SceneWrap";
import { Kicker } from "../../components/Text";
import { Ring, ShotFrame } from "../Shot";
import { line, TimelineScene } from "../timeline";

const SRC_W = 3412;
const DW = 944;
const SCALE = DW / SRC_W;

/** Real screenshot: a single Wiki page — facts, wikilinks, sources, backlinks. */
export const WikiPage: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const linksAt = line(scene, "page_2").localStart;
  const backAt = line(scene, "page_3").localStart;
  return (
    <SceneWrap padding={80}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
        <Kicker>One page, everything on it</Kicker>
        <ShotFrame src="shot-wiki-page.png" width={DW}>
          {/* relationships / wikilinks */}
          <Ring
            box={{ x: 1146, y: 1258, w: 540, h: 224 }}
            scale={SCALE}
            delay={linksAt}
            label="links to related notes"
            labelAt={{ x: 1700, y: 1330 }}
          />
          {/* backlinks panel */}
          <Ring
            box={{ x: 2642, y: 280, w: 762, h: 286 }}
            scale={SCALE}
            delay={backAt}
            label="backlinks — what points here"
            labelAt={{ x: 2300, y: 250 }}
          />
        </ShotFrame>
      </div>
    </SceneWrap>
  );
};
