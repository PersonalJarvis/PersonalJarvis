/**
 * The agents floor's window strip as data: olive trees in stone pots closing
 * every cross aisle at the glass, and beside each outer department a
 * cushioned oak window bench on one side of its window spot and a slatted oak
 * planter of grasses on the other (swapped between west and east, so the two
 * windows never mirror each other).
 *
 * Pure and shared: the layout adds every item to its obstacles, the renderer
 * builds each piece inside the same box. Everything stands in the 1.6 m
 * window strip between the departments and the railing, so no desk, room door
 * or checkpoint loses its way in.
 */
import type { Rect } from "./officeLayout";

export type AgentsAmbienceKind = "olive" | "bench" | "planter";

export interface AgentsAmbienceItem extends Rect {
  id: string;
  kind: AgentsAmbienceKind;
  /** The side the piece's front looks at: benches face into the office. */
  facing: "east" | "west";
  /** Height of the piece in metres (the olive's crown top). */
  h: number;
}

/** An olive pot's square footprint, and how far its centre stands out from the department column. */
export const OLIVE_BOX = 0.8;
const OLIVE_OUT = 0.8;
/** Benches and planters: depth, and how far their inner face stands out from the department column. */
export const BENCH_DEPTH = 0.4;
export const PLANTER_DEPTH = 0.34;
const RUN_OUT = 0.95;
/** Half the gap left round a department's window spot, and the margin at the department's ends. */
const WINDOW_GAP = 0.85;
const END_MARGIN = 0.4;
const MIN_RUN = 1.2;

export const OLIVE_H = 2.3;
export const BENCH_H = 0.46;
export const PLANTER_H = 0.52;

/** Where the aisles are: the department rows and the room strips north and south of them. */
export interface AgentsAmbienceFrame {
  departments: readonly Rect[];
  rooms: readonly Rect[];
}

/** Every window-strip piece of the agents floor, in a stable order. */
export function agentsAmbience({ departments, rooms }: AgentsAmbienceFrame): AgentsAmbienceItem[] {
  if (departments.length === 0) return [];
  const colMinX = Math.min(...departments.map((d) => d.minX));
  const colMaxX = Math.max(...departments.map((d) => d.maxX));
  const firstZ = Math.min(...departments.map((d) => d.minZ));
  const lastZ = Math.max(...departments.map((d) => d.maxZ));
  const north = rooms.filter((r) => r.maxZ <= firstZ + 1e-6).map((r) => r.maxZ);
  const south = rooms.filter((r) => r.minZ >= lastZ - 1e-6).map((r) => r.minZ);
  const items: AgentsAmbienceItem[] = [];

  // Cross aisles: in front of the north rooms, between department rows, in front of the south strip.
  const rowStarts = [...new Set(departments.map((d) => d.minZ))].sort((a, b) => a - b);
  const aisles: number[] = [];
  if (north.length > 0) aisles.push((Math.max(...north) + firstZ) / 2);
  for (let i = 1; i < rowStarts.length; i += 1) {
    const above = Math.max(...departments.filter((d) => d.minZ === rowStarts[i - 1]).map((d) => d.maxZ));
    aisles.push((above + rowStarts[i]) / 2);
  }
  if (south.length > 0) aisles.push((lastZ + Math.min(...south)) / 2);
  aisles.forEach((z, i) => {
    for (const [side, x] of [["west", colMinX - OLIVE_OUT], ["east", colMaxX + OLIVE_OUT]] as const) {
      items.push({
        id: `olive-${side}-${i}`, kind: "olive", facing: side === "west" ? "east" : "west", h: OLIVE_H,
        minX: x - OLIVE_BOX / 2, maxX: x + OLIVE_BOX / 2, minZ: z - OLIVE_BOX / 2, maxZ: z + OLIVE_BOX / 2,
      });
    }
  });

  // Beside each outer department: two runs, one either side of its window spot.
  departments.forEach((dept, index) => {
    const zc = (dept.minZ + dept.maxZ) / 2;
    const runs = [[dept.minZ + END_MARGIN, zc - WINDOW_GAP], [zc + WINDOW_GAP, dept.maxZ - END_MARGIN]] as const;
    runs.forEach(([z0, z1], run) => {
      if (z1 - z0 < MIN_RUN) return;
      for (const side of ["west", "east"] as const) {
        if (side === "west" ? dept.minX > colMinX + 1e-6 : dept.maxX < colMaxX - 1e-6) continue;
        // West: bench north of the spot, planter south of it; the east window the other way round.
        const kind: AgentsAmbienceKind = (run === 0) === (side === "west") ? "bench" : "planter";
        const depth = kind === "bench" ? BENCH_DEPTH : PLANTER_DEPTH;
        const [minX, maxX] = side === "west"
          ? [colMinX - RUN_OUT - depth, colMinX - RUN_OUT]
          : [colMaxX + RUN_OUT, colMaxX + RUN_OUT + depth];
        items.push({
          id: `${kind}-${side}-${index}-${run}`, kind, facing: side === "west" ? "east" : "west",
          h: kind === "bench" ? BENCH_H : PLANTER_H, minX, maxX, minZ: z0, maxZ: z1,
        });
      }
    });
  });
  return items;
}
