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

/**
 * The coding floor's own look, so the two floors never read as copies: a
 * violet night instead of navy, cool terrazzo instead of warm planks, a glowing
 * rim around the slab, and a studio style per department.
 */
export const CODING_SCENE = {
  space: "#140e2e",
  slabEdge: "#2a2542",
  rim: "#5eead4",
  sky: "#d9e2ff",
  ground: "#4d4868",
  terrazzo: { base: "#dde1e8", seam: "rgba(120,128,145,0.3)", chips: ["#c3c9d4", "#efcdbf", "#c2dcd5", "#d2cae6", "#a6adbb", "#f6f7f9"] },
} as const;

export type CarpetPattern = "grid" | "stripes" | "checker" | "dots" | "diagonal" | "zigzag";

export interface StudioStyle {
  pattern: CarpetPattern;
  /** Carpet base, its pattern tone, and the rug border. */
  carpet: string;
  weave: string;
  border: string;
  /** The department's back wall and its slats. */
  wall: string;
  slat: string;
  /** Desk top, cabinet, leg, chair base and seat. */
  deskTop: string;
  deskBody: string;
  deskLeg: string;
  chair: string;
  seat: string;
}

/** One studio per workspace department, cycled; every one differs in pattern, colours and furniture. */
export const CODING_STUDIOS: readonly StudioStyle[] = [
  { pattern: "grid", carpet: "#3b4a6b", weave: "#4d5e82", border: "#5eead4", wall: "#2f3b57", slat: "#5eead4",
    deskTop: "#6b4a33", deskBody: "#23262d", deskLeg: "#3a3d44", chair: "#23262d", seat: "#3aa99a" },
  { pattern: "stripes", carpet: "#a7b98f", weave: "#95a87e", border: "#e8d9a8", wall: "#e9efe0", slat: "#7c9a5e",
    deskTop: "#e6d2ad", deskBody: "#fbfaf6", deskLeg: "#c9c4b8", chair: "#4b5a3a", seat: "#7c9a5e" },
  { pattern: "checker", carpet: "#d99a7b", weave: "#c9876a", border: "#f3e3cf", wall: "#f5e6d8", slat: "#b5654a",
    deskTop: "#f0e2cb", deskBody: "#fdf8f0", deskLeg: "#d8cbb8", chair: "#6b3a2a", seat: "#c46a4c" },
  { pattern: "dots", carpet: "#b9aed6", weave: "#d6cdee", border: "#7d6bb0", wall: "#ece6f7", slat: "#8b79c0",
    deskTop: "#fbfbfb", deskBody: "#d9d7e2", deskLeg: "#a9a6b8", chair: "#3a3350", seat: "#7a4f8f" },
  { pattern: "diagonal", carpet: "#7fb9b8", weave: "#6aa6a5", border: "#1f5f6b", wall: "#dcefee", slat: "#2e7c85",
    deskTop: "#d9b98f", deskBody: "#e7eeee", deskLeg: "#9fb3b5", chair: "#1f3b4d", seat: "#2d5f86" },
  { pattern: "zigzag", carpet: "#e2c070", weave: "#d0aa55", border: "#3a3024", wall: "#fbf1d6", slat: "#b8862e",
    deskTop: "#5b3a25", deskBody: "#2c2a28", deskLeg: "#46423d", chair: "#2c2a28", seat: "#e0a93a" },
];

/** Colours of the room props (lobby, team room, wardrobe, break room). */
export const PROP_COLOURS = {
  steel: "#aab2bc",
  steelDark: "#6f7782",
  elevatorShaft: "#e7e4de",
  indicator: "#ffb347",
  lockers: ["#5fa8a0", "#e2856e", "#f2c14e", "#9b8ac4"],
  lockerVent: "#2f3440",
  mirror: "#d6ecf8",
  waterBottle: "#6fb7ea",
  waterTap: ["#e05a4f", "#4f8fe0"],
  mug: "#fdfbf7",
  coffee: "#5a3a24",
  espresso: "#c9ced4",
  chalkboard: "#2c3a33",
  arcadeBody: "#5b4a9e",
  arcadeTrim: "#2a2140",
  arcadeMarquee: "#ff7ab8",
  joystick: "#e0463c",
  arcadeButtons: ["#f2c14e", "#4fb3e0", "#6fd07a"],
  beanbag: ["#e27d60", "#85cdca", "#e8a87c", "#c38d9e", "#7d9bd6"],
  bell: "#e0b64a",
  boardFrame: "#d7d9dd",
  boardWhite: "#fbfbf8",
  kioskBody: "#f4f2ee",
  kioskHead: "#2a2e36",
  paper: "#fdfcf9",
  rugInner: "#c9b28f",
} as const;

/** Floor overlays per room kind: base colour plus the pattern's accent tones. */
export const ROOM_FLOOR_COLOURS = {
  lead: { base: "#6a432a", accents: ["#74492d", "#5e3a24", "#6c4429", "#7d5133", "#553420"] },
  team: { base: "#7fb3ad", accents: ["#8bbdb7", "#74a7a1", "#86b8b2"] },
  wardrobe: { base: "#ddd3ea", accents: ["#cfc3e0", "#e6ddf1"] },
  reception: { base: "#e7e2d9", accents: ["#ece8e0", "#e0dacf", "#e9e4dc"] },
  break: { base: "#d98b6e", accents: ["#e0967a", "#d08065", "#dc9074"] },
  // Coding floor: a calm sage carpet in the focus zone, cool raised-floor tiles in the server room.
  focus: { base: "#9fb08c", accents: ["#a9ba96", "#94a582", "#a3b491"] },
  server: { base: "#b9c0cb", accents: ["#c3cad4", "#aeb5c0"] },
} as const;

/** Checkpoint gold: floor ring, hexagon token and the label badge. */
export const CHECKPOINT_GOLD = { ring: "#f5b83d", rim: "#e0a02a", face: "#f7c65a", faceDeep: "#e39b1f", icon: "#ffffff" } as const;

/**
 * The lead office's executive suite: dark walnut, brass, oxblood leather and a
 * navy rug. Brass is the one deliberately metallic material outside the
 * railings, so the boss's room reads as the most precious place on the floor.
 */
export const LEAD_SUITE = {
  walnut: "#5b3a25",
  walnutDark: "#3a2416",
  walnutLight: "#80583a",
  brass: "#d8ae52",
  leather: "#7a2c22",
  leatherDark: "#4e1a14",
  leatherTan: "#a9683c",
  velvetGold: "#d9a441",
  chrome: "#c9ced6",
  blackGlass: "#12141a",
  marble: "#eeeae3",
  led: "#ffd08a",
  lampGlass: "#2f7a4f",
  shade: "#fff0d2",
  backlight: "#8a5a36",
  glass: "#e8d3a8",
  rug: { field: "#1d2744", inner: "#26335a", border: "#c9a24a", accent: "#8f2f3a" },
  globe: { ocean: "#5f8f96", land: "#e2cd98", line: "#3d5c61" },
  bottles: ["#b8661e", "#4f7a3a", "#d9e6ea", "#8c2a2a", "#c79a3a"],
  trophy: "#e8b949",
  dog: { basket: "#6d4a33", cushion: "#8f2f3a" },
} as const;
