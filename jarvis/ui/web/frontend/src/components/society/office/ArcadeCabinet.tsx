/**
 * The break-room arcade, playable: walking up to the cabinet and pressing E
 * opens this overlay with "Asteroid Run". The rules live in arcadeGame.ts;
 * this component feeds the keyboard in, runs a fixed-step loop and paints
 * the playfield. It is a modal dialog, so the office character stands still
 * while you play.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { useT } from "@/i18n";
import {
  ARCADE_H, ARCADE_W, RAPID_S, ROCK_RADIUS, SHIP_RADIUS, arcadeLevel, newArcade, stepArcade,
  type ArcadeInput, type ArcadePhase, type ArcadeState, type PickupKind,
} from "./arcadeGame";
import "./arcade.css";

const BEST_KEY = "jarvis.office.arcade.asteroids.best";
const STEP = 1 / 120;
/** Canvas pixels per playfield unit; CSS scales the canvas to fit. */
const SCALE = 3;

const PICKUP_COLOUR: Record<PickupKind, string> = { shield: "#7dd3fc", rapid: "#fde68a", life: "#ff7ab8" };
const ROCK_FILL = ["#8f7b66", "#7d6a57", "#6b5a4a"];

function readBest(): number {
  try { return Number(window.localStorage.getItem(BEST_KEY)) || 0; } catch { return 0; }
}
function writeBest(score: number): void {
  // Private windows and blocked site data throw here; the best score is only a nicety.
  try { window.localStorage.setItem(BEST_KEY, String(score)); } catch { /* not persisted this session */ }
}

function drawShip(ctx: CanvasRenderingContext2D, s: ArcadeState, now: number): void {
  const { shipX: x, shipY: y } = s;
  const tilt = Math.max(-0.35, Math.min(0.35, s.vx / 400));
  ctx.save();
  ctx.translate(x, y);
  ctx.rotate(tilt);
  // Flame, flickering, longer while thrusting forward.
  const flame = 7 + Math.sin(now / 30) * 2 + (s.vy < 0 ? 4 : 0);
  ctx.fillStyle = "#ff7a59";
  ctx.beginPath(); ctx.moveTo(-3, 7); ctx.lineTo(0, 7 + flame); ctx.lineTo(3, 7); ctx.closePath(); ctx.fill();
  ctx.fillStyle = "#ffd166";
  ctx.beginPath(); ctx.moveTo(-1.6, 7); ctx.lineTo(0, 7 + flame * 0.6); ctx.lineTo(1.6, 7); ctx.closePath(); ctx.fill();
  // Fins.
  ctx.fillStyle = "#e0526b";
  ctx.beginPath(); ctx.moveTo(-4, 2); ctx.lineTo(-8, 9); ctx.lineTo(-3, 7); ctx.closePath(); ctx.fill();
  ctx.beginPath(); ctx.moveTo(4, 2); ctx.lineTo(8, 9); ctx.lineTo(3, 7); ctx.closePath(); ctx.fill();
  // Body.
  ctx.fillStyle = "#eef2f7";
  ctx.beginPath();
  ctx.moveTo(0, -11);
  ctx.quadraticCurveTo(5, -5, 4, 7);
  ctx.lineTo(-4, 7);
  ctx.quadraticCurveTo(-5, -5, 0, -11);
  ctx.fill();
  // Nose cone and window.
  ctx.fillStyle = "#e0526b";
  ctx.beginPath(); ctx.moveTo(0, -11); ctx.quadraticCurveTo(3.2, -7.5, 3.4, -5); ctx.lineTo(-3.4, -5); ctx.quadraticCurveTo(-3.2, -7.5, 0, -11); ctx.fill();
  ctx.fillStyle = "#38bdf8";
  ctx.beginPath(); ctx.arc(0, -1, 1.9, 0, Math.PI * 2); ctx.fill();
  ctx.restore();
  if (s.shield) {
    ctx.strokeStyle = `rgba(125, 211, 252, ${0.55 + Math.sin(now / 150) * 0.25})`;
    ctx.lineWidth = 1.2;
    ctx.beginPath(); ctx.arc(x, y, SHIP_RADIUS + 7, 0, Math.PI * 2); ctx.stroke();
  }
}

function drawPickup(ctx: CanvasRenderingContext2D, kind: PickupKind, x: number, y: number, t: number): void {
  const pulse = 1 + Math.sin(t * 6) * 0.12;
  ctx.save();
  ctx.translate(x, y);
  ctx.scale(pulse, pulse);
  ctx.strokeStyle = PICKUP_COLOUR[kind];
  ctx.fillStyle = PICKUP_COLOUR[kind];
  ctx.lineWidth = 1.2;
  ctx.beginPath(); ctx.arc(0, 0, 6, 0, Math.PI * 2); ctx.stroke();
  ctx.beginPath();
  if (kind === "shield") { ctx.moveTo(0, -3.5); ctx.lineTo(3, -2); ctx.lineTo(2.4, 1.8); ctx.lineTo(0, 3.6); ctx.lineTo(-2.4, 1.8); ctx.lineTo(-3, -2); }
  else if (kind === "rapid") { ctx.moveTo(0.8, -4); ctx.lineTo(-2.4, 0.6); ctx.lineTo(0, 0.6); ctx.lineTo(-0.8, 4); ctx.lineTo(2.4, -0.6); ctx.lineTo(0, -0.6); }
  else { ctx.moveTo(0, 3.4); ctx.bezierCurveTo(-5, -0.5, -2.2, -4.5, 0, -1.6); ctx.bezierCurveTo(2.2, -4.5, 5, -0.5, 0, 3.4); }
  ctx.closePath(); ctx.fill();
  ctx.restore();
}

function paint(ctx: CanvasRenderingContext2D, s: ArcadeState, now: number): void {
  const shake = s.shake * 10;
  const ox = shake > 0 ? (Math.random() - 0.5) * shake : 0, oy = shake > 0 ? (Math.random() - 0.5) * shake : 0;
  ctx.setTransform(SCALE, 0, 0, SCALE, ox * SCALE, oy * SCALE);
  const sky = ctx.createLinearGradient(0, 0, 0, ARCADE_H);
  sky.addColorStop(0, "#05040d");
  sky.addColorStop(1, "#120b2a");
  ctx.fillStyle = sky;
  ctx.fillRect(-10, -10, ARCADE_W + 20, ARCADE_H + 20);
  // Stars stretch into streaks as the run speeds up.
  const streak = 1 + Math.min(6, s.time / 20);
  for (const star of s.stars) {
    ctx.fillStyle = `rgba(220, 225, 255, ${0.25 + star.depth * 0.6})`;
    ctx.fillRect(star.x, star.y, star.depth > 0.7 ? 1.2 : 0.8, streak * star.depth);
  }
  for (const p of s.pickups) drawPickup(ctx, p.kind, p.x, p.y, p.t);
  for (const rock of s.rocks) {
    const r = ROCK_RADIUS[rock.size];
    ctx.save();
    ctx.translate(rock.x, rock.y);
    ctx.rotate(rock.angle);
    ctx.beginPath();
    rock.shape.forEach((k, i) => {
      const a = (i / rock.shape.length) * Math.PI * 2;
      if (i === 0) ctx.moveTo(Math.cos(a) * r * k, Math.sin(a) * r * k); else ctx.lineTo(Math.cos(a) * r * k, Math.sin(a) * r * k);
    });
    ctx.closePath();
    ctx.fillStyle = rock.flash > 0 ? "#ffffff" : ROCK_FILL[rock.size];
    ctx.fill();
    ctx.strokeStyle = "#b8a38a";
    ctx.lineWidth = 1;
    ctx.stroke();
    // A couple of craters.
    ctx.fillStyle = "rgba(0, 0, 0, 0.22)";
    ctx.beginPath(); ctx.arc(r * 0.3, -r * 0.2, r * 0.22, 0, Math.PI * 2); ctx.fill();
    ctx.beginPath(); ctx.arc(-r * 0.35, r * 0.3, r * 0.15, 0, Math.PI * 2); ctx.fill();
    ctx.restore();
  }
  ctx.fillStyle = "#fde68a";
  for (const shot of s.shots) ctx.fillRect(shot.x - 0.7, shot.y - 3, 1.4, 6);
  for (const p of s.particles) {
    ctx.globalAlpha = Math.max(0, p.life / p.max);
    ctx.fillStyle = p.colour;
    ctx.fillRect(p.x - 0.8, p.y - 0.8, 1.6, 1.6);
  }
  ctx.globalAlpha = 1;
  // The ship blinks while it is invulnerable after a hit.
  if (s.phase !== "over" && (s.invulnerable <= 0 || Math.floor(now / 100) % 2 === 0)) drawShip(ctx, s, now);
  // Rapid-fire time left, as a bar along the bottom.
  if (s.rapid > 0) {
    ctx.fillStyle = "#fde68a";
    ctx.fillRect(4, ARCADE_H - 4, (ARCADE_W - 8) * (s.rapid / RAPID_S), 1.5);
  }
}

const KEY_LEFT = new Set(["ArrowLeft", "KeyA"]);
const KEY_RIGHT = new Set(["ArrowRight", "KeyD"]);
const KEY_UP = new Set(["ArrowUp", "KeyW"]);
const KEY_DOWN = new Set(["ArrowDown", "KeyS"]);
const idleInput = (): ArcadeInput => ({ left: false, right: false, up: false, down: false, fire: false });

export function ArcadeCabinet({ onClose }: { onClose: () => void }) {
  const t = useT();
  const canvas = useRef<HTMLCanvasElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const game = useRef<ArcadeState>(newArcade());
  const input = useRef<ArcadeInput>(idleInput());
  const [hud, setHud] = useState({ score: 0, lives: 3, level: 1, phase: "ready" as ArcadePhase });
  const [best, setBest] = useState(readBest);
  const bestRef = useRef(best);
  const lastPhase = useRef<ArcadePhase>("ready");

  const sync = useCallback(() => {
    const s = game.current;
    const score = Math.floor(s.score), level = arcadeLevel(s);
    setHud((h) => (h.score === score && h.lives === s.lives && h.level === level && h.phase === s.phase
      ? h : { score, lives: s.lives, level, phase: s.phase }));
    if (score > bestRef.current) { bestRef.current = score; setBest(score); }
    // Store a new best once, when a run stops (game over or pause), not every frame.
    if (lastPhase.current === "playing" && s.phase !== "playing" && bestRef.current > readBest()) writeBest(bestRef.current);
    lastPhase.current = s.phase;
  }, []);

  const startOrResume = useCallback(() => {
    const s = game.current;
    if (s.phase === "over") game.current = { ...newArcade(), phase: "playing" };
    else s.phase = "playing";
    input.current.fire = false;
    sync();
  }, [sync]);

  // Closing mid-run still keeps a new best score.
  useEffect(() => () => { if (bestRef.current > readBest()) writeBest(bestRef.current); }, []);

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
      else if (KEY_UP.has(code)) input.current.up = true;
      else if (KEY_DOWN.has(code)) input.current.down = true;
      else if (code === "Space" || code === "Enter") {
        if (s.phase === "playing") input.current.fire = code === "Space" || input.current.fire;
        else if (!event.repeat) startOrResume();
      } else return;
      event.preventDefault(); event.stopPropagation();
    };
    const up = (event: KeyboardEvent) => {
      const code = event.code;
      if (KEY_LEFT.has(code)) input.current.left = false;
      else if (KEY_RIGHT.has(code)) input.current.right = false;
      else if (KEY_UP.has(code)) input.current.up = false;
      else if (KEY_DOWN.has(code)) input.current.down = false;
      else if (code === "Space") input.current.fire = false;
    };
    // Losing window focus pauses the game and forgets held keys.
    const blur = () => {
      input.current = idleInput();
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
          <span>{t("society.office.arcade_level")} <b>{hud.level}</b></span>
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
