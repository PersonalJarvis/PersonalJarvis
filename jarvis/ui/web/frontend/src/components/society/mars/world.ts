/** New Mars geometry and collision projection; no legacy island state is consumed. */
import { BufferGeometry, Float32BufferAttribute, Color } from "three";
import definition from "./worldDefinition.json";
import outpostContract from "../../../../../../../../art/studies/mars-outpost-reference/source/geometry-contract.json";

export type Vec3 = [number, number, number];
export interface Bounds { min: Vec3; max: Vec3 }
export interface Collider extends Bounds { id: string }
export const WORLD = definition;
export const WORLD_BOUNDS: Bounds = {
  min: [...WORLD.bounds.min] as Vec3,
  max: [...WORLD.bounds.max] as Vec3,
};
export const OUTPOST = WORLD.districts.find((d) => d.id === "communications-outpost")!;
const nodes = new Map(WORLD.navigation.nodes.map((node) => [node.id, node]));
export const ROADS = WORLD.navigation.edges.map((edge) => ({
  ...edge,
  start: [...nodes.get(edge.from)!.position] as Vec3,
  end: [...nodes.get(edge.to)!.position] as Vec3,
}));
export type Road = (typeof ROADS)[number];

const smooth = (value: number) => {
  const t = Math.max(0, Math.min(1, value));
  return t * t * (3 - 2 * t);
};

export function projectRoad(x: number, z: number, road: Road) {
  const dx = road.end[0] - road.start[0];
  const dz = road.end[2] - road.start[2];
  const along = ((x - road.start[0]) * dx + (z - road.start[2]) * dz) / (dx * dx + dz * dz);
  const t = Math.max(0, Math.min(1, along));
  return {
    t, along,
    distance: Math.hypot(x - road.start[0] - dx * t, z - road.start[2] - dz * t),
    height: road.start[1] + (road.end[1] - road.start[1]) * t,
  };
}

/** Authored plateaus over a continuous deterministic height field. */
function designHeight(x: number, z: number): number {
  let height = -3 + 3.5 * Math.sin(x * 0.016) * Math.cos(z * 0.012)
    + 1.2 * Math.sin(x * 0.051 + z * 0.029);
  for (const district of WORLD.districts) {
    // The finished reference owns its own cliff shell. A coarse terrain mound
    // here would bury the modeled rear/side faces inside the blockout.
    if (district.id === "communications-outpost") continue;
    const dx = Math.abs(x - district.center[0]);
    const dz = Math.abs(z - district.center[2]);
    // A level central pad with irregular, fully modeled surrounding escarpments.
    const outside = Math.max(dx - district.size[0] / 2, dz - district.size[1] / 2);
    const skirt = 30 + 6 * Math.sin(x * 0.04 + z * 0.03);
    const weight = 1 - smooth(outside / skirt);
    height = Math.max(height, -6 + (district.center[1] + 6) * weight);
  }
  let nearest = Infinity;
  let roadHeight = height;
  let weight = 0;
  for (const road of ROADS) {
    if (road.kind === "bridge" || road.kind === "interior"
      || road.from.startsWith("outpost-") || road.to.startsWith("outpost-")) continue;
    const projection = projectRoad(x, z, road);
    const distance = Math.max(0, projection.distance - road.width / 2);
    if (distance < nearest && distance < 9) {
      nearest = distance;
      roadHeight = projection.height;
      weight = 1 - smooth(distance / 9);
    }
  }
  height += (roadHeight - height) * weight;
  // A coarse triangle adjacent to a graded corridor must not poke through its
  // deck. Cut only excess terrain, including bridge abutments; never raise the
  // ravine under a bridge. The margin covers this field's 5 m cells.
  for (const road of ROADS) {
    if (road.kind === "interior") continue;
    const projection = projectRoad(x, z, road);
    if (projection.distance <= road.width / 2 + 7.5) height = Math.min(height, projection.height - 0.2);
  }
  return height;
}

// This grid is both the rendered terrain and the exact triangle collision field.
const CELLS_X = 174;
const CELLS_Z = 164;
const DX = (WORLD_BOUNDS.max[0] - WORLD_BOUNDS.min[0]) / CELLS_X;
const DZ = (WORLD_BOUNDS.max[2] - WORLD_BOUNDS.min[2]) / CELLS_Z;
const heights = new Float32Array((CELLS_X + 1) * (CELLS_Z + 1));
for (let iz = 0; iz <= CELLS_Z; iz++) {
  for (let ix = 0; ix <= CELLS_X; ix++) {
    heights[iz * (CELLS_X + 1) + ix] = designHeight(WORLD_BOUNDS.min[0] + ix * DX, WORLD_BOUNDS.min[2] + iz * DZ);
  }
}

export function terrainHeight(x: number, z: number): number {
  const gx = Math.max(0, Math.min(CELLS_X - 0.000001, (x - WORLD_BOUNDS.min[0]) / DX));
  const gz = Math.max(0, Math.min(CELLS_Z - 0.000001, (z - WORLD_BOUNDS.min[2]) / DZ));
  const ix = Math.floor(gx), iz = Math.floor(gz), u = gx - ix, v = gz - iz;
  const index = iz * (CELLS_X + 1) + ix;
  const a = heights[index], b = heights[index + 1];
  const c = heights[index + CELLS_X + 1], d = heights[index + CELLS_X + 2];
  return u + v <= 1 ? a + (b - a) * u + (c - a) * v : d + (c - d) * (1 - u) + (b - d) * (1 - v);
}

/** Roads and bridges retain stable collision independently of rendered detail. */
export function surfaceHeight(x: number, z: number): number {
  let height = terrainHeight(x, z), nearest = Infinity;
  const plateau = outpostContract.colliders.find((collider) => collider.shape === "plateau");
  const footprint = plateau?.footprint;
  if (footprint) {
    const localX = x - 320, localZ = z - 50;
    let inside = false;
    for (let i = 0, j = footprint.length - 1; i < footprint.length; j = i++) {
      const [xi, zi] = footprint[i], [xj, zj] = footprint[j];
      if ((zi > localZ) !== (zj > localZ)
        && localX < (xj - xi) * (localZ - zi) / (zj - zi) + xi) inside = !inside;
    }
    if (inside) height = 58 + (plateau.top_y ?? 0);
  }
  for (const road of ROADS) {
    const projection = projectRoad(x, z, road);
    // Collision uses the same rectangular deck as RoadMesh, not a rounded
    // endpoint capsule that creates a step before two sloped routes meet.
    if (projection.along >= -1e-7 && projection.along <= 1 + 1e-7
      && projection.distance <= road.width / 2 && projection.distance < nearest) {
      nearest = projection.distance;
      height = projection.height + 0.08;
    }
  }
  return height;
}

export function createTerrainGeometry(): BufferGeometry {
  const positions: number[] = [], colors: number[] = [], indices: number[] = [];
  const dark = new Color("#694336"), light = new Color("#ad7954");
  const color = new Color();
  for (let iz = 0; iz <= CELLS_Z; iz++) {
    for (let ix = 0; ix <= CELLS_X; ix++) {
      const x = WORLD_BOUNDS.min[0] + ix * DX, z = WORLD_BOUNDS.min[2] + iz * DZ;
      const height = heights[iz * (CELLS_X + 1) + ix];
      positions.push(x, height, z);
      color.copy(dark).lerp(light, Math.max(0, Math.min(1, 0.4 + height / 160 + 0.12 * Math.sin(x * 0.08 + z * 0.09))));
      colors.push(color.r, color.g, color.b);
      if (ix < CELLS_X && iz < CELLS_Z) {
        const a = iz * (CELLS_X + 1) + ix, b = a + 1, c = a + CELLS_X + 1, d = c + 1;
        indices.push(a, c, b, b, c, d);
      }
    }
  }
  // Close the four sides down to the base: rear and side inspection never reveals a sheet.
  const perimeter: number[] = [];
  for (let ix = 0; ix <= CELLS_X; ix++) perimeter.push(ix);
  for (let iz = 1; iz <= CELLS_Z; iz++) perimeter.push(iz * (CELLS_X + 1) + CELLS_X);
  for (let ix = CELLS_X - 1; ix >= 0; ix--) perimeter.push(CELLS_Z * (CELLS_X + 1) + ix);
  for (let iz = CELLS_Z - 1; iz > 0; iz--) perimeter.push(iz * (CELLS_X + 1));
  for (let i = 0; i < perimeter.length; i++) {
    const a = perimeter[i], b = perimeter[(i + 1) % perimeter.length], bottom = positions.length / 3;
    positions.push(positions[a * 3], -12, positions[a * 3 + 2], positions[b * 3], -12, positions[b * 3 + 2]);
    colors.push(dark.r, dark.g, dark.b, dark.r, dark.g, dark.b);
    indices.push(a, b, bottom, b, bottom + 1, bottom);
  }
  const geometry = new BufferGeometry();
  geometry.setAttribute("position", new Float32BufferAttribute(positions, 3));
  geometry.setAttribute("color", new Float32BufferAttribute(colors, 3));
  geometry.setIndex(indices);
  geometry.computeVertexNormals();
  geometry.computeBoundingSphere();
  return geometry;
}

export function box(id: string, x: number, y: number, z: number, width: number, height: number, depth: number): Collider {
  return { id, min: [x - width / 2, y, z - depth / 2], max: [x + width / 2, y + height, z + depth / 2] };
}

/** A real simple entrance is reserved in the operations blockout; final art is pending. */
export const BUILDING_COLLIDERS: Collider[] = WORLD.buildings.flatMap((building) => {
  if (building.district_id === "communications-outpost") return [];
  const [x, y, z] = building.position, [w, h, d] = building.size;
  if (building.id !== "operations") return [box(building.id, x, y, z, w, h, d)];
  const door = 3.6, wall = 0.45, side = (w - door) / 2;
  return [
    box("operations-west", x - w / 2, y, z, wall, h, d),
    box("operations-east", x + w / 2, y, z, wall, h, d),
    box("operations-north", x, y, z - d / 2, w, h, wall),
    box("operations-front-left", x - door / 2 - side / 2, y, z + d / 2, side, h, wall),
    box("operations-front-right", x + door / 2 + side / 2, y, z + d / 2, side, h, wall),
    box("operations-lintel", x, y + 3.4, z + d / 2, door, h - 3.4, wall),
    box("operations-roof", x, y + h, z, w, wall, d),
  ];
}).concat(outpostContract.colliders.flatMap((collider) => {
  if (collider.shape !== "box" || !collider.center || !collider.size) return [];
  const [x, y, z] = collider.center, [w, h, d] = collider.size;
  return [box("outpost:" + collider.id, x + 320, y + 58 - h / 2, z + 50, w, h, d)];
}));

export const PLAYER_SPAWN: Vec3 = [WORLD.spawn.position[0], surfaceHeight(WORLD.spawn.position[0], WORLD.spawn.position[2]), WORLD.spawn.position[2]];

export function outpostBounds(): Bounds {
  return {
    min: [OUTPOST.center[0] - OUTPOST.size[0] / 2 - 16, 32, OUTPOST.center[2] - OUTPOST.size[1] / 2 - 16],
    max: [OUTPOST.center[0] + OUTPOST.size[0] / 2 + 16, OUTPOST.center[1] + OUTPOST.landmark_height, OUTPOST.center[2] + OUTPOST.size[1] / 2 + 16],
  };
}
