/**
 * Every cabinet on the arcade floor, in the order they stand in the hall.
 *
 * The metadata (title, cabinet colours, which strings describe it) is cheap
 * and loaded with the floor; a game's code loads only when someone plays it
 * or walks up to its screen (`loadRetroGame`). Titles are the games' own
 * names and stay untranslated; the tagline and the controls line are i18n
 * keys under `society.arcade.games.<key>`.
 *
 * Asteroid Run is the 3D game from the break room; its cabinet opens the
 * existing ArcadeCabinet overlay instead of the retro overlay.
 */
import type { RetroGame } from "./retroGame";

export type RetroGameId =
  | "neon-snake" | "brick-breaker" | "paddle-duel"
  | "block-drop" | "maze-muncher" | "desert-dash"
  | "pixel-raiders" | "road-hopper" | "city-defense";

export type ArcadeGameId = RetroGameId | "asteroid-run";

export interface CabinetLook {
  /** Cabinet side panels. */
  body: string;
  /** Trim, bezel and control panel. */
  trim: string;
  /** The lit marquee's background and its title colour. */
  marquee: string;
  marqueeText: string;
  /** The neon accent: side stripes, the glow on the floor, the joystick ball. */
  accent: string;
}

export interface ArcadeGameInfo {
  id: ArcadeGameId;
  /** The game's own name, shown on the marquee and the overlay. Never translated. */
  title: string;
  /** i18n key segment: `society.arcade.games.<key>.tagline` and `.how`. */
  key: string;
  /** "retro" opens RetroArcadeOverlay; "asteroid3d" the break room's 3D ArcadeCabinet. */
  kind: "retro" | "asteroid3d";
  look: CabinetLook;
}

export const ARCADE_GAMES: readonly ArcadeGameInfo[] = [
  { id: "asteroid-run", title: "Asteroid Run", key: "asteroid_run", kind: "asteroid3d",
    look: { body: "#1d1a3a", trim: "#0e0c1f", marquee: "#120f2a", marqueeText: "#facc15", accent: "#2dff5a" } },
  { id: "neon-snake", title: "Neon Snake", key: "neon_snake", kind: "retro",
    look: { body: "#0f2a1e", trim: "#08140e", marquee: "#06180f", marqueeText: "#5dff9c", accent: "#22e07a" } },
  { id: "brick-breaker", title: "Brick Breaker", key: "brick_breaker", kind: "retro",
    look: { body: "#3a1420", trim: "#1c0910", marquee: "#22070f", marqueeText: "#ff7aa8", accent: "#ff3d7f" } },
  { id: "paddle-duel", title: "Paddle Duel", key: "paddle_duel", kind: "retro",
    look: { body: "#e8e4d8", trim: "#2a2a2e", marquee: "#151518", marqueeText: "#f5f5f0", accent: "#9aa3ff" } },
  { id: "block-drop", title: "Block Drop", key: "block_drop", kind: "retro",
    look: { body: "#14224a", trim: "#0a1126", marquee: "#0b1433", marqueeText: "#7cc4ff", accent: "#3fa2ff" } },
  { id: "maze-muncher", title: "Maze Muncher", key: "maze_muncher", kind: "retro",
    look: { body: "#2b1f05", trim: "#120d02", marquee: "#100c02", marqueeText: "#ffd23f", accent: "#ffb800" } },
  { id: "desert-dash", title: "Desert Dash", key: "desert_dash", kind: "retro",
    look: { body: "#5a3416", trim: "#2a180a", marquee: "#2b1607", marqueeText: "#ffb36b", accent: "#ff8a3d" } },
  { id: "pixel-raiders", title: "Pixel Raiders", key: "pixel_raiders", kind: "retro",
    look: { body: "#1a1035", trim: "#0c0719", marquee: "#0d0820", marqueeText: "#c58bff", accent: "#a855f7" } },
  { id: "road-hopper", title: "Road Hopper", key: "road_hopper", kind: "retro",
    look: { body: "#123a2e", trim: "#081a14", marquee: "#071b14", marqueeText: "#b6f36b", accent: "#7ed957" } },
  { id: "city-defense", title: "City Defense", key: "city_defense", kind: "retro",
    look: { body: "#3b0d0d", trim: "#1a0505", marquee: "#1c0404", marqueeText: "#ffcf5a", accent: "#ff4b2b" } },
];

const BY_ID = new Map<string, ArcadeGameInfo>(ARCADE_GAMES.map((g) => [g.id, g]));

/** The game behind a cabinet furniture id (`cabinet-<gameId>`), or null for anything else. */
export function gameForCabinet(furnitureId: string): ArcadeGameInfo | null {
  return furnitureId.startsWith(CABINET_PREFIX) ? BY_ID.get(furnitureId.slice(CABINET_PREFIX.length)) ?? null : null;
}

export function arcadeGame(id: string): ArcadeGameInfo | null {
  return BY_ID.get(id) ?? null;
}

/** Cabinets on the arcade floor are furniture of kind "retroCabinet" with the id `cabinet-<gameId>`. */
export const CABINET_PREFIX = "cabinet-";

export function cabinetId(game: ArcadeGameId): string {
  return CABINET_PREFIX + game;
}

// eslint-disable-next-line @typescript-eslint/no-explicit-any
type AnyRetroGame = RetroGame<any>;

/** Code-split loaders: a game's module is fetched the first time it is needed. */
const LOADERS: Record<RetroGameId, () => Promise<{ default: AnyRetroGame }>> = {
  "neon-snake": () => import("./games/neonSnake"),
  "brick-breaker": () => import("./games/brickBreaker"),
  "paddle-duel": () => import("./games/paddleDuel"),
  "block-drop": () => import("./games/blockDrop"),
  "maze-muncher": () => import("./games/mazeMuncher"),
  "desert-dash": () => import("./games/desertDash"),
  "pixel-raiders": () => import("./games/pixelRaiders"),
  "road-hopper": () => import("./games/roadHopper"),
  "city-defense": () => import("./games/cityDefense"),
};

const loaded = new Map<RetroGameId, Promise<AnyRetroGame>>();

/** Load a retro game's definition (cached; a failed load is retried next time). */
export function loadRetroGame(id: RetroGameId): Promise<AnyRetroGame> {
  let pending = loaded.get(id);
  if (!pending) {
    pending = LOADERS[id]().then((m) => m.default);
    pending.catch(() => loaded.delete(id));
    loaded.set(id, pending);
  }
  return pending;
}

export function isRetroGame(id: ArcadeGameId): id is RetroGameId {
  return id !== "asteroid-run";
}
