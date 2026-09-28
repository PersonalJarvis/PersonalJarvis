/**
 * The office's own colours ("cosy toy office", docs/agent-society/office-map.md §2).
 * Like the earlier worlds, nothing inside the canvas reads a theme token: the
 * scene is a lit diorama that looks the same in light and dark mode.
 */
export const OFFICE = {
  space: "#0b1024",
  slab: "#3a3f4b",
  slabEdge: "#2b2f38",
  wood: "#b98a5e",
  woodDark: "#8a6240",
  walkway: "#c9a27a",
  deskTop: "#e8d3b0",
  deskBody: "#f2f0ec",
  deskLeg: "#d9d6cf",
  chair: "#2c2f36",
  chairSeat: "#3b3f48",
  monitor: "#23262d",
  keyboard: "#3a3d44",
  railing: "#b7bcc4",
  glass: "#bfe3ff",
  wallWhite: "#f1efe9",
  signBoard: "#3d2c22",
  signText: "#fff7ec",
  couch: "#2f3542",
  couchCushion: "#3d4556",
  plantPot: "#f4f1ea",
  leaf: "#5fa35a",
  leafDark: "#3f7f45",
  trunk: "#6b4a2e",
  book: ["#c8553d", "#f2a541", "#4f7cac", "#5e9c76", "#8d6cab"],
  rug: "#d8c8ae",
  ringWorking: "#4ade80",
  ringIdle: "#e8ecf2",
  ringWaiting: "#fbbf24",
  ringPaused: "#94a3b8",
} as const;

/** Carpet tints per department — soft, desaturated, readable next to wood. */
export const DEPARTMENT_TINTS = ["#9aa6b8", "#a9b59a", "#b8a39a", "#a39ab8", "#9ab5b1", "#b8b19a"] as const;
