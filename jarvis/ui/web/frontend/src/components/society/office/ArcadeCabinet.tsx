/**
 * The break-room arcade, playable: walking up to the cabinet and pressing E
 * opens this overlay with "Asteroid Run" in 3D. The rules live in
 * arcadeGame.ts, the 3D view in ArcadeScene.tsx; this component owns the
 * keyboard, the HUD and the WebGL surface. It is a modal dialog, so the
 * office character stands still while you play.
 */
import { Component, useCallback, useEffect, useRef, useState, type ReactNode } from "react";
import { Canvas } from "@react-three/fiber";
import { useReducedMotion } from "framer-motion";
import { useT } from "@/i18n";
import { useWebglSurface } from "@/hooks/useWebglSurface";
import { IDLE_INPUT, MAX_MISSILES, MAX_SHIELDS, arcadeLevel, newArcade, type ArcadeInput, type ArcadePhase, type ArcadeState } from "./arcadeGame";
import { ArcadeScene } from "./ArcadeScene";
import "./arcade.css";

const BEST_KEY = "jarvis.office.arcade.asteroids.best";

function readBest(): number {
  try { return Number(window.localStorage.getItem(BEST_KEY)) || 0; } catch { return 0; }
}
function writeBest(score: number): void {
  // Private windows and blocked site data throw here; the best score is only a nicety.
  try { window.localStorage.setItem(BEST_KEY, String(score)); } catch { /* not persisted this session */ }
}

/** A scene that throws shows a quiet line instead of taking the office down with it. */
class SceneBoundary extends Component<{ fallback: string; children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: unknown) { console.warn("[arcade] 3D scene failed", error); }
  render() { return this.state.failed ? <p className="office-arcade-fallback" role="status">{this.props.fallback}</p> : this.props.children; }
}

const KEY_LEFT = new Set(["ArrowLeft", "KeyA"]);
const KEY_RIGHT = new Set(["ArrowRight", "KeyD"]);
const KEY_UP = new Set(["ArrowUp", "KeyW"]);
const KEY_DOWN = new Set(["ArrowDown", "KeyS"]);
const KEY_BOOST = new Set(["ShiftLeft", "ShiftRight"]);

interface Hud { score: number; shields: number; missiles: number; level: number; boost: number; boosting: boolean; rapid: boolean; phase: ArcadePhase }

export function ArcadeCabinet({ onClose }: { onClose: () => void }) {
  const t = useT();
  const host = useRef<HTMLDivElement>(null);
  const dialog = useRef<HTMLDivElement>(null);
  const game = useRef<ArcadeState>(newArcade());
  const input = useRef<ArcadeInput>({ ...IDLE_INPUT });
  const reduced = useReducedMotion() ?? false;
  const { generation } = useWebglSurface(host);
  const [hud, setHud] = useState<Hud>({ score: 0, shields: 3, missiles: MAX_MISSILES, level: 1, boost: 1, boosting: false, rapid: false, phase: "ready" });
  const [best, setBest] = useState(readBest);
  const bestRef = useRef(best);
  const lastPhase = useRef<ArcadePhase>("ready");

  // The HUD follows the game at a relaxed pace; the scene itself runs every frame.
  const sync = useCallback(() => {
    const s = game.current;
    const next: Hud = {
      score: Math.floor(s.score), shields: s.shields, missiles: s.missileAmmo, level: arcadeLevel(s), boost: Math.round(s.boost * 50) / 50,
      boosting: s.boosting, rapid: s.rapid > 0, phase: s.phase,
    };
    setHud((h) => (h.score === next.score && h.shields === next.shields && h.missiles === next.missiles && h.level === next.level && h.boost === next.boost
      && h.boosting === next.boosting && h.rapid === next.rapid && h.phase === next.phase ? h : next));
    if (next.score > bestRef.current) { bestRef.current = next.score; setBest(next.score); }
    // Store a new best once, when a run stops (game over or pause), not every frame.
    if (lastPhase.current === "playing" && s.phase !== "playing" && bestRef.current > readBest()) writeBest(bestRef.current);
    lastPhase.current = s.phase;
  }, []);

  useEffect(() => {
    const id = window.setInterval(sync, 80);
    return () => window.clearInterval(id);
  }, [sync]);

  // Closing mid-run still keeps a new best score.
  useEffect(() => () => { if (bestRef.current > readBest()) writeBest(bestRef.current); }, []);

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
      else if (KEY_UP.has(code)) input.current.up = true;
      else if (KEY_DOWN.has(code)) input.current.down = true;
      else if (KEY_BOOST.has(code)) input.current.boost = true;
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
      else if (KEY_BOOST.has(code)) input.current.boost = false;
      else if (code === "Space") input.current.fire = false;
    };
    // Losing window focus pauses the game and forgets held keys.
    const blur = () => {
      input.current = { ...IDLE_INPUT };
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

  useEffect(() => { dialog.current?.focus({ preventScroll: true }); }, []);

  const message = hud.phase === "ready" ? t("society.office.arcade_start")
    : hud.phase === "paused" ? t("society.office.arcade_paused")
      : hud.phase === "over" ? t("society.office.arcade_over").replace("{0}", String(hud.score))
        : null;

  return (
    <div className="office-arcade-backdrop" data-office-ui onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={dialog} className="office-arcade" role="dialog" data-state="open" aria-modal="true"
        aria-label={t("society.office.arcade_title")} tabIndex={-1}>
        <div ref={host} className="office-arcade-screen">
          <SceneBoundary fallback={t("society.office.no_graphics")}>
            <Canvas key={generation} dpr={[1, 1.75]} camera={{ fov: 62, near: 0.1, far: 220, position: [3.6, 3, 10.5] }}
              gl={{ antialias: true, powerPreference: "high-performance" }}>
              <ArcadeScene state={game} input={input} reduced={reduced} />
            </Canvas>
          </SceneBoundary>
          <div className="office-arcade-hud" aria-live="polite">
            <div className="office-arcade-hud-line">
              <span>{t("society.office.arcade_score")} {hud.score}</span>
              <span aria-hidden>·</span>
              <span aria-label={`${t("society.office.arcade_shields")} ${hud.shields}`}>
                {t("society.office.arcade_shields")}{" "}
                <span className="office-arcade-shields">
                  {Array.from({ length: MAX_SHIELDS }, (_, i) => <i key={i} data-on={i < hud.shields ? "true" : "false"} />)}
                </span>
              </span>
              <span aria-hidden>·</span>
              <span aria-label={`${t("society.office.arcade_missiles")} ${hud.missiles}`}>
                {t("society.office.arcade_missiles")}{" "}
                <span className="office-arcade-missiles">
                  {Array.from({ length: MAX_MISSILES }, (_, i) => <i key={i} data-on={i < hud.missiles ? "true" : "false"} />)}
                </span>
              </span>
              <span aria-hidden>·</span>
              <span data-active={hud.boosting ? "true" : "false"} className="office-arcade-boost-label">{t("society.office.arcade_boost")}</span>
              {hud.rapid && <><span aria-hidden>·</span><span className="office-arcade-rapid">{t("society.office.arcade_rapid")}</span></>}
            </div>
            <div className="office-arcade-bar" aria-hidden><i style={{ width: `${hud.boost * 100}%` }} /></div>
            <div className="office-arcade-hud-sub">
              {t("society.office.arcade_level")} {hud.level} · {t("society.office.arcade_best")} {best}
            </div>
          </div>
          <button type="button" className="office-arcade-close" onClick={onClose} aria-label={t("society.office.close")}>×</button>
          {message && (
            <button type="button" className="office-arcade-message" onClick={startOrResume}>
              <strong>{t("society.office.arcade_title")}</strong>
              <span>{message}</span>
            </button>
          )}
          <p className="office-arcade-keys">{t("society.office.arcade_keys")}</p>
        </div>
      </div>
    </div>
  );
}
