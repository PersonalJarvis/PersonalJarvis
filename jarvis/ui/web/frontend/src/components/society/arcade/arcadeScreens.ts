/**
 * Everything the arcade floor draws on canvases: each cabinet's attract
 * screen and lit marquee, the neon signs on the back walls, the faces of the
 * other machines (prize counter, token changer, claw machine, pinball, air
 * hockey, snack bar) and the live preview of the cabinet the person stands at.
 *
 * No words: only the games' own titles (never translated, see arcadeGames),
 * digits and symbols. Static faces are drawn once and cached; an attract
 * screen holds two frames stacked in one texture, and the hall flips between
 * them by moving the texture's offset, so ten cabinets animate for free.
 */
import { CanvasTexture, MeshBasicMaterial, MeshStandardMaterial, SRGBColorSpace, type Texture } from "three";
import { cachedCanvasTexture, canvasMaterial } from "../office/canvasMaterials";
import type { ArcadeGameId, ArcadeGameInfo, CabinetLook } from "./arcadeGames";
import { retroFont, seededRandom, type RetroDrawInfo, type RetroGame } from "./retroGame";
import { HALL, lcg, starPath } from "./arcadeHallLook";

type Ctx = CanvasRenderingContext2D;

/** One attract-screen frame in canvas pixels (4:3, like the cabinets' tubes). */
export const SCREEN_PX = { w: 256, h: 192 } as const;
export const MARQUEE_PX = { w: 512, h: 128 } as const;

/** The look of a cabinet whose id names no game (a test piece, a game removed from the registry). */
export const BLANK_LOOK: CabinetLook = { body: "#1d1a3a", trim: "#0e0c1f", marquee: "#120f2a", marqueeText: "#facc15", accent: "#8b5cff" };

// ---------------------------------------------------------------------------
// Fonts
// ---------------------------------------------------------------------------

const fontWaiters = new Set<string>();

/**
 * Redraw a cached canvas texture once the pixel face has loaded: a texture
 * drawn before that would keep the fallback font for the whole session.
 */
function redrawWhenFontLoads(key: string, texture: Texture | null, draw: (ctx: Ctx, w: number, h: number) => void): void {
  if (!texture || fontWaiters.has(key) || typeof document === "undefined" || !("fonts" in document)) return;
  const font = retroFont(20);
  if (document.fonts.check(font)) return;
  fontWaiters.add(key);
  document.fonts.load(font).then(() => {
    const canvas = texture.image as HTMLCanvasElement | undefined;
    const ctx = canvas?.getContext?.("2d");
    if (!canvas || !ctx) return;
    draw(ctx, canvas.width, canvas.height);
    texture.needsUpdate = true;
  }, () => {
    // The face failed to load: the fallback monospace already on the texture stays, which is legible.
  });
}

/** A cached material showing a canvas that writes in the pixel face. */
function fontCanvasMaterial(key: string, w: number, h: number, draw: (ctx: Ctx, w: number, h: number) => void,
  options: Parameters<typeof canvasMaterial>[4]): MeshStandardMaterial {
  const material = canvasMaterial(key, w, h, draw, options);
  redrawWhenFontLoads(key, material.map, draw);
  return material;
}

// ---------------------------------------------------------------------------
// Pixel helpers
// ---------------------------------------------------------------------------

/** Paint a little bitmap: each character of `rows` is one block of `size` pixels; "." is empty. */
function pixels(ctx: Ctx, rows: readonly string[], x: number, y: number, size: number, colours: Record<string, string>): void {
  rows.forEach((row, r) => {
    for (let c = 0; c < row.length; c += 1) {
      const colour = colours[row[c]];
      if (!colour) continue;
      ctx.fillStyle = colour;
      ctx.fillRect(x + c * size, y + r * size, size, size);
    }
  });
}

/** Fit `text` into `room` pixels by shrinking the pixel face from `px` down to `min`. */
function fitFont(ctx: Ctx, text: string, room: number, px: number, min: number, weight = 700): void {
  let size = px;
  ctx.font = retroFont(size, weight);
  while (size > min && ctx.measureText(text).width > room) {
    size -= 2;
    ctx.font = retroFont(size, weight);
  }
}

/** Faint horizontal scanlines over a whole screen frame. */
function scanlines(ctx: Ctx, y0: number, w: number, h: number): void {
  ctx.fillStyle = "rgba(0,0,0,0.18)";
  for (let y = 0; y < h; y += 3) ctx.fillRect(0, y0 + y, w, 1);
}

// ---------------------------------------------------------------------------
// Attract screens
// ---------------------------------------------------------------------------

type ScreenArt = (ctx: Ctx, w: number, h: number, frame: 0 | 1, look: CabinetLook) => void;

const ALIEN: readonly (readonly string[])[] = [
  ["...XXXXX...", ".XXXXXXXXX.", "XX.XX.XX.XX", "XXXXXXXXXXX", ".X.X...X.X.", "X.........X"],
  ["...XXXXX...", ".XXXXXXXXX.", "XX.XX.XX.XX", "XXXXXXXXXXX", "..X.X.X.X..", ".X.......X."],
];

const ART: Record<ArcadeGameId, ScreenArt> = {
  "neon-snake": (ctx, w, h, frame) => {
    ctx.strokeStyle = "rgba(34,224,122,0.12)";
    for (let x = 0; x < w; x += 16) ctx.strokeRect(x, 40, 16, h);
    const path = [[3, 9], [4, 9], [5, 9], [6, 9], [6, 8], [6, 7], [7, 7], [8, 7], [9, 7], [10, 7], [10, 8], [10, 9], [11, 9], [12, 9]];
    const body = path.slice(frame, frame + 12);
    body.forEach(([cx, cy], i) => {
      ctx.fillStyle = i === body.length - 1 ? "#d9ffe9" : i % 2 === 0 ? "#22e07a" : "#5dff9c";
      ctx.fillRect(cx * 16 + 1, cy * 16 + 1, 14, 14);
    });
    ctx.fillStyle = "#ff4d6d";
    ctx.fillRect(13 * 16 + 3, 6 * 16 + 3, 10, 10);
    ctx.fillStyle = "#5dff9c";
    ctx.fillRect(13 * 16 + 8, 6 * 16 - 1, 3, 5);
  },
  "brick-breaker": (ctx, w, _h, frame, look) => {
    const colours = ["#ff3d7f", "#ff8a3d", "#ffd23f", "#39ff88", "#2de2e6"];
    for (let row = 0; row < 5; row += 1) {
      for (let col = 0; col < 10; col += 1) {
        if (frame === 1 && row === 4 && col === 6) continue;
        ctx.fillStyle = colours[row];
        ctx.fillRect(8 + col * 24, 44 + row * 11, 22, 9);
      }
    }
    ctx.fillStyle = look.marqueeText;
    ctx.fillRect(frame === 0 ? 96 : 120, 172, 44, 7);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(frame === 0 ? 150 : 158, frame === 0 ? 132 : 112, 7, 7);
    ctx.fillStyle = "rgba(255,255,255,0.08)";
    ctx.fillRect(0, 186, w, 6);
  },
  "paddle-duel": (ctx, w, h, frame) => {
    ctx.fillStyle = "#f5f5f0";
    for (let y = 42; y < h; y += 14) ctx.fillRect(w / 2 - 2, y, 4, 8);
    ctx.fillRect(16, frame === 0 ? 86 : 100, 7, 38);
    ctx.fillRect(w - 23, frame === 0 ? 120 : 104, 7, 38);
    ctx.fillRect(frame === 0 ? 88 : 150, frame === 0 ? 110 : 128, 8, 8);
    ctx.font = retroFont(34, 700);
    ctx.textAlign = "center";
    ctx.fillText("3", w / 2 - 40, 76);
    ctx.fillText("5", w / 2 + 40, 76);
  },
  "block-drop": (ctx, _w, h, frame) => {
    const cell = 12, x0 = 68, wellW = 10;
    ctx.fillStyle = "#3fa2ff";
    ctx.fillRect(x0 - 4, 38, 4, h - 38);
    ctx.fillRect(x0 + wellW * cell, 38, 4, h - 38);
    const colours = ["#3fa2ff", "#ffd23f", "#ff3d7f", "#39ff88", "#b18cff", "#ff8a3d"];
    const stack = ["..........", ".....AA...", "B...AAC..D", "BB.EEECCDD", "BFFFEEECDD", "BBFFAACCDD"];
    stack.forEach((row, r) => {
      for (let c = 0; c < row.length; c += 1) {
        if (row[c] === ".") continue;
        ctx.fillStyle = colours[(row.charCodeAt(c) - 65) % colours.length];
        ctx.fillRect(x0 + c * cell + 1, h - (stack.length - r) * cell + 1, cell - 2, cell - 2);
      }
    });
    // The falling piece, one row lower on the second frame.
    ctx.fillStyle = "#b18cff";
    const py = 64 + frame * cell;
    for (const [dx, dy] of [[0, 0], [1, 0], [2, 0], [1, 1]]) ctx.fillRect(x0 + (3 + dx) * cell + 1, py + dy * cell + 1, cell - 2, cell - 2);
  },
  "maze-muncher": (ctx, w, h, frame) => {
    ctx.strokeStyle = "#2f6bff";
    ctx.lineWidth = 3;
    ctx.strokeRect(10, 42, w - 20, h - 52);
    for (const [x, y, ww, hh] of [[40, 70, 50, 20], [166, 70, 50, 20], [40, 130, 50, 20], [166, 130, 50, 20], [108, 98, 40, 26]]) {
      ctx.strokeRect(x, y, ww, hh);
    }
    ctx.fillStyle = "#ffd9a0";
    for (let x = 26; x < w - 20; x += 14) {
      ctx.fillRect(x, 56, 3, 3);
      ctx.fillRect(x, h - 26, 3, 3);
    }
    const mx = frame === 0 ? 70 : 84, my = 113;
    ctx.fillStyle = "#ffd23f";
    ctx.beginPath();
    const open = frame === 0 ? 0.6 : 0.12;
    ctx.moveTo(mx, my);
    ctx.arc(mx, my, 11, open, Math.PI * 2 - open);
    ctx.closePath();
    ctx.fill();
    // A round three-eyed blob with an antenna gives chase.
    const bx = frame === 0 ? 176 : 166;
    ctx.fillStyle = "#ff5ad1";
    ctx.beginPath();
    ctx.arc(bx, my, 11, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillRect(bx - 1, my - 18, 2, 7);
    ctx.fillStyle = "#ffffff";
    for (const dx of [-5, 0, 5]) ctx.fillRect(bx + dx - 1.5, my - 4, 3, 4);
  },
  "desert-dash": (ctx, w, h, frame) => {
    const sky = ctx.createLinearGradient(0, 40, 0, h);
    sky.addColorStop(0, "#3a1660");
    sky.addColorStop(1, "#ff8a3d");
    ctx.fillStyle = sky;
    ctx.fillRect(0, 40, w, h - 40);
    ctx.fillStyle = "#ffd23f";
    ctx.beginPath();
    ctx.arc(196, 92, 22, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#5a3416";
    ctx.beginPath();
    ctx.moveTo(0, 160);
    for (let x = 0; x <= w; x += 8) ctx.lineTo(x, 156 + Math.sin(x * 0.04) * 6);
    ctx.lineTo(w, h);
    ctx.lineTo(0, h);
    ctx.fill();
    const cx = frame === 0 ? 170 : 150;
    ctx.fillStyle = "#2f8f4e";
    ctx.fillRect(cx, 128, 9, 32);
    ctx.fillRect(cx - 9, 136, 9, 5);
    ctx.fillRect(cx - 9, 128, 4, 10);
    ctx.fillRect(cx + 9, 140, 8, 5);
    ctx.fillRect(cx + 13, 132, 4, 10);
    // The runner: head, body and legs that swap between frames.
    ctx.fillStyle = "#ffe8c2";
    ctx.fillRect(60, 124, 10, 10);
    ctx.fillRect(58, 134, 12, 14);
    ctx.fillRect(frame === 0 ? 56 : 62, 148, 5, 10);
    ctx.fillRect(frame === 0 ? 66 : 60, 148, 5, 10);
  },
  "pixel-raiders": (ctx, w, _h, frame) => {
    const colours = ["#c58bff", "#2de2e6", "#39ff88"];
    for (let row = 0; row < 3; row += 1) {
      for (let col = 0; col < 6; col += 1) {
        pixels(ctx, ALIEN[frame], 22 + col * 36 + frame * 6, 48 + row * 26, 2.5, { X: colours[row] });
      }
    }
    ctx.fillStyle = "#39ff88";
    ctx.fillRect(116, 172, 24, 8);
    ctx.fillRect(124, 166, 8, 6);
    ctx.fillStyle = "#ffffff";
    ctx.fillRect(127, frame === 0 ? 140 : 128, 2, 10);
    ctx.fillStyle = "#5a7d4a";
    for (const x of [30, 100, 170]) ctx.fillRect(x + 6, 150, 40, 10);
    ctx.fillStyle = "rgba(255,255,255,0.1)";
    ctx.fillRect(0, 186, w, 2);
  },
  "road-hopper": (ctx, w, h, frame) => {
    ctx.fillStyle = "#123a6b";
    ctx.fillRect(0, 44, w, 46);
    ctx.fillStyle = "#7a4a22";
    ctx.fillRect(frame === 0 ? 20 : 34, 50, 70, 12);
    ctx.fillRect(frame === 0 ? 150 : 136, 72, 80, 12);
    ctx.fillStyle = "#2a2a33";
    ctx.fillRect(0, 96, w, 70);
    ctx.fillStyle = "#e9e2d6";
    for (let x = 0; x < w; x += 24) ctx.fillRect(x + (frame * 12), 130, 12, 2);
    ctx.fillStyle = "#ff4b2b";
    ctx.fillRect(frame === 0 ? 30 : 46, 104, 34, 16);
    ctx.fillStyle = "#ffd23f";
    ctx.fillRect(frame === 0 ? 190 : 174, 140, 40, 16);
    ctx.fillStyle = "#3fa2ff";
    ctx.fillRect(frame === 0 ? 110 : 126, 140, 30, 16);
    ctx.fillStyle = "#3b6b2e";
    ctx.fillRect(0, 166, w, h - 166);
    pixels(ctx, frame === 0 ? ["X.X", "XXX", "X.X"] : [".X.", "XXX", "X.X"], 118, 170, 6, { X: "#7ed957" });
  },
  "city-defense": (ctx, w, h, frame) => {
    ctx.fillStyle = "#0a0f2a";
    ctx.fillRect(0, 40, w, h - 40);
    ctx.fillStyle = "#ffffff";
    for (let i = 0; i < 26; i += 1) ctx.fillRect((i * 97) % w, 44 + ((i * 53) % 70), 1.5, 1.5);
    ctx.strokeStyle = "#ff4b2b";
    ctx.lineWidth = 2;
    for (const [x0, x1] of [[30, 90], [140, 110], [230, 190]]) {
      const len = frame === 0 ? 0.55 : 0.75;
      ctx.beginPath();
      ctx.moveTo(x0, 42);
      ctx.lineTo(x0 + (x1 - x0) * len, 42 + 120 * len);
      ctx.stroke();
    }
    ctx.fillStyle = frame === 0 ? "rgba(255,207,90,0.85)" : "rgba(255,207,90,0.45)";
    ctx.beginPath();
    ctx.arc(176, 96, frame === 0 ? 12 : 18, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#ffcf5a";
    for (const [x, ww, hh] of [[14, 22, 20], [44, 18, 28], [92, 26, 16], [150, 20, 24], [196, 24, 18], [226, 18, 26]]) ctx.fillRect(x, h - 14 - hh, ww, hh);
    ctx.fillStyle = "#7a4a22";
    ctx.fillRect(0, h - 14, w, 14);
    ctx.fillStyle = "#2de2e6";
    ctx.beginPath();
    ctx.arc(128, h - 14, 12, Math.PI, 0);
    ctx.fill();
  },
  "asteroid-run": (ctx, w, h, frame) => {
    ctx.fillStyle = "#07060f";
    ctx.fillRect(0, 40, w, h - 40);
    const rand = lcg(0xa57e);
    for (let i = 0; i < 46; i += 1) {
      const x = rand() * w, y = 42 + rand() * (h - 44), len = 2 + rand() * 6 + frame * 4;
      ctx.fillStyle = `rgba(201,212,255,${0.4 + rand() * 0.5})`;
      ctx.fillRect(x, y, len, 1.5);
    }
    ctx.fillStyle = "#8d93b8";
    for (const [x, y, r] of [[200, 70, 16], [46, 150, 11], [222, 158, 9]]) {
      ctx.beginPath();
      for (let k = 0; k < 7; k += 1) {
        const a = (k / 7) * Math.PI * 2, rr = r * (0.75 + ((k * 7) % 4) * 0.1);
        if (k === 0) ctx.moveTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr); else ctx.lineTo(x + Math.cos(a) * rr, y + Math.sin(a) * rr);
      }
      ctx.fill();
    }
    ctx.save();
    ctx.translate(112, 120);
    ctx.rotate(Math.PI / 2);
    ctx.fillStyle = frame === 0 ? "#ffcc33" : "#ff8a3d";
    ctx.beginPath(); ctx.moveTo(-6, 14); ctx.lineTo(0, frame === 0 ? 34 : 42); ctx.lineTo(6, 14); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#e0443e";
    ctx.beginPath(); ctx.moveTo(-8, 2); ctx.lineTo(-17, 16); ctx.lineTo(-7, 12); ctx.closePath(); ctx.fill();
    ctx.beginPath(); ctx.moveTo(8, 2); ctx.lineTo(17, 16); ctx.lineTo(7, 12); ctx.closePath(); ctx.fill();
    ctx.fillStyle = "#eef2f8";
    ctx.fillRect(-8, -14, 16, 28);
    ctx.fillStyle = "#3b82f6";
    ctx.beginPath(); ctx.moveTo(-8, -14); ctx.lineTo(0, -28); ctx.lineTo(8, -14); ctx.closePath(); ctx.fill();
    ctx.restore();
  },
};

/** One attract frame: the game's art, its title on top, a score of zeros and a blinking coin at the foot. */
export function drawScreenFrame(ctx: Ctx, y0: number, game: ArcadeGameInfo | null, frame: 0 | 1): void {
  const { w, h } = SCREEN_PX;
  const look = game?.look ?? BLANK_LOOK;
  ctx.save();
  ctx.beginPath();
  ctx.rect(0, y0, w, h);
  ctx.clip();
  ctx.translate(0, y0);
  ctx.fillStyle = "#05040b";
  ctx.fillRect(0, 0, w, h);
  if (game) ART[game.id](ctx, w, h, frame, look);
  // Title band.
  ctx.fillStyle = "rgba(5,4,11,0.82)";
  ctx.fillRect(0, 0, w, 38);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillStyle = look.marqueeText;
  if (game) {
    fitFont(ctx, game.title, w - 24, 24, 12);
    ctx.fillText(game.title, w / 2, 21);
  }
  ctx.font = retroFont(11);
  ctx.textAlign = "left";
  ctx.fillStyle = "#ffffff";
  ctx.fillText("000000", 8, 7);
  // The blinking coin: shown on the first frame only.
  if (frame === 0) {
    ctx.fillStyle = "#ffd23f";
    ctx.beginPath();
    ctx.arc(w - 14, 8, 5, 0, Math.PI * 2);
    ctx.fill();
  }
  scanlines(ctx, 0, w, h);
  ctx.restore();
}

/** Both attract frames stacked: frame 0 on top, frame 1 below. */
function drawScreens(game: ArcadeGameInfo | null) {
  return (ctx: Ctx) => {
    drawScreenFrame(ctx, 0, game, 0);
    drawScreenFrame(ctx, SCREEN_PX.h, game, 1);
  };
}

const framedScreens = new Set<string>();

/**
 * The cached, self-lit attract screen of a game (null = a blank screen).
 * Its texture shows one of two stacked frames; `setScreenFrame` flips them.
 */
export function screenMaterial(game: ArcadeGameInfo | null): MeshStandardMaterial {
  const key = `arcade:screen:${game?.id ?? "blank"}`;
  const material = fontCanvasMaterial(key, SCREEN_PX.w, SCREEN_PX.h * 2, drawScreens(game), { glow: 0.95, fallback: "#120f2a", roughness: 0.35 });
  if (material.map && !framedScreens.has(key)) {
    framedScreens.add(key);
    material.map.repeat.set(1, 0.5);
    // The canvas's top half is the upper half of the texture (v from 0.5 to 1).
    material.map.offset.set(0, 0.5);
  }
  return material;
}

/** Show attract frame 0 or 1 on a game's screen. */
export function setScreenFrame(game: ArcadeGameInfo, frame: 0 | 1): void {
  const map = screenMaterial(game).map;
  if (map) map.offset.y = frame === 0 ? 0.5 : 0;
}

// ---------------------------------------------------------------------------
// Marquees
// ---------------------------------------------------------------------------

function drawMarquee(game: ArcadeGameInfo | null) {
  return (ctx: Ctx, w: number, h: number) => {
    const look = game?.look ?? BLANK_LOOK;
    const g = ctx.createLinearGradient(0, 0, 0, h);
    g.addColorStop(0, look.marquee);
    g.addColorStop(0.5, look.body);
    g.addColorStop(1, look.marquee);
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, w, h);
    ctx.fillStyle = look.accent;
    ctx.fillRect(0, 6, w, 5);
    ctx.fillRect(0, h - 11, w, 5);
    // A pixel star at each end.
    for (const x of [26, w - 40]) pixels(ctx, ["..X..", ".XXX.", "XXXXX", ".XXX.", "..X.."], x, h / 2 - 10, 4, { X: look.marqueeText });
    if (!game) return;
    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    fitFont(ctx, game.title, w - 120, 62, 28);
    ctx.shadowColor = look.accent;
    ctx.shadowBlur = 14;
    ctx.fillStyle = look.marqueeText;
    ctx.fillText(game.title, w / 2, h / 2 + 3);
    ctx.shadowBlur = 0;
  };
}

/** The cached lit marquee of a game, its title drawn in the pixel face. */
export function marqueeMaterial(game: ArcadeGameInfo | null): MeshStandardMaterial {
  return fontCanvasMaterial(`arcade:marquee:${game?.id ?? "blank"}`, MARQUEE_PX.w, MARQUEE_PX.h, drawMarquee(game),
    { glow: 1.1, fallback: game?.look.marquee ?? BLANK_LOOK.marquee, roughness: 0.4 });
}

// ---------------------------------------------------------------------------
// Neon signs on the back walls
// ---------------------------------------------------------------------------

export type NeonSymbol = "joystick" | "alien" | "bolt" | "star" | "ticket" | "soda" | "claw" | "coin";

/** Stroke the current path as a neon tube: a wide soft halo, the coloured tube and a pale hot core. */
function tube(ctx: Ctx, colour: string, width: number): void {
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  ctx.shadowColor = colour;
  ctx.shadowBlur = width * 3;
  ctx.strokeStyle = colour;
  ctx.lineWidth = width;
  ctx.stroke();
  ctx.shadowBlur = width;
  ctx.stroke();
  ctx.shadowBlur = 0;
  ctx.strokeStyle = "rgba(255,255,255,0.85)";
  ctx.lineWidth = width * 0.35;
  ctx.stroke();
}

/** Each neon symbol drawn into an `s` × `s` square. */
export const NEON_DRAW: Record<NeonSymbol, (ctx: Ctx, s: number) => void> = {
  joystick: (ctx, s) => {
    ctx.beginPath();
    ctx.ellipse(s * 0.5, s * 0.74, s * 0.3, s * 0.1, 0, 0, Math.PI * 2);
    tube(ctx, HALL.neon.cyan, s * 0.035);
    ctx.beginPath();
    ctx.moveTo(s * 0.5, s * 0.72);
    ctx.lineTo(s * 0.42, s * 0.36);
    tube(ctx, HALL.neon.cyan, s * 0.035);
    ctx.beginPath();
    ctx.arc(s * 0.41, s * 0.28, s * 0.08, 0, Math.PI * 2);
    tube(ctx, HALL.neon.magenta, s * 0.035);
    ctx.beginPath();
    ctx.arc(s * 0.72, s * 0.68, s * 0.04, 0, Math.PI * 2);
    tube(ctx, HALL.neon.yellow, s * 0.03);
  },
  alien: (ctx, s) => {
    const rows = ALIEN[0], size = s / 15, x0 = s * 0.5 - (rows[0].length * size) / 2, y0 = s * 0.3;
    rows.forEach((row, r) => {
      for (let c = 0; c < row.length; c += 1) {
        if (row[c] !== "X") continue;
        ctx.beginPath();
        ctx.rect(x0 + c * size + size * 0.18, y0 + r * size + size * 0.18, size * 0.64, size * 0.64);
        tube(ctx, HALL.neon.green, size * 0.22);
      }
    });
  },
  bolt: (ctx, s) => {
    ctx.beginPath();
    ctx.moveTo(s * 0.58, s * 0.1);
    ctx.lineTo(s * 0.32, s * 0.52);
    ctx.lineTo(s * 0.5, s * 0.52);
    ctx.lineTo(s * 0.4, s * 0.9);
    ctx.lineTo(s * 0.7, s * 0.42);
    ctx.lineTo(s * 0.52, s * 0.42);
    ctx.closePath();
    tube(ctx, HALL.neon.yellow, s * 0.035);
  },
  star: (ctx, s) => {
    starPath(ctx, s * 0.5, s * 0.52, s * 0.36, s * 0.15);
    tube(ctx, HALL.neon.magenta, s * 0.035);
    for (let i = 0; i < 8; i += 1) {
      const a = (i / 8) * Math.PI * 2 + 0.2;
      ctx.beginPath();
      ctx.moveTo(s * 0.5 + Math.cos(a) * s * 0.42, s * 0.52 + Math.sin(a) * s * 0.42);
      ctx.lineTo(s * 0.5 + Math.cos(a) * s * 0.47, s * 0.52 + Math.sin(a) * s * 0.47);
      tube(ctx, HALL.neon.cyan, s * 0.025);
    }
  },
  ticket: (ctx, s) => {
    const x0 = s * 0.12, x1 = s * 0.88, y0 = s * 0.3, y1 = s * 0.7, r = s * 0.07;
    ctx.beginPath();
    ctx.moveTo(x0, y0);
    ctx.lineTo(x1, y0);
    ctx.arc(x1, (y0 + y1) / 2, r, -Math.PI / 2, Math.PI / 2, true);
    ctx.lineTo(x1, y1);
    ctx.lineTo(x0, y1);
    ctx.arc(x0, (y0 + y1) / 2, r, Math.PI / 2, -Math.PI / 2, true);
    ctx.closePath();
    tube(ctx, HALL.neon.orange, s * 0.03);
    starPath(ctx, s * 0.5, s * 0.5, s * 0.12, s * 0.05);
    tube(ctx, HALL.neon.yellow, s * 0.025);
  },
  soda: (ctx, s) => {
    ctx.beginPath();
    ctx.moveTo(s * 0.32, s * 0.32);
    ctx.lineTo(s * 0.68, s * 0.32);
    ctx.lineTo(s * 0.62, s * 0.86);
    ctx.lineTo(s * 0.38, s * 0.86);
    ctx.closePath();
    tube(ctx, HALL.neon.cyan, s * 0.032);
    ctx.beginPath();
    ctx.moveTo(s * 0.54, s * 0.32);
    ctx.lineTo(s * 0.6, s * 0.12);
    ctx.lineTo(s * 0.7, s * 0.12);
    tube(ctx, HALL.neon.magenta, s * 0.028);
    ctx.beginPath();
    ctx.moveTo(s * 0.36, s * 0.48);
    ctx.lineTo(s * 0.64, s * 0.48);
    tube(ctx, HALL.neon.magenta, s * 0.02);
  },
  claw: (ctx, s) => {
    ctx.beginPath();
    ctx.moveTo(s * 0.5, s * 0.06);
    ctx.lineTo(s * 0.5, s * 0.3);
    tube(ctx, HALL.neon.cyan, s * 0.03);
    ctx.beginPath();
    ctx.moveTo(s * 0.5, s * 0.3);
    ctx.quadraticCurveTo(s * 0.26, s * 0.36, s * 0.3, s * 0.56);
    ctx.moveTo(s * 0.5, s * 0.3);
    ctx.quadraticCurveTo(s * 0.74, s * 0.36, s * 0.7, s * 0.56);
    tube(ctx, HALL.neon.cyan, s * 0.03);
    // The heart it is about to grab.
    const cx = s * 0.5, cy = s * 0.7, r = s * 0.12;
    ctx.beginPath();
    ctx.moveTo(cx, cy + r * 1.2);
    ctx.bezierCurveTo(cx - r * 2, cy - r * 0.2, cx - r * 0.8, cy - r * 1.6, cx, cy - r * 0.5);
    ctx.bezierCurveTo(cx + r * 0.8, cy - r * 1.6, cx + r * 2, cy - r * 0.2, cx, cy + r * 1.2);
    tube(ctx, HALL.neon.magenta, s * 0.03);
  },
  coin: (ctx, s) => {
    ctx.beginPath();
    ctx.arc(s * 0.5, s * 0.5, s * 0.34, 0, Math.PI * 2);
    tube(ctx, HALL.neon.yellow, s * 0.035);
    ctx.beginPath();
    ctx.arc(s * 0.5, s * 0.5, s * 0.26, 0, Math.PI * 2);
    tube(ctx, HALL.neon.orange, s * 0.02);
    starPath(ctx, s * 0.5, s * 0.5, s * 0.14, s * 0.06);
    tube(ctx, HALL.neon.yellow, s * 0.025);
  },
};

const neonMaterials = new Map<NeonSymbol, MeshBasicMaterial>();

/** A neon sign's tubes on a transparent sheet; it glows on its own and lets the wall show between the tubes. */
export function neonMaterial(symbol: NeonSymbol): MeshBasicMaterial {
  let material = neonMaterials.get(symbol);
  if (!material) {
    const map = cachedCanvasTexture(`arcade:neon:${symbol}`, 256, 256, (ctx, w) => {
      ctx.clearRect(0, 0, w, w);
      NEON_DRAW[symbol](ctx, w);
    });
    material = map
      ? new MeshBasicMaterial({ map, transparent: true, depthWrite: false, toneMapped: false })
      : new MeshBasicMaterial({ color: HALL.neon.magenta, transparent: true, opacity: 0, depthWrite: false });
    neonMaterials.set(symbol, material);
  }
  return material;
}

let medallion: MeshBasicMaterial | null = null;
/** The star medallion inlaid in the carpet: a neon ring round a star, on a transparent sheet. */
export function medallionMaterial(): MeshBasicMaterial {
  let material = medallion;
  if (!material) {
    const map = cachedCanvasTexture("arcade:medallion", 512, 512, (ctx, w) => {
      ctx.clearRect(0, 0, w, w);
      ctx.beginPath();
      ctx.arc(w / 2, w / 2, w * 0.44, 0, Math.PI * 2);
      tube(ctx, HALL.neon.cyan, w * 0.018);
      ctx.beginPath();
      ctx.arc(w / 2, w / 2, w * 0.38, 0, Math.PI * 2);
      tube(ctx, HALL.neon.magenta, w * 0.012);
      starPath(ctx, w / 2, w / 2, w * 0.3, w * 0.12);
      tube(ctx, HALL.neon.yellow, w * 0.018);
    });
    material = map
      ? new MeshBasicMaterial({ map, transparent: true, depthWrite: false, toneMapped: false, polygonOffset: true, polygonOffsetFactor: -2 })
      : new MeshBasicMaterial({ color: HALL.neon.cyan, transparent: true, opacity: 0, depthWrite: false });
    medallion = material;
  }
  return material;
}

// ---------------------------------------------------------------------------
// Faces of the other machines
// ---------------------------------------------------------------------------

/** The prize counter's lit front: tickets and stars in a row. */
function drawPrizeSign(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#1b0b24";
  ctx.fillRect(0, 0, w, h);
  const step = w / 6;
  for (let i = 0; i < 6; i += 1) {
    const cx = step * (i + 0.5), cy = h / 2;
    if (i % 2 === 0) {
      ctx.fillStyle = HALL.neon.orange;
      ctx.fillRect(cx - 26, cy - 14, 52, 28);
      ctx.fillStyle = "#1b0b24";
      ctx.beginPath();
      ctx.arc(cx - 26, cy, 6, 0, Math.PI * 2);
      ctx.arc(cx + 26, cy, 6, 0, Math.PI * 2);
      ctx.fill();
      ctx.fillStyle = HALL.neon.yellow;
      starPath(ctx, cx, cy, 9, 4);
      ctx.fill();
    } else {
      ctx.fillStyle = HALL.neon.magenta;
      starPath(ctx, cx, cy, 20, 8);
      ctx.fill();
    }
  }
}

/** The token changer's panel: a bill turning into a stack of coins. */
function drawTokenFace(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#14141c";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "#39ff88";
  ctx.fillRect(w * 0.2, h * 0.1, w * 0.6, h * 0.2);
  ctx.fillStyle = "#14141c";
  ctx.beginPath();
  ctx.arc(w / 2, h * 0.2, h * 0.06, 0, Math.PI * 2);
  ctx.fill();
  ctx.fillStyle = "#ffffff";
  ctx.beginPath();
  ctx.moveTo(w / 2 - 12, h * 0.38);
  ctx.lineTo(w / 2 + 12, h * 0.38);
  ctx.lineTo(w / 2, h * 0.48);
  ctx.closePath();
  ctx.fill();
  for (let i = 0; i < 4; i += 1) {
    ctx.fillStyle = i % 2 === 0 ? HALL.neon.yellow : "#e0a92a";
    ctx.beginPath();
    ctx.ellipse(w / 2, h * 0.82 - i * h * 0.07, w * 0.22, h * 0.05, 0, 0, Math.PI * 2);
    ctx.fill();
  }
}

/** The claw machine's lit header: a heart between two stars. */
function drawClawHeader(ctx: Ctx, w: number, h: number): void {
  const g = ctx.createLinearGradient(0, 0, w, 0);
  g.addColorStop(0, "#2a0d3a");
  g.addColorStop(0.5, "#4a1660");
  g.addColorStop(1, "#2a0d3a");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  const cx = w / 2, cy = h / 2, r = h * 0.2;
  ctx.fillStyle = HALL.neon.magenta;
  ctx.beginPath();
  ctx.moveTo(cx, cy + r * 1.3);
  ctx.bezierCurveTo(cx - r * 2.2, cy - r * 0.2, cx - r * 0.8, cy - r * 1.8, cx, cy - r * 0.5);
  ctx.bezierCurveTo(cx + r * 0.8, cy - r * 1.8, cx + r * 2.2, cy - r * 0.2, cx, cy + r * 1.3);
  ctx.fill();
  ctx.fillStyle = HALL.neon.yellow;
  for (const x of [w * 0.2, w * 0.8]) {
    starPath(ctx, x, cy, h * 0.28, h * 0.12);
    ctx.fill();
  }
}

/** A pinball backglass: a ringed planet over a starfield, and the score reels. */
function drawBackglass(ctx: Ctx, w: number, h: number): void {
  const g = ctx.createLinearGradient(0, 0, 0, h);
  g.addColorStop(0, "#1a0b3a");
  g.addColorStop(1, "#3a1050");
  ctx.fillStyle = g;
  ctx.fillRect(0, 0, w, h);
  const rand = lcg(0xb411);
  for (let i = 0; i < 70; i += 1) {
    ctx.fillStyle = `rgba(255,255,255,${0.3 + rand() * 0.7})`;
    ctx.fillRect(rand() * w, rand() * h * 0.7, 2, 2);
  }
  ctx.fillStyle = HALL.neon.orange;
  ctx.beginPath();
  ctx.arc(w * 0.5, h * 0.4, w * 0.2, 0, Math.PI * 2);
  ctx.fill();
  ctx.strokeStyle = HALL.neon.cyan;
  ctx.lineWidth = 6;
  ctx.beginPath();
  ctx.ellipse(w * 0.5, h * 0.4, w * 0.36, w * 0.08, -0.25, 0, Math.PI * 2);
  ctx.stroke();
  ctx.fillStyle = HALL.neon.magenta;
  starPath(ctx, w * 0.18, h * 0.18, 16, 7);
  ctx.fill();
  ctx.fillStyle = "#05040b";
  ctx.fillRect(w * 0.12, h * 0.76, w * 0.76, h * 0.16);
  ctx.fillStyle = HALL.neon.yellow;
  ctx.font = retroFont(30, 700);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  ctx.fillText("000000", w / 2, h * 0.84 + 2);
}

/** A pinball playfield seen from above: lanes, lit inserts, bumper rings and the two flippers. */
function drawPlayfield(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#101a4a";
  ctx.fillRect(0, 0, w, h);
  ctx.strokeStyle = "#e9e2d6";
  ctx.lineWidth = 3;
  ctx.beginPath();
  ctx.moveTo(w * 0.85, h);
  ctx.lineTo(w * 0.85, h * 0.12);
  ctx.quadraticCurveTo(w * 0.85, 4, w * 0.5, 4);
  ctx.quadraticCurveTo(w * 0.1, 4, w * 0.1, h * 0.15);
  ctx.stroke();
  const inserts = [HALL.neon.magenta, HALL.neon.yellow, HALL.neon.cyan, HALL.neon.green, HALL.neon.orange];
  for (let i = 0; i < 5; i += 1) {
    ctx.fillStyle = inserts[i];
    ctx.beginPath();
    ctx.moveTo(w * 0.47, h * (0.5 + i * 0.05));
    ctx.lineTo(w * 0.53, h * (0.5 + i * 0.05));
    ctx.lineTo(w * 0.5, h * (0.535 + i * 0.05));
    ctx.closePath();
    ctx.fill();
  }
  ctx.strokeStyle = HALL.neon.cyan;
  ctx.lineWidth = 4;
  for (const [x, y] of [[0.32, 0.28], [0.62, 0.24], [0.48, 0.4]]) {
    ctx.beginPath();
    ctx.arc(w * x, h * y, w * 0.09, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.fillStyle = "#e9e2d6";
  ctx.save();
  ctx.translate(w * 0.36, h * 0.9);
  ctx.rotate(0.45);
  ctx.fillRect(0, -3, w * 0.16, 6);
  ctx.restore();
  ctx.save();
  ctx.translate(w * 0.64, h * 0.9);
  ctx.rotate(Math.PI - 0.45);
  ctx.fillRect(0, -3, w * 0.16, 6);
  ctx.restore();
}

/** The air-hockey surface: ice white, a red centre line, blue face-off circles, goal creases and air holes. */
function drawAirHockey(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#e8f4ff";
  ctx.fillRect(0, 0, w, h);
  ctx.fillStyle = "rgba(60,90,140,0.25)";
  for (let y = 10; y < h; y += 16) for (let x = 10; x < w; x += 16) ctx.fillRect(x, y, 2, 2);
  ctx.fillStyle = "#e0443e";
  ctx.fillRect(w / 2 - 3, 0, 6, h);
  ctx.strokeStyle = "#3b82f6";
  ctx.lineWidth = 5;
  ctx.beginPath();
  ctx.arc(w / 2, h / 2, h * 0.22, 0, Math.PI * 2);
  ctx.stroke();
  for (const x of [0, w]) {
    ctx.beginPath();
    ctx.arc(x, h / 2, h * 0.26, 0, Math.PI * 2);
    ctx.stroke();
  }
  ctx.fillStyle = "#1c2440";
  ctx.fillRect(0, h / 2 - h * 0.14, 8, h * 0.28);
  ctx.fillRect(w - 8, h / 2 - h * 0.14, 8, h * 0.28);
}

/** The snack bar's lit menu strip: burger, fries, soda, popcorn and ice cream, each with a price in coins. */
function drawSnackMenu(ctx: Ctx, w: number, h: number): void {
  ctx.fillStyle = "#1a0a0c";
  ctx.fillRect(0, 0, w, h);
  const step = w / 5, cy = h * 0.42;
  const items: ((x: number) => void)[] = [
    (x) => {
      ctx.fillStyle = "#e0a24a"; ctx.beginPath(); ctx.arc(x, cy, 18, Math.PI, 0); ctx.fill();
      ctx.fillStyle = "#6b3a1e"; ctx.fillRect(x - 18, cy + 2, 36, 7);
      ctx.fillStyle = "#39ff88"; ctx.fillRect(x - 19, cy, 38, 3);
      ctx.fillStyle = "#e0a24a"; ctx.fillRect(x - 17, cy + 10, 34, 7);
    },
    (x) => {
      ctx.fillStyle = HALL.neon.yellow;
      for (let i = -3; i <= 3; i += 1) ctx.fillRect(x + i * 4 - 1, cy - 22 + Math.abs(i) * 2, 3, 20);
      ctx.fillStyle = "#e0443e"; ctx.fillRect(x - 14, cy - 6, 28, 22);
    },
    (x) => {
      ctx.fillStyle = HALL.neon.cyan;
      ctx.beginPath(); ctx.moveTo(x - 13, cy - 16); ctx.lineTo(x + 13, cy - 16); ctx.lineTo(x + 9, cy + 18); ctx.lineTo(x - 9, cy + 18); ctx.fill();
      ctx.fillStyle = "#ffffff"; ctx.fillRect(x + 2, cy - 28, 3, 14);
    },
    (x) => {
      ctx.fillStyle = "#fff4d6";
      for (const [dx, dy] of [[-8, -14], [0, -18], [8, -14], [-4, -8], [5, -9]]) { ctx.beginPath(); ctx.arc(x + dx, cy + dy, 6, 0, Math.PI * 2); ctx.fill(); }
      ctx.fillStyle = "#e0443e"; ctx.fillRect(x - 13, cy - 8, 26, 26);
      ctx.fillStyle = "#ffffff"; ctx.fillRect(x - 4, cy - 8, 8, 26);
    },
    (x) => {
      ctx.fillStyle = "#e0a24a";
      ctx.beginPath(); ctx.moveTo(x - 11, cy - 4); ctx.lineTo(x + 11, cy - 4); ctx.lineTo(x, cy + 20); ctx.fill();
      ctx.fillStyle = "#ff8fc7"; ctx.beginPath(); ctx.arc(x, cy - 10, 12, 0, Math.PI * 2); ctx.fill();
    },
  ];
  ctx.font = retroFont(16, 700);
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  items.forEach((paint, i) => {
    const x = step * (i + 0.5);
    paint(x);
    // The price: a number of tokens.
    const price = String([3, 2, 1, 2, 1][i]);
    ctx.fillStyle = HALL.neon.yellow;
    ctx.beginPath();
    ctx.arc(x + 9, h * 0.86, 6, 0, Math.PI * 2);
    ctx.fill();
    ctx.fillStyle = "#ffffff";
    ctx.fillText(price, x - 5, h * 0.86 + 1);
  });
}

/** The faces' drawings, by face. */
export const FACE_DRAWINGS = {
  prizeSign: drawPrizeSign, token: drawTokenFace, clawHeader: drawClawHeader, backglass: drawBackglass,
  playfield: drawPlayfield, airHockey: drawAirHockey, snackMenu: drawSnackMenu,
} as const;

/** The machines' faces, each a cached self-lit material. */
export const FACES = {
  prizeSign: () => canvasMaterial("arcade:face:prize", 512, 96, drawPrizeSign, { glow: 0.85, fallback: "#1b0b24" }),
  token: () => canvasMaterial("arcade:face:token", 128, 160, drawTokenFace, { glow: 0.7, fallback: "#14141c" }),
  clawHeader: () => canvasMaterial("arcade:face:claw", 256, 64, drawClawHeader, { glow: 0.9, fallback: "#4a1660" }),
  backglass: () => fontCanvasMaterial("arcade:face:backglass", 256, 256, drawBackglass, { glow: 0.85, fallback: "#2a0d45" }),
  playfield: () => canvasMaterial("arcade:face:playfield", 128, 256, drawPlayfield, { glow: 0.45, fallback: "#101a4a", roughness: 0.3 }),
  airHockey: () => canvasMaterial("arcade:face:hockey", 512, 288, drawAirHockey, { glow: 0.3, fallback: "#e8f4ff", roughness: 0.25 }),
  snackMenu: () => fontCanvasMaterial("arcade:face:menu", 512, 96, drawSnackMenu, { glow: 0.75, fallback: "#1a0a0c" }),
};

// ---------------------------------------------------------------------------
// Live preview
// ---------------------------------------------------------------------------

/** How often the live preview repaints, per second. */
export const PREVIEW_FPS = 12;

export interface LiveScreen {
  material: MeshBasicMaterial;
  /** Advance the preview's clock by `dt` seconds; repaints at PREVIEW_FPS. */
  tick(dt: number): void;
  dispose(): void;
}

function defaultCanvas(): HTMLCanvasElement | null {
  return typeof document === "undefined" ? null : document.createElement("canvas");
}

/**
 * A game's own title screen, repainted a dozen times a second into a canvas
 * texture of the attract screen's size (the game's frame letterboxed into it).
 * Null where there is no 2D canvas. A game that throws while drawing freezes
 * on its last good frame: the preview is decoration, the overlay is the game.
 */
export function createLiveScreen(
  game: RetroGame<unknown>,
  { seed = 7, canvas = defaultCanvas }: { seed?: number; canvas?: () => HTMLCanvasElement | null } = {},
): LiveScreen | null {
  const source = canvas();
  const output = canvas();
  if (!source || !output) return null;
  let sourceCtx: Ctx | null = null, outputCtx: Ctx | null = null;
  try {
    sourceCtx = source.getContext("2d");
    outputCtx = output.getContext("2d");
  } catch {
    // jsdom without the canvas package throws "not implemented": no live preview, the static screen stays.
    return null;
  }
  if (!sourceCtx || !outputCtx) return null;
  let state: unknown;
  try {
    state = game.create(seededRandom(seed));
  } catch {
    // A game that cannot even start leaves its static attract screen; the overlay reports it when played.
    return null;
  }
  source.width = Math.max(1, game.width);
  source.height = Math.max(1, game.height);
  output.width = SCREEN_PX.w;
  output.height = SCREEN_PX.h;
  const texture = new CanvasTexture(output);
  texture.colorSpace = SRGBColorSpace;
  const material = new MeshBasicMaterial({ map: texture, toneMapped: false });
  const info: RetroDrawInfo = { time: 0, reduced: false, idle: true };
  // Letterbox: the largest copy of the game's frame that fits the screen.
  const scale = Math.min(SCREEN_PX.w / source.width, SCREEN_PX.h / source.height);
  const dw = source.width * scale, dh = source.height * scale;
  const dx = (SCREEN_PX.w - dw) / 2, dy = (SCREEN_PX.h - dh) / 2;
  const src = sourceCtx, out = outputCtx;
  out.imageSmoothingEnabled = false;
  let since = Infinity;
  let broken = false;
  const paint = () => {
    try {
      game.draw(src, state, info);
    } catch {
      // See above: a broken draw keeps the last good frame; the game's own overlay reports real errors.
      broken = true;
      return;
    }
    out.fillStyle = "#05040b";
    out.fillRect(0, 0, SCREEN_PX.w, SCREEN_PX.h);
    out.drawImage(source, dx, dy, dw, dh);
    scanlines(out, 0, SCREEN_PX.w, SCREEN_PX.h);
    texture.needsUpdate = true;
  };
  return {
    material,
    tick(dt: number) {
      if (broken) return;
      info.time += dt;
      since += dt;
      if (since < 1 / PREVIEW_FPS) return;
      since = 0;
      paint();
    },
    dispose() {
      texture.dispose();
      material.dispose();
    },
  };
}
