import { WORLD, type Vec3 } from "./world";

/** Material-batched GLBs must not turn every road or cliff click into mast focus. */
export function outpostBuildingAt(point: Vec3): string | null {
  const candidates = WORLD.buildings.filter((building) => {
    if (building.district_id !== "communications-outpost") return false;
    const [x, y, z] = building.position, [width, height, depth] = building.size;
    return point[1] >= y - 0.05 && point[1] <= y + height + 0.5
      && Math.abs(point[0] - x) <= width / 2 + 0.3 && Math.abs(point[2] - z) <= depth / 2 + 0.3;
  });
  candidates.sort((a, b) => Math.hypot(point[0] - a.position[0], point[2] - a.position[2])
    - Math.hypot(point[0] - b.position[0], point[2] - b.position[2]));
  return candidates[0]?.id ?? null;
}
