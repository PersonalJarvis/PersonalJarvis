import { interpolate, useCurrentFrame } from "remotion";
import { SceneWrap } from "../../components/SceneWrap";
import { Kicker } from "../../components/Text";
import { Pill, ShotCrop } from "../Shot";
import { line, TimelineScene } from "../timeline";

// shot-wiki-page.png is 6824×3928. Two big crops: the content column (facts +
// wikilinks), slowly panned down, then the backlinks panel.
const SRC_W = 6824;

/** Real screenshot: one Wiki page — the readable content, shown large. */
export const WikiPage: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const backAt = line(scene, "page_3").localStart; // 200

  // crossfade content column → backlinks panel around page_3
  const contentO = interpolate(frame, [backAt - 12, backAt + 10], [1, 0], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });
  const backO = interpolate(frame, [backAt - 4, backAt + 16], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
  });

  return (
    <SceneWrap padding={70}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 12 }}>
        <Kicker>One page — every fact, big and legible</Kicker>
        <div style={{ position: "relative", width: 960, height: 560 }}>
          {/* content column: title → facts → relationships (wikilinks), panned down */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              justifyContent: "center",
              alignItems: "center",
              opacity: contentO,
            }}
          >
            {/* Pan title → summary → facts, and STOP before the Relationships
                section (its "Owned by" line would wrongly imply ownership of a
                third-party tool). The wikilink/backlink story is carried by the
                backlinks panel below. */}
            <ShotCrop
              src="shot-wiki-page.png"
              srcW={SRC_W}
              crop={{ x: 2240, y: 636, w: 2470, h: 1440 }}
              displayW={940}
              panBy={360}
              panWindow={[26, 185]}
            />
          </div>
          {/* backlinks panel */}
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              justifyContent: "center",
              alignItems: "center",
              gap: 14,
              opacity: backO,
            }}
          >
            <ShotCrop
              src="shot-wiki-page.png"
              srcW={SRC_W}
              crop={{ x: 5280, y: 520, w: 1544, h: 820 }}
              displayW={880}
              delay={backAt - 8}
            />
            <Pill delay={backAt + 6} tone="gold" size={24}>
              backlinks — every note that points here
            </Pill>
          </div>
        </div>
      </div>
    </SceneWrap>
  );
};
