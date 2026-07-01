import { SceneWrap } from "../../components/SceneWrap";
import { Kicker } from "../../components/Text";
import { Pill, ShotZoom } from "../Shot";
import { line, TimelineScene } from "../timeline";

// shot-wiki-map.png is 6824×3928 (captured at 4× device scale so a zoom stays
// crisp). Focal point ≈ the central "Ruben" hub.
const SRC_W = 6824;
const SRC_H = 3928;
const DW = 928;

/** Real screenshot: the Wiki Memory Map — slow push into the actual graph. */
export const WikiMap: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const countAt = line(scene, "map_2").localStart;
  const graphAt = line(scene, "map_3").localStart;
  return (
    <SceneWrap padding={70}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
        <Kicker>My memory — the real thing</Kicker>
        <div style={{ position: "relative" }}>
          <ShotZoom
            src="shot-wiki-map.png"
            srcW={SRC_W}
            srcH={SRC_H}
            displayW={DW}
            zoomTo={2.5}
            focal={{ x: 0.513, y: 0.511 }}
            zoomWindow={[8, scene.dur]}
          />
          {/* stat — rendered big so it's always legible, backed by the graph */}
          <div style={{ position: "absolute", top: 18, left: 20 }}>
            <Pill delay={6} tone="gold" size={24}>
              59 pages · 184 wikilinks
            </Pill>
          </div>
          <div style={{ position: "absolute", top: 70, left: "50%", transform: "translateX(-50%)" }}>
            <Pill delay={countAt} size={26}>
              everything you&apos;ve told me
            </Pill>
          </div>
          <div style={{ position: "absolute", bottom: 30, left: "50%", transform: "translateX(-50%)" }}>
            <Pill delay={graphAt} size={26}>
              every link is a memory
            </Pill>
          </div>
        </div>
      </div>
    </SceneWrap>
  );
};
