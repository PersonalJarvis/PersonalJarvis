/**
 * The break-room arcade, playable: walking up to the cabinet and pressing E
 * opens this overlay. The rules live in arcadeGame.ts; this component feeds
 * the keyboard in, runs a fixed-step loop and paints the playfield. It is a
 * modal dialog, so the office character stands still while you play.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/i18n";
import {
  ARCADE_H, ARCADE_SHIP_Y, ARCADE_W, invaderPos, newArcade, stepArcade,
  type ArcadeInput, type ArcadePhase, type ArcadeState,
} from "./arcadeGame";
import "./arcade.css";

const BEST_KEY = "jarvis.office.arcade.best";
const STEP = 1 / 120;
/** Canvas pixels per playfield pixel; CSS scales the canvas to fit. */
const SCALE = 3;

// Pixel sprites, two marching frames per invader row style ("X" = lit).
const SPRITES: Record<"squid" | "crab" | "octo", [string[], string[]]> = {
  squid: [
    ["....XXX....", "...XXXXX...", "..XXXXXXX..", ".XX.XXX.XX.", ".XXXXXXXXX.", "...X...X...", "..X.XXX.X..", ".X.X...X.X."],
    ["....XXX....", "...XXXXX...", "..XXXXXXX..", ".XX.XXX.XX.", ".XXXXXXXXX.", "..X.X.X.X..", ".X.......X.", "..X.....X.."],
  ],
  crab: [
    ["..X.....X..", "...X...X...", "..XXXXXXX..", ".XX.XXX.XX.", "XXXXXXXXXXX", "X.XXXXXXX.X", "X.X.....X.X", "...XX.XX..."],
    ["..X.....X..", "X..X...X..X", "X.XXXXXXX.X", "XXX.XXX.XXX", "XXXXXXXXXXX", ".XXXXXXXXX.", "..X.....X..", ".X.......X."],
  ],
  octo: [
    ["...XXXXX...", ".XXXXXXXXX.", "XXXXXXXXXXX", "XXX..X..XXX", "XXXXXXXXXXX", "..XX...XX..", ".XX.XXX.XX.", "XX.......XX"],
    ["...XXXXX...", ".XXXXXXXXX.", "XXXXXXXXXXX", "XXX..X..XXX", "XXXXXXXXXXX", "...XX.XX...", "..XX.X.XX..", "...XX.XX..."],
  ],
};
const ROW_SPRITE = ["squid", "crab", "crab", "octo", "octo"] as const;
const ROW_COLOUR = ["#ff7ab8", "#c792ff", "#c792ff", "#7cfc9a", "#7cfc9a"];
const SHIP = ["......X......", ".....XXX.....", ".XXXXXXXXXXX.", "XXXXXXXXXXXXX", "XXXXXXXXXXXXX"];

function readBest(): number {
  try { return Number(window.localStorage.getItem(BEST_KEY)) || 0; } catch { return 0; }
}
function writeBest(score: number): void {
  // Private windows and blocked site data throw here; the best score is only a nicety.
  try { window.localStorage.setItem(BEST_KEY, String(score)); } catch { /* not persisted this session */ }
}

function sprite(ctx: CanvasRenderingContext2D, rows: readonly string[], x: number, y: number): void {
  rows.forEach((line, dy) => {
    for (let dx = 0; dx < line.length; dx += 1) if (line[dx] === "X") ctx.fillRect(x + dx, y + dy, 1, 1);
  });
}

function paint(ctx: CanvasRenderingContext2D, s: ArcadeState, now: number): void {
  ctx.setTransform(SCALE, 0, 0, SCALE, 0, 0);
  ctx.fillStyle = "#07060f";
  ctx.fillRect(0, 0, ARCADE_W, ARCADE_H);
  // A few fixed stars, for depth.
  ctx.fillStyle = "#2a2645";
  for (let i = 0; i < 40; i += 1) ctx.fillRect((i * 97) % ARCADE_W, (i * 53 + 17) % (ARCADE_H - 40), 1, 1);
  for (const inv of s.invaders) {
    if (!inv.alive) continue;
    const { x, y } = invaderPos(s, inv);
    ctx.fillStyle = ROW_COLOUR[inv.row];
    sprite(ctx, SPRITES[ROW_SPRITE[inv.row]][s.frame], Math.round(x), Math.round(y));
  }
  ctx.fillStyle = "#fde68a";
  if (s.shot) ctx.fillRect(Math.round(s.shot.x), Math.round(s.shot.y), 1, 5);
  ctx.fillStyle = "#ff9e6b";
  for (const b of s.bombs) {
    const zig = Math.floor(b.y / 3) % 2;
    ctx.fillRect(Math.round(b.x) + zig, Math.round(b.y), 1, 2);
    ctx.fillRect(Math.round(b.x) - zig + 1, Math.round(b.y) + 2, 1, 2);
  }
  // The ship blinks while it recovers from a hit.
  if (s.phase !== "over" && (s.hitTimer <= 0 || Math.floor(now / 120) % 2 === 0)) {
    ctx.fillStyle = s.hitTimer > 0 ? "#ff6b6b" : "#7dd3fc";
    sprite(ctx, SHIP, Math.round(s.shipX) - 6, ARCADE_SHIP_Y - 2);
  }
  ctx.fillStyle = "#3b3470";
  ctx.fillRect(0, ARCADE_SHIP_Y + 6, ARCADE_W, 1);
  // Reserve lives, bottom left.
  ctx.fillStyle = "#7dd3fc";
  for (let i = 0; i < s.lives - 1; i += 1) sprite(ctx, SHIP, 6 + i * 16, ARCADE_H - 12);
}

const KEY_LEFT = new Set(["ArrowLeft", "KeyA"]);
const KEY_RIGHT = new Set(["ArrowRight", "KeyD"]);
const KEY_FIRE = new Set(["Space", "ArrowUp", "KeyW"]);

export function ArcadeCabinet({ onClose }: { onClose: () => void }) {
  const t = useT();
  const canvas = useRef<HTMLCanvasElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const game = useRef<ArcadeState>(newArcade());
  const input = useRef<ArcadeInput>({ left: false, right: false, fire: false });
  const [hud, setHud] = useState({ score: 0, lives: 3, wave: 1, phase: "ready" as ArcadePhase });
  const [best, setBest] = useState(readBest);
  const bestRef = useRef(best);

  const sync = useCallback(() => {
    const s = game.current;
    setHud((h) => (h.score === s.score && h.lives === s.lives && h.wave === s.wave && h.phase === s.phase
      ? h : { score: s.score, lives: s.lives, wave: s.wave, phase: s.phase }));
    if (s.score > bestRef.current) {
      bestRef.current = s.score;
      setBest(s.score);
      writeBest(s.score);
    }
  }, []);

  const startOrResume = useCallback(() => {
    const s = game.current;
    if (s.phase === "over") game.current = { ...newArcade(), phase: "playing" };
    else s.phase = "playing";
    input.current.fire = false;
    sync();
  }, [sync]);

  // Keyboard: the dialog owns every key while it is open (capture phase, so
  // the office's own Escape and E handlers never see them).
  useEffect(() => {
    const down = (event: KeyboardEvent) => {
      const s = game.current;
      const code = event.code;
      if (event.key === "Escape" || code === "KeyE") {
        event.preventDefault(); event.stopPropagation();
        if (!event.repeat) onClose();
        return;
      }
      if (code === "KeyP") {
        event.preventDefault(); event.stopPropagation();
        if (!event.repeat && (s.phase === "playing" || s.phase === "paused")) { s.phase = s.phase === "playing" ? "paused" : "playing"; sync(); }
        return;
      }
      if (KEY_LEFT.has(code)) input.current.left = true;
      else if (KEY_RIGHT.has(code)) input.current.right = true;
      else if (KEY_FIRE.has(code) || code === "Enter") {
        if (s.phase === "playing") input.current.fire = KEY_FIRE.has(code) || input.current.fire;
        else if (!event.repeat) startOrResume();
      } else return;
      event.preventDefault(); event.stopPropagation();
    };
    const up = (event: KeyboardEvent) => {
      if (KEY_LEFT.has(event.code)) input.current.left = false;
      else if (KEY_RIGHT.has(event.code)) input.current.right = false;
      else if (KEY_FIRE.has(event.code)) input.current.fire = false;
    };
    // Losing window focus pauses the game and forgets held keys.
    const blur = () => {
      input.current = { left: false, right: false, fire: false };
      if (game.current.phase === "playing") { game.current.phase = "paused"; sync(); }
    };
    document.addEventListener("keydown", down, true);
    document.addEventListener("keyup", up, true);
    window.addEventListener("blur", blur);
    return () => {
      document.removeEventListener("keydown", down, true);
      document.removeEventListener("keyup", up, true);
      window.removeEventListener("blur", blur);
    };
  }, [onClose, startOrResume, sync]);

  // Fixed-step simulation, painted once per animation frame.
  useEffect(() => {
    const ctx = canvas.current?.getContext("2d") ?? null;
    if (!ctx) return;
    ctx.imageSmoothingEnabled = false;
    let raf = 0;
    let last = performance.now();
    let acc = 0;
    const tick = (now: number) => {
      acc += Math.min(0.1, (now - last) / 1000);
      last = now;
      while (acc >= STEP) { stepArcade(game.current, input.current, STEP); acc -= STEP; }
      paint(ctx, game.current, now);
      sync();
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [sync]);

  useEffect(() => { dialog.current?.focus({ preventScroll: true }); }, []);

  const message = hud.phase === "ready" ? t("society.office.arcade_start")
    : hud.phase === "paused" ? t("society.office.arcade_paused")
      : hud.phase === "over" ? t("society.office.arcade_over").replace("{0}", String(hud.score))
        : null;

  return (
    <div className="office-arcade-backdrop" data-office-ui onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={dialog} className="office-card office-arcade" role="dialog" data-state="open" aria-modal="true"
        aria-label={t("society.office.arcade_title")} tabIndex={-1}>
        <header className="office-arcade-head">
          <h2>{t("society.office.arcade_title")}</h2>
          <button type="button" className="office-icon-button" onClick={onClose} aria-label={t("society.office.close")}>×</button>
        </header>
        <div className="office-arcade-score" aria-live="polite">
          <span>{t("society.office.arcade_score")} <b>{hud.score}</b></span>
          <span>{t("society.office.arcade_wave")} <b>{hud.wave}</b></span>
          <span>{t("society.office.arcade_lives")} <b>{hud.lives}</b></span>
          <span>{t("society.office.arcade_best")} <b>{best}</b></span>
        </div>
        <div className="office-arcade-screen">
          <canvas ref={canvas} width={ARCADE_W * SCALE} height={ARCADE_H * SCALE} aria-hidden />
          {message && (
            <button type="button" className="office-arcade-message" onClick={startOrResume}>{message}</button>
          )}
        </div>
        <p className="office-hint office-arcade-keys">{t("society.office.arcade_keys")}</p>
      </div>
    </div>
  );
}
