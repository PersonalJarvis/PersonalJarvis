import { useCameraStore } from "./cameraStore";
import { BUILDING_ASSETS, buildingHeight } from "./worldManifest";
/**
 * In-world signs over the places, drawn as DOM so they stay legible through
 * the pixel pass. Text comes from the `society` locale chunk; the look from
 * `world.css` (the world's own type, MASTERPLAN §4.3).
 */
import { Html } from "@react-three/drei";

import { useT } from "@/i18n";
import { buildIsland, groundY, tileToWorld, type PlaceId } from "./islandLayout";

/** Label anchor height above the ground per place (roughly the roofline). */
const LABEL_Y: Record<PlaceId, number> = {
  market: 12.5,
  hub: 26,
  workshop: 9,
  archive: 19,
  harbor: 8,
  lighthouse: 20,
  gardens: 6,
  solar: 5,
  mine: 10,
  plugins: 13,
  foundry: 18,
  skills: 9,
  mcp: 12,
  cli: 7,
  comms: 14,
  desktop: 13,
  web: 16,
  models: 17,
  civic: 18,
  gallery: 11,
};

export function PlaceLabels() {
  const t = useT();
  const zoom = useCameraStore(s => s.zoom);
  const { map, content } = buildIsland();
  return (
    <group>
      {(Object.keys(content.places) as PlaceId[]).filter(id => zoom < 3 || ["market", "foundry", "archive", "harbor", "lighthouse", "mine"].includes(id)).map((id) => {
        const pose = id in content.kitPoses ? content.kitPoses[id as keyof typeof content.kitPoses] : null;
        const [tx, tz] = content.places[id].tile;
        const [x, z] = pose ? [pose.x, pose.z] : tileToWorld(tx, tz);
        const y = groundY(map, x, z) + (id in BUILDING_ASSETS ? buildingHeight(id as keyof typeof BUILDING_ASSETS) + 1 : LABEL_Y[id]);
        return (
          <Html key={id} position={[x, y, z]} center zIndexRange={[20, 5]} style={{ pointerEvents: "none" }}>
            <div className="sw-placelabel">{t(`society.world.place_${id}`)}</div>
          </Html>
        );
      })}
    </group>
  );
}
