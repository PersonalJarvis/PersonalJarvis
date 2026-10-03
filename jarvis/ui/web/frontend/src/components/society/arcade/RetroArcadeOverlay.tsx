/**
 * Standing at a cabinet on the arcade floor: the full-screen player for every
 * 2D retro game.
 *
 * The game itself is pure (retroGame.ts); this overlay owns everything around
 * it: loading the game's code, the cabinet look, the keyboard / gamepad /
 * touch controls, the fixed-step loop, the title / pause / game-over screens,
 * the HUD and the best score. It is a modal dialog, so the office character
 * stands still while you play, and it takes every key it uses in the capture
 * phase so the office never sees them.
 *
 * Phase changes are React state (rare); the loop itself runs on refs and
 * never sets state per frame. The HUD polls the game ten times a second.
 */
import { useCallback, useEffect, useLayoutEffect, useRef, useState, type CSSProperties, type PointerEvent as ReactPointerEvent } from "react";
import { useReducedMotion } from "framer-motion";
import { useT } from "@/i18n";
import { arcadeGame, loadRetroGame, type CabinetLook, type RetroGameId } from "./arcadeGames";
import { RETRO_STEP, type RetroGame } from "./retroGame";
import {
  clearEdges, createInputTracker, dpadDirections, firstGamepad, hasStartGesture, leavePointer, pressKey, readGamepad, releaseAll,
  releaseKey, screenScale, setPointer, setTouch, takeInput, toLogical, type GamepadLike, type InputTracker, type RetroButton,
} from "./retroInput";
import { createRetroClock, resetClock, stepsDue } from "./retroLoop";
import { readBest, saveBest } from "./retroScores";
import "./retroArcade.css";

type Phase = "loading" | "error" | "ready" | "playing" | "paused" | "over";

/** After a run ends, Space / Enter wait this long, so a held fire key does not skip the result. */
const RESTART_GUARD_MS = 600;
const HUD_POLL_MS = 100;
/** The screen size shown while a game's code is still loading. */
const PLACEHOLDER = { width: 320, height: 240 };
const FALLBACK_LOOK: CabinetLook = { body: "#1d1a3a", trim: "#0e0c1f", marquee: "#120f2a", marqueeText: "#facc15", accent: "#2dff5a" };

interface Hud { score: number; lives?: number; level?: number }
interface RunResult { score: number; best: number; newBest: boolean; won: boolean }
interface Run { game: RetroGame<unknown>; state: unknown }

type GameLoader = (id: RetroGameId) => Promise<RetroGame<unknown>>;

function sameHud(a: Hud, b: Hud): boolean {
  return a.score === b.score && a.lives === b.lives && a.level === b.level;
}

function readPads(): readonly (GamepadLike | null)[] | null {
  if (typeof navigator === "undefined" || typeof navigator.getGamepads !== "function") return null;
  try {
    return navigator.getGamepads();
  } catch {
    // A permissions policy can forbid the Gamepad API; the keyboard still works.
    return null;
  }
}

function useCoarsePointer(): boolean {
  const query = "(pointer: coarse)";
  const [coarse, setCoarse] = useState(() => typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(query).matches);
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return;
    const media = window.matchMedia(query);
    const change = () => setCoarse(media.matches);
    media.addEventListener?.("change", change);
    return () => media.removeEventListener?.("change", change);
  }, []);
  return coarse;
}

export function RetroArcadeOverlay({ gameId, onClose, loadGame = loadRetroGame }: {
  gameId: RetroGameId;
  onClose: () => void;
  loadGame?: GameLoader;
}) {
  const t = useT();
  const info = arcadeGame(gameId);
  const title = info?.title ?? gameId;
  const look = info?.look ?? FALLBACK_LOOK;
  const reduced = useReducedMotion() ?? false;
  const coarse = useCoarsePointer();

  const dialog = useRef<HTMLDivElement>(null);
  const stage = useRef<HTMLDivElement>(null);
  const canvas = useRef<HTMLCanvasElement>(null);
  const run = useRef<Run | null>(null);
  const tracker = useRef<InputTracker>(createInputTracker());
  const startRequest = useRef(false);
  const reducedRef = useRef(reduced);
  reducedRef.current = reduced;
  // Callers often pass fresh arrow functions; refs keep the listeners and the load from churning on every render.
  const closeRef = useRef(onClose);
  closeRef.current = onClose;
  const loaderRef = useRef(loadGame);
  loaderRef.current = loadGame;

  const [game, setGame] = useState<RetroGame<unknown> | null>(null);
  const [attempt, setAttempt] = useState(0);
  const [phase, setPhaseState] = useState<Phase>("loading");
  const phaseRef = useRef<Phase>("loading");
  const [hud, setHud] = useState<Hud>({ score: 0 });
  const [best, setBest] = useState(() => readBest(gameId));
  const [result, setResult] = useState<RunResult | null>(null);
  const [scale, setScale] = useState(1);

  const setPhase = useCallback((next: Phase) => {
    phaseRef.current = next;
    setPhaseState(next);
  }, []);

  // Load (or reload after an error) the game's code.
  useEffect(() => {
    let alive = true;
    setPhase("loading");
    loaderRef.current(gameId).then(
      (loaded) => {
        if (!alive) return;
        run.current = { game: loaded, state: loaded.create(Math.random) };
        setGame(loaded);
        setResult(null);
        setPhase("ready");
      },
      (error: unknown) => {
        if (!alive) return;
        console.warn("[arcade] game failed to load", gameId, error);
        setPhase("error");
      },
    );
    return () => { alive = false; };
  }, [gameId, attempt, setPhase]);

  const pause = useCallback(() => {
    releaseAll(tracker.current);
    if (phaseRef.current === "playing") setPhase("paused");
  }, [setPhase]);

  // The fixed-step loop. It runs for as long as the overlay is open (the
  // browser throttles animation frames of a truly hidden page by itself; the
  // desktop shell's visibility signal is not trusted to stop it).
  useEffect(() => {
    const clock = createRetroClock();
    let raf = 0;
    let last = -1;
    let opened = -1;
    let overAt = 0;

    const finish = (score: number, won: boolean, now: number) => {
      const previous = readBest(gameId);
      const newBest = saveBest(gameId, score);
      const kept = Math.max(previous, score);
      setBest(kept);
      setResult({ score, best: kept, newBest, won });
      overAt = now;
      clearEdges(tracker.current);
      setPhase("over");
    };

    const begin = (now: number) => {
      const current = run.current;
      if (!current) return;
      if (phaseRef.current === "over") {
        if (now - overAt < RESTART_GUARD_MS) return;
        current.state = current.game.create(Math.random);
        setResult(null);
      }
      resetClock(clock);
      clearEdges(tracker.current);
      setPhase("playing");
    };

    const frame = (now: number) => {
      raf = requestAnimationFrame(frame);
      if (opened < 0) opened = now;
      const elapsed = last < 0 ? 0 : (now - last) / 1000;
      last = now;
      const current = run.current;
      const phase = phaseRef.current;
      if (!current || phase === "loading" || phase === "error") return;
      const input = tracker.current;
      const pads = readPads();
      const startPressed = pads ? readGamepad(input, firstGamepad(pads)) : false;
      try {
        if (phase === "playing") {
          if (startPressed) {
            pause();
          } else {
            const steps = stepsDue(clock, elapsed);
            for (let i = 0; i < steps; i++) {
              current.game.step(current.state, takeInput(input, !!current.game.pointer), RETRO_STEP, Math.random);
              const status = current.game.status(current.state);
              if (status.over) {
                finish(Math.max(0, Math.floor(status.score)), !!status.won, now);
                break;
              }
            }
          }
        } else {
          const wants = startRequest.current || startPressed || hasStartGesture(input);
          startRequest.current = false;
          clearEdges(input);
          if (wants) begin(now);
        }
        const ctx = canvas.current?.getContext("2d") ?? null;
        if (ctx) {
          ctx.imageSmoothingEnabled = false;
          const shown = phaseRef.current;
          current.game.draw(ctx, current.state, {
            time: (now - opened) / 1000,
            reduced: reducedRef.current,
            idle: shown === "ready" || shown === "paused",
          });
        }
      } catch (error) {
        // A game that throws stops here with a retry, instead of throwing every frame.
        console.warn("[arcade] game crashed", gameId, error);
        run.current = null;
        setPhase("error");
      }
    };
    raf = requestAnimationFrame(frame);
    return () => cancelAnimationFrame(raf);
  }, [gameId, pause, setPhase]);

  const togglePause = useCallback(() => {
    if (phaseRef.current === "playing") pause();
    else if (phaseRef.current === "paused") startRequest.current = true;
  }, [pause]);

  // Keyboard: every key the cabinet uses is taken in the capture phase, so
  // neither the office's Escape nor its walking keys ever see it.
  useEffect(() => {
    const swallow = (event: KeyboardEvent) => { event.preventDefault(); event.stopPropagation(); };
    const down = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const code = event.code;
      if (event.key === "Escape" || code === "KeyE") {
        swallow(event);
        if (!event.repeat) closeRef.current();
      } else if (code === "KeyP") {
        swallow(event);
        if (!event.repeat) togglePause();
      } else if (code === "Enter" || code === "NumpadEnter") {
        swallow(event);
        if (!event.repeat) startRequest.current = true;
      } else if (pressKey(tracker.current, code, event.repeat)) {
        swallow(event);
      }
    };
    const up = (event: KeyboardEvent) => {
      const code = event.code;
      // Released keys always count, even with a modifier down, or they would stick.
      if (releaseKey(tracker.current, code) || code === "Enter" || code === "NumpadEnter" || code === "KeyP") swallow(event);
    };
    // Leaving the window (or hiding it) pauses and forgets held keys: their keyup never arrives.
    const visibility = () => { if (document.hidden) pause(); };
    window.addEventListener("keydown", down, true);
    window.addEventListener("keyup", up, true);
    window.addEventListener("blur", pause);
    document.addEventListener("visibilitychange", visibility);
    return () => {
      window.removeEventListener("keydown", down, true);
      window.removeEventListener("keyup", up, true);
      window.removeEventListener("blur", pause);
      document.removeEventListener("visibilitychange", visibility);
    };
  }, [pause, togglePause]);

  // The HUD follows the game at a relaxed pace, and only re-renders on change.
  useEffect(() => {
    const id = window.setInterval(() => {
      const current = run.current;
      if (!current || phaseRef.current === "loading" || phaseRef.current === "error") return;
      try {
        const status = current.game.status(current.state);
        const next: Hud = { score: Math.max(0, Math.floor(status.score)), lives: status.lives, level: status.level };
        setHud((h) => (sameHud(h, next) ? h : next));
      } catch {
        // The loop reports a crashing game; the HUD just keeps its last numbers.
      }
    }, HUD_POLL_MS);
    return () => window.clearInterval(id);
  }, []);

  // Closing mid-run still keeps a new best score.
  useEffect(() => () => {
    const current = run.current;
    if (!current) return;
    try { saveBest(gameId, current.game.status(current.state).score); } catch { /* a crashed game has no score worth keeping */ }
  }, [gameId]);

  // Focus moves into the dialog and back to where it was on close.
  useLayoutEffect(() => {
    const previous = document.activeElement;
    dialog.current?.focus({ preventScroll: true });
    return () => {
      if (previous instanceof HTMLElement && previous.isConnected) previous.focus({ preventScroll: true });
    };
  }, []);

  const width = game?.width ?? PLACEHOLDER.width;
  const height = game?.height ?? PLACEHOLDER.height;

  // The screen is scaled to the largest crisp factor that fits the stage.
  useEffect(() => {
    const el = stage.current;
    if (!el) return;
    const measure = () => {
      const next = screenScale(el.clientWidth, el.clientHeight, width, height, window.devicePixelRatio || 1);
      setScale((s) => (Math.abs(s - next) < 1e-6 ? s : next));
    };
    measure();
    if (typeof ResizeObserver !== "undefined") {
      const observer = new ResizeObserver(measure);
      observer.observe(el);
      return () => observer.disconnect();
    }
    window.addEventListener("resize", measure);
    return () => window.removeEventListener("resize", measure);
  }, [width, height]);

  const pointerAt = (event: ReactPointerEvent<HTMLCanvasElement>, down: boolean) => {
    const el = canvas.current;
    if (!el || !game?.pointer) return;
    const at = toLogical(event.clientX, event.clientY, el.getBoundingClientRect(), width, height);
    if (at) setPointer(tracker.current, at.x, at.y, down);
    else leavePointer(tracker.current);
  };

  const style = {
    "--cab-body": look.body, "--cab-trim": look.trim, "--cab-marquee": look.marquee,
    "--cab-marquee-text": look.marqueeText, "--cab-accent": look.accent,
  } as CSSProperties;
  const screenStyle = { width: `${width * scale}px`, height: `${height * scale}px`, "--retro-px": `${scale}px` } as CSSProperties;
  const key = info?.key ?? "";
  const shownBest = Math.max(best, hud.score);
  const requestStart = () => { startRequest.current = true; };

  return (
    <div className="retro-cab-backdrop" data-office-ui onPointerDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={dialog} className="retro-cab" style={style} role="dialog" data-state="open" aria-modal="true" aria-label={title}
        data-phase={phase} data-reduced={reduced ? "true" : "false"} tabIndex={-1}>
        <header className="retro-cab-marquee">
          <h2>{title}</h2>
          <button type="button" className="retro-cab-close" onClick={onClose} aria-label={t("society.office.close")}>×</button>
        </header>
        <div className="retro-cab-bezel">
          <div className="retro-cab-hud">
            <span>{t("society.arcade.score")} <b>{hud.score}</b></span>
            <span>{t("society.arcade.best")} <b>{shownBest}</b></span>
            {hud.lives !== undefined && <span>{t("society.arcade.lives")} <b>{hud.lives}</b></span>}
            {hud.level !== undefined && <span>{t("society.arcade.level")} <b>{hud.level}</b></span>}
          </div>
          <div ref={stage} className="retro-cab-stage">
            <div className="retro-cab-screen" style={screenStyle} data-scan={scale >= 2 ? "on" : "off"}>
              <canvas ref={canvas} width={width} height={height} data-pointer={game?.pointer ? "true" : "false"}
                onPointerDown={(event) => {
                  if (!game?.pointer) return;
                  event.currentTarget.setPointerCapture?.(event.pointerId);
                  pointerAt(event, true);
                }}
                onPointerMove={(event) => pointerAt(event, event.buttons !== 0)}
                onPointerUp={(event) => pointerAt(event, false)}
                onPointerCancel={() => leavePointer(tracker.current)}
                onPointerLeave={() => leavePointer(tracker.current)} />
              <div className="retro-cab-crt" aria-hidden />
              {phase === "loading" && (
                <div className="retro-cab-card" role="status"><span>{t("society.arcade.loading")}</span></div>
              )}
              {phase === "error" && (
                <div className="retro-cab-card" role="alert">
                  <span>{t("society.arcade.load_failed")}</span>
                  <button type="button" className="retro-cab-retry" onClick={() => setAttempt((n) => n + 1)}>{t("society.arcade.retry")}</button>
                </div>
              )}
              {phase === "ready" && (
                <button type="button" className="retro-cab-card" onClick={requestStart}>
                  <strong>{title}</strong>
                  {key && <span className="retro-cab-tagline">{t(`society.arcade.games.${key}.tagline`)}</span>}
                  {key && <span className="retro-cab-how">{t(`society.arcade.games.${key}.how`)}</span>}
                  <span className="retro-cab-blink">{t(coarse ? "society.arcade.tap_start" : "society.arcade.press_start")}</span>
                </button>
              )}
              {phase === "paused" && (
                <button type="button" className="retro-cab-card" onClick={requestStart}>
                  <strong>{t("society.arcade.paused")}</strong>
                  <span>{t(coarse ? "society.arcade.tap_resume" : "society.arcade.resume_hint")}</span>
                </button>
              )}
              {phase === "over" && result && (
                <button type="button" className="retro-cab-card" onClick={requestStart}>
                  <strong>{t(result.won ? "society.arcade.you_win" : "society.arcade.game_over")}</strong>
                  <span>{t("society.arcade.score")} <b>{result.score}</b> · {t("society.arcade.best")} <b>{result.best}</b></span>
                  {result.newBest && <span className="retro-cab-newbest">{t("society.arcade.new_best")}</span>}
                  <span className="retro-cab-blink">{t(coarse ? "society.arcade.tap_again" : "society.arcade.play_again")}</span>
                </button>
              )}
            </div>
          </div>
          {coarse ? (
            <TouchPad tracker={tracker.current} onPause={togglePause} pauseLabel={t("society.arcade.pause")}
              labels={{ up: t("society.arcade.pad_up"), down: t("society.arcade.pad_down"), left: t("society.arcade.pad_left"), right: t("society.arcade.pad_right") }} />
          ) : (
            <p className="retro-cab-keys">{t("society.arcade.keys_hint")}</p>
          )}
        </div>
      </div>
    </div>
  );
}

const DIRECTIONS = ["up", "down", "left", "right"] as const;

/**
 * The on-screen control panel for touch screens: an 8-way d-pad you can roll
 * your thumb across, A and B, and pause. Each control tracks its own pointer
 * ids, so two thumbs (move + fire) work at once.
 */
function TouchPad({ tracker, onPause, pauseLabel, labels }: {
  tracker: InputTracker;
  onPause: () => void;
  pauseLabel: string;
  labels: Record<(typeof DIRECTIONS)[number], string>;
}) {
  const dpadPointer = useRef<number | null>(null);

  const steer = (event: ReactPointerEvent<HTMLDivElement>) => {
    const rect = event.currentTarget.getBoundingClientRect();
    const dirs = dpadDirections(event.clientX - (rect.left + rect.width / 2), event.clientY - (rect.top + rect.height / 2), rect.width / 2);
    for (const d of DIRECTIONS) setTouch(tracker, d, dirs[d]);
  };
  const releaseDpad = (event: ReactPointerEvent<HTMLDivElement>) => {
    if (event.pointerId !== dpadPointer.current) return;
    dpadPointer.current = null;
    for (const d of DIRECTIONS) setTouch(tracker, d, false);
  };

  return (
    <div className="retro-cab-touch">
      <div className="retro-cab-dpad" role="group"
        onPointerDown={(event) => {
          if (dpadPointer.current !== null) return;
          event.preventDefault();
          dpadPointer.current = event.pointerId;
          event.currentTarget.setPointerCapture?.(event.pointerId);
          steer(event);
        }}
        onPointerMove={(event) => { if (event.pointerId === dpadPointer.current) steer(event); }}
        onPointerUp={releaseDpad}
        onPointerCancel={releaseDpad}
        onLostPointerCapture={releaseDpad}>
        {DIRECTIONS.map((d) => <span key={d} className="retro-cab-dpad-arm" data-dir={d} role="img" aria-label={labels[d]} />)}
      </div>
      <button type="button" className="retro-cab-pause" aria-label={pauseLabel} onClick={onPause}>❚❚</button>
      <div className="retro-cab-buttons">
        <TouchButton tracker={tracker} button="b" />
        <TouchButton tracker={tracker} button="a" />
      </div>
    </div>
  );
}

function TouchButton({ tracker, button }: { tracker: InputTracker; button: RetroButton }) {
  const held = useRef(new Set<number>());
  const release = (event: ReactPointerEvent<HTMLButtonElement>) => {
    if (!held.current.delete(event.pointerId)) return;
    if (held.current.size === 0) setTouch(tracker, button, false);
  };
  // Labelled with its letter: A and B are the cabinet's own button names in every language.
  return (
    <button type="button" className="retro-cab-btn" data-button={button} aria-label={button.toUpperCase()}
      onPointerDown={(event) => {
        event.preventDefault();
        event.currentTarget.setPointerCapture?.(event.pointerId);
        held.current.add(event.pointerId);
        setTouch(tracker, button, true);
      }}
      onPointerUp={release}
      onPointerCancel={release}
      onLostPointerCapture={release}
      onContextMenu={(event) => event.preventDefault()}>
      {button.toUpperCase()}
    </button>
  );
}
