/**
 * The office's own colours: a contemporary workplace (docs/agent-society/office-map.md §2)
 * — light microcement floor, light-oak tops on black steel, graphite screens,
 * felt zone rugs, charcoal wayfinding with white type and lots of green.
 * Like the earlier worlds, nothing inside the canvas reads a theme token: the
 * scene is a lit diorama that looks the same in light and dark mode.
 */
export const OFFICE = {
  space: "#0d1320",
  slab: "#2a2e35",
  slabEdge: "#1c2027",
  /** Microcement floor: base plus the soft cloud tones and panel seams drawn over it. */
  floor: "#dcd8d1",
  floorCloud: ["#d4d0c8", "#e2ded8", "#d8d4cd", "#e6e3dd"],
  floorSeam: "rgba(120,114,104,0.22)",
  wood: "#d8bc94",
  woodDark: "#a88460",
  walkway: "#dcd8d1",
  deskTop: "#dcc39d",
  deskEdge: "#c9ab81",
  deskBody: "#f3f2ef",
  deskLeg: "#1f2226",
  steel: "#1f2226",
  chair: "#1d2025",
  chairSeat: "#3a3f47",
  chairMesh: "#40454e",
  monitor: "#16181c",
  monitorArm: "#b8bdc5",
  keyboard: "#e4e5e8",
  mug: "#f7f5f1",
  railing: "#23262b",
  glass: "#d6ecff",
  wallWhite: "#f6f5f2",
  signBoard: "#23262c",
  signText: "#ffffff",
  couch: "#bdb6aa",
  couchCushion: "#d8d1c4",
  couchAccent: ["#7f9a86", "#c98f6f", "#7c8fa8"],
  plantPot: "#34373d",
  planterLight: "#ebe6de",
  leaf: "#4f8f52",
  leafDark: "#2f6b3f",
  leafLight: "#77ad64",
  trunk: "#6b5440",
  book: ["#c8553d", "#e9b44c", "#4f7cac", "#5e9c76", "#8d6cab", "#e7e2d8"],
  rug: "#cfc7ba",
  ringWorking: "#4ade80",
  ringIdle: "#e8ecf2",
  ringWaiting: "#fbbf24",
  ringPaused: "#94a3b8",
} as const;

/**
 * Department zones: a felt rug, the matching acoustic wall panel and the seat
 * fabric of every chair in it — muted, contemporary tones that read on the
 * light floor without shouting.
 */
export const DEPARTMENT_ZONES = [
  { rug: "#bccab6", panel: "#8aa391", seat: "#6f8b77" },
  { rug: "#b9c6d4", panel: "#7b91ab", seat: "#5f7894" },
  { rug: "#ddd1bb", panel: "#bfa07a", seat: "#a8845a" },
  { rug: "#e2cbc0", panel: "#c48e7a", seat: "#b0705b" },
  { rug: "#c6c1d5", panel: "#918aad", seat: "#766e96" },
  { rug: "#b8d0c9", panel: "#6f9f94", seat: "#528579" },
] as const;

/** Rug tints per department (kept for callers that only need the floor colour). */
export const DEPARTMENT_TINTS = DEPARTMENT_ZONES.map((z) => z.rug);

/**
 * The coding floor's own look, so the two floors never read as copies: a
 * plum night instead of navy, honey-oak herringbone instead of microcement,
 * a warm amber rim round the slab, and a studio style per department.
 */
export const CODING_SCENE = {
  space: "#170f2a",
  slabEdge: "#2b2233",
  rim: "#f3b16b",
  sky: "#fff1de",
  ground: "#5e4c44",
  /** Herringbone oak: the seam between blocks and the block tones. */
  oak: { seam: "#8a6848", planks: ["#cfab80", "#c7a176", "#d6b58b", "#c09a6f", "#dcbb92", "#caa47a"] },
  /** The polished concrete band along the windows, and its brass inlay. */
  concrete: { base: "#cfc6ba", cloud: ["#c6bcaf", "#d8d0c5", "#c9c0b4"], brass: "#c9a24a" },
  /** Walnut slat walls behind each studio, with brass lettering. */
  walnut: { slat: "#5d3d28", gap: "#2e1d13", cap: "#1f1a17" },
  brass: "#d8ae52",
  /** Linear pendants over the desks: body, lit underside and the pool of light below. */
  pendant: { body: "#1d1b1a", glow: "#ffd9a0", pool: "#ffcf8f" },
} as const;

export type CarpetPattern = "grid" | "stripes" | "checker" | "dots" | "diagonal" | "zigzag";

export interface StudioStyle {
  pattern: CarpetPattern;
  /** Carpet base, its pattern tone, and the rug border. */
  carpet: string;
  weave: string;
  border: string;
  /** The studio's accent: the kick band along its walnut wall. */
  slat: string;
  /** Desk top, cabinet, leg, chair base and seat. */
  deskTop: string;
  deskBody: string;
  deskLeg: string;
  chair: string;
  seat: string;
}

/**
 * One studio per workspace department, cycled; every one differs in pattern
 * and colours. The patterns are woven tone-on-tone and the furniture stays in
 * the office's contemporary kit (oak or walnut tops, black or white steel,
 * graphite chairs), so a studio reads as a team's corner, not a theme park.
 */
export const CODING_STUDIOS: readonly StudioStyle[] = [
  { pattern: "grid", carpet: "#4a403a", weave: "#544841", border: "#c77b58", slat: "#c77b58",
    deskTop: "#c9ab81", deskBody: "#2b2f36", deskLeg: "#1f2226", chair: "#1d2025", seat: "#3aa99a" },
  { pattern: "stripes", carpet: "#b3bda2", weave: "#a9b398", border: "#7f9471", slat: "#7f9471",
    deskTop: "#dcc39d", deskBody: "#f3f2ef", deskLeg: "#1f2226", chair: "#1d2025", seat: "#6f8b77" },
  { pattern: "checker", carpet: "#dcbfae", weave: "#d4b5a3", border: "#bf7a5e", slat: "#bf7a5e",
    deskTop: "#f2f0ec", deskBody: "#f3f2ef", deskLeg: "#1f2226", chair: "#1d2025", seat: "#b0705b" },
  { pattern: "dots", carpet: "#cbbfc6", weave: "#c0b3bb", border: "#8f7a8a", slat: "#8f7a8a",
    deskTop: "#dcc39d", deskBody: "#e9e7ef", deskLeg: "#e9e7ef", chair: "#2b2f36", seat: "#766e96" },
  { pattern: "diagonal", carpet: "#b9c3c9", weave: "#afb9c0", border: "#6f8694", slat: "#6f8694",
    deskTop: "#c9ab81", deskBody: "#f3f2ef", deskLeg: "#1f2226", chair: "#1d2025", seat: "#5f7894" },
  { pattern: "zigzag", carpet: "#dccaa6", weave: "#d3c09a", border: "#b8955e", slat: "#b8955e",
    deskTop: "#8a6446", deskBody: "#2b2f36", deskLeg: "#1f2226", chair: "#1d2025", seat: "#c79a3a" },
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
  /** The dark frame of the reception's help display (and the counter's brochure tray). */
  displayFrame: "#2a2e36",
  paper: "#fdfcf9",
  rugInner: "#c9b28f",
} as const;

/** Floor overlays per room kind: base colour plus the pattern's accent tones. */
export const ROOM_FLOOR_COLOURS = {
  lead: { base: "#6a432a", accents: ["#74492d", "#5e3a24", "#6c4429", "#7d5133", "#553420"] },
  // Contemporary rooms: slate felt (team), light terrazzo (wardrobe), large pale stone (reception),
  // light-oak planks (break room); on the coding floor a soft sage felt and a raised-floor grid.
  team: { base: "#b4bec6", accents: ["#aab4bd", "#bcc6cd", "#b0bac2"] },
  wardrobe: { base: "#ece8e2", accents: ["#c9c2b8", "#9aa4ad", "#d9b8aa", "#aebcaa"] },
  reception: { base: "#e9e6e1", accents: ["#ece9e4", "#e5e2dc", "#efece8"] },
  break: { base: "#d9c09b", accents: ["#dcc39f", "#d2b791", "#e0c8a6", "#cfb48c"] },
  // Mission Control: dark graphite microcement.
  command: { base: "#454b54", accents: ["#3e444c", "#4d535c", "#41474f", "#51575f"] },
  server: { base: "#c3c8cf", accents: ["#c9ced4", "#bcc2c9"] },
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
