/**
 * The coding floor's open-plan dressing as data: tree planters closing every
 * cross aisle at the windows, a low library of bookcases along the west glass
 * and planter troughs along the east glass, each beside a department and
 * split round its window spot.
 *
 * Pure and shared: the layout adds every item to its obstacles, the renderer
 * builds each piece inside the same box. Everything stands in the 1.6 m window
 * strip between the departments and the railing, so no desk, room door or
 * checkpoint loses its way in.
 */
import type { Rect } from "./officeLayout";

export type AmbienceKind = "tree" | "library" | "trough";

export interface AmbienceItem extends Rect {
  id: string;
  kind: AmbienceKind;
  /** The side the piece's front looks at: bookcases face into the office. */
  facing: "east" | "west";
  /** Height of the piece in metres (the tree's crown top). */
  h: number;
}

/** A tree planter's square footprint, and how far its centre stands out from the department column. */
export const TREE_BOX = 0.84;
const TREE_OUT = 0.8;
/** Bookcases and troughs: depth, and the band they stand in, measured out from the department column. */
const SHELF_DEPTH = 0.32;
const SHELF_OUT = 1.04;
/** Half the gap left round a department's window spot, and the margin at the department's ends. */
const WINDOW_GAP = 0.85;
const END_MARGIN = 0.35;
const MIN_RUN = 1.2;

export const LIBRARY_H = 1.02;
export const TROUGH_H = 0.46;
export const TREE_H = 2.35;

/** Where the aisles are: the department rows and the room strips north and south of them. */
export interface AmbienceFrame {
  departments: readonly Rect[];
  rooms: readonly Rect[];
}

/** Every ambience piece of a coding floor, in a stable order. */
export function codingAmbience({ departments, rooms }: AmbienceFrame): AmbienceItem[] {
  if (departments.length === 0) return [];
  const colMinX = Math.min(...departments.map((d) => d.minX));
  const colMaxX = Math.max(...departments.map((d) => d.maxX));
  const firstZ = Math.min(...departments.map((d) => d.minZ));
  const lastZ = Math.max(...departments.map((d) => d.maxZ));
  const north = rooms.filter((r) => r.maxZ <= firstZ + 1e-6).map((r) => r.maxZ);
  const south = rooms.filter((r) => r.minZ >= lastZ - 1e-6).map((r) => r.minZ);
  const items: AmbienceItem[] = [];

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
    for (const [side, x] of [["west", colMinX - TREE_OUT], ["east", colMaxX + TREE_OUT]] as const) {
      items.push({
        id: `tree-${side}-${i}`, kind: "tree", facing: side === "west" ? "east" : "west", h: TREE_H,
        minX: x - TREE_BOX / 2, maxX: x + TREE_BOX / 2, minZ: z - TREE_BOX / 2, maxZ: z + TREE_BOX / 2,
      });
    }
  });

  // Beside each outer department: two runs, one either side of its window spot.
  departments.forEach((dept, index) => {
    const west = dept.minX <= colMinX + 1e-6;
    const east = dept.maxX >= colMaxX - 1e-6;
    const zc = (dept.minZ + dept.maxZ) / 2;
    const runs = [[dept.minZ + END_MARGIN, zc - WINDOW_GAP], [zc + WINDOW_GAP, dept.maxZ - END_MARGIN]] as const;
    runs.forEach(([z0, z1], run) => {
      if (z1 - z0 < MIN_RUN) return;
      if (west) {
        items.push({ id: `library-${index}-${run}`, kind: "library", facing: "east", h: LIBRARY_H,
          minX: colMinX - SHELF_OUT - SHELF_DEPTH, maxX: colMinX - SHELF_OUT, minZ: z0, maxZ: z1 });
      }
      if (east) {
        items.push({ id: `trough-${index}-${run}`, kind: "trough", facing: "west", h: TROUGH_H,
          minX: colMaxX + SHELF_OUT, maxX: colMaxX + SHELF_OUT + SHELF_DEPTH, minZ: z0, maxZ: z1 });
      }
    });
  });
  return items;
}
