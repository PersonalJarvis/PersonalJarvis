import { useEffect, useRef, useState } from "react";

import { cn } from "@/lib/utils";
import { readVoiceInputLevel } from "@/lib/voiceInputLevel";
import { voiceOutputLevelRef } from "@/lib/voiceOutputLevel";
import { releaseWebglContext } from "@/hooks/useWebglSurface";
import { createGlowRenderer } from "@/components/home/voiceGlowRenderer";
import { createVoiceFollower } from "@/components/home/voiceFollower";

/**
 * The light behind voice mode's composer — a voice glow in this theme's one
 * signal hue.
 *
 * An aurora rises from the bottom edge: a soft haze with a flowing crest,
 * curtains of light drifting up through it, and a bright core along the
 * edge (components/home/voiceGlowRenderer, one WebGL shader). At rest (no
 * call) it is a faint, motionless wash. While a call is open it flows; when
 * someone speaks — you (the microphone level) or the assistant (the
 * playback level), whichever is louder — the light stands taller, flows
 * faster and its core flares with each syllable
 * (components/home/voiceFollower turns the raw level into those smooth
 * signals). While the assistant thinks, nobody speaks — so the light does
 * not go dark: it breathes, two comets glide along the bottom edge
 * trailing sparks, and each time they meet a pillar of light shoots up. Levels are read on animation frames — no React state, no
 * re-render per frame — and the loop stops once the light has settled after
 * a call. Reduced motion keeps the light and drops the movement.
 *
 * Where WebGL is unavailable, or the context is lost, the light falls back
 * to three CSS pools that move with the same signals.
 *
 * Purely decorative: no pointer events, hidden from screen readers.
 */
export function VoiceGlow({ active, thinking = false }: { active: boolean; thinking?: boolean }) {
  const [webgl, setWebgl] = useState(webglAvailable);
  return (
    <div
      aria-hidden
      data-testid="voice-glow"
      data-renderer={webgl ? "webgl" : "css"}
      className="pointer-events-none absolute inset-x-0 bottom-0 h-[38vh] overflow-hidden"
    >
      {webgl ? (
        <ShaderGlow active={active} thinking={thinking} onUnavailable={() => setWebgl(false)} />
      ) : (
        <CssGlow active={active} thinking={thinking} />
      )}
    </div>
  );
}

function webglAvailable(): boolean {
  return typeof window !== "undefined" && typeof window.WebGLRenderingContext !== "undefined";
}

function prefersReducedMotion(): boolean {
  return (
    typeof window.matchMedia === "function" &&
    window.matchMedia("(prefers-reduced-motion: reduce)").matches
  );
}

/** The loudest voice right now: the assistant's playback or your microphone. */
function currentVoiceLevel(now: number): number {
  return Math.max(voiceOutputLevelRef.current ?? 0, readVoiceInputLevel(now));
}

/** How long the light takes to come up when a call opens and to settle after. */
const POWER_TAU_S = 0.5;
/** How long the thinking beams take to fade in and out. */
const THINK_TAU_S = 0.35;
/** Radians per second of the beams' glide: one there-and-back every ~4.5 s. */
const SWEEP_SPEED = 1.4;
/** Radians per second of the light's breathing while it thinks (~3 s a breath). */
const BREATH_SPEED = 2.1;

/** How high the light stands while thinking: a calm, breathing middle height. */
function thinkingLevel(think: number, breath: number): number {
  return think * (0.24 + 0.08 * Math.sin(breath));
}

/**
 * Contexts handed back one task after an unmount, so React's development
 * effect replay (same canvas, same context) can reclaim them first — the
 * same dance as hooks/useWebglSurface.
 */
const pendingReleases = new Map<HTMLCanvasElement, number>();

function ShaderGlow({
  active,
  thinking,
  onUnavailable,
}: {
  active: boolean;
  thinking: boolean;
  onUnavailable: () => void;
}) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null);
  const activeRef = useRef(active);
  const thinkingRef = useRef(thinking);
  const wakeRef = useRef<() => void>(() => {});
  const unavailableRef = useRef(onUnavailable);

  useEffect(() => {
    unavailableRef.current = onUnavailable;
  }, [onUnavailable]);

  useEffect(() => {
    activeRef.current = active;
    thinkingRef.current = thinking;
    wakeRef.current();
  }, [active, thinking]);

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const pending = pendingReleases.get(canvas);
    if (pending !== undefined) {
      window.clearTimeout(pending);
      pendingReleases.delete(canvas);
    }
    const renderer = createGlowRenderer(canvas);
    if (!renderer) {
      unavailableRef.current();
      return;
    }

    const reduced = prefersReducedMotion();
    const follower = createVoiceFollower();
    let signals = { level: 0, pulse: 0 };
    // Flow time is ACCUMULATED (faster while someone speaks), never
    // computed as clock * speed(level): that form turned every change in
    // loudness into a jump of the whole pattern.
    let time = 12;
    let power = 0;
    let think = 0;
    let sweepPhase = 0;
    let breathPhase = 0;
    let color: [number, number, number] = [61, 139, 255];
    let light = false;
    let sinceTheme = 0;
    let last = -1;
    let frame = 0;
    let running = false;

    const readTheme = () => {
      const channels = getComputedStyle(canvas)
        .getPropertyValue("--accent-rgb")
        .trim()
        .split(/[\s,]+/)
        .map(Number);
      if (channels.length >= 3 && channels.slice(0, 3).every(Number.isFinite)) {
        color = [channels[0], channels[1], channels[2]];
      }
      light = canvas.closest(".dark") === null;
    };
    const draw = () =>
      renderer.render({
        time,
        level: Math.max(signals.level, thinkingLevel(think, breathPhase)),
        pulse: signals.pulse,
        think,
        // The beams start together in the middle and part from there.
        sweep: Math.sin(sweepPhase),
        sweepVel: Math.cos(sweepPhase),
        power,
        light,
        color,
      });

    const tick = (now: number) => {
      const dt = last < 0 ? 0 : Math.min(0.1, (now - last) / 1000);
      last = now;
      const live = activeRef.current;
      signals = follower.step(dt, live ? currentVoiceLevel(now) : 0);
      power += ((live ? 1 : 0) - power) * (1 - Math.exp(-dt / POWER_TAU_S));
      const thinkingNow = live && thinkingRef.current;
      think += ((thinkingNow ? 1 : 0) - think) * (1 - Math.exp(-dt / THINK_TAU_S));
      if (think < 0.002 && !thinkingNow) {
        think = 0;
        sweepPhase = 0;
      } else {
        sweepPhase += dt * SWEEP_SPEED;
        breathPhase += dt * BREATH_SPEED;
      }
      time += dt * (0.55 + Math.max(signals.level, think * 0.3) * 1.4);
      sinceTheme += dt;
      if (sinceTheme > 0.5) {
        sinceTheme = 0;
        readTheme();
      }
      if (!live && power < 0.002 && think === 0 && signals.level < 0.002 && signals.pulse < 0.002) {
        // Settled after a call: one last resting frame, then no loop at all.
        power = 0;
        signals = { level: 0, pulse: 0 };
        draw();
        running = false;
        return;
      }
      draw();
      frame = requestAnimationFrame(tick);
    };

    const wake = () => {
      if (reduced || typeof requestAnimationFrame === "undefined") {
        power = activeRef.current ? 1 : 0;
        // Reduced motion: the beams stand still, together in the middle.
        think = activeRef.current && thinkingRef.current ? 1 : 0;
        readTheme();
        draw();
        return;
      }
      if (running) return;
      running = true;
      last = -1;
      frame = requestAnimationFrame(tick);
    };
    wakeRef.current = wake;

    const fit = () => {
      const box = canvas.getBoundingClientRect();
      if (renderer.resize(box.width, box.height) && !running) draw();
    };
    readTheme();
    fit();
    draw();
    wake();

    const resizeObserver = typeof ResizeObserver !== "undefined" ? new ResizeObserver(fit) : null;
    resizeObserver?.observe(canvas);
    // A theme switch while the light rests must repaint the resting frame.
    const themeObserver =
      typeof MutationObserver !== "undefined"
        ? new MutationObserver(() => {
            readTheme();
            if (!running) draw();
          })
        : null;
    themeObserver?.observe(document.documentElement, {
      attributes: true,
      attributeFilter: ["class", "style", "data-theme"],
    });

    // A lost context (GPU reset, the page out of contexts) would otherwise
    // leave Chromium's broken-canvas placeholder on screen. Claim the event
    // and hand over to the CSS light; a decorative glow is not worth a
    // rebuild dance.
    const onLost = (event: Event) => {
      event.preventDefault();
      cancelAnimationFrame(frame);
      running = false;
      unavailableRef.current();
    };
    canvas.addEventListener("webglcontextlost", onLost);

    return () => {
      wakeRef.current = () => {};
      cancelAnimationFrame(frame);
      running = false;
      resizeObserver?.disconnect();
      themeObserver?.disconnect();
      canvas.removeEventListener("webglcontextlost", onLost);
      renderer.dispose();
      pendingReleases.set(
        canvas,
        window.setTimeout(() => {
          pendingReleases.delete(canvas);
          releaseWebglContext(canvas);
        }, 0),
      );
    };
  }, []);

  return <canvas ref={canvasRef} className="absolute inset-0 h-full w-full" />;
}

/** The fallback: three pools of accent light that move with the same signals. */
function CssGlow({ active, thinking }: { active: boolean; thinking: boolean }) {
  const blobRefs = useRef<(HTMLDivElement | null)[]>([]);

  useEffect(() => {
    const blobs = blobRefs.current.filter((b): b is HTMLDivElement => b !== null);
    if (!active || prefersReducedMotion() || typeof requestAnimationFrame === "undefined") {
      blobs.forEach((el) => {
        el.style.opacity = active ? "0.7" : "0.3";
        el.style.transform = "translateX(-50%)";
      });
      return;
    }
    const follower = createVoiceFollower();
    let last = -1;
    let frame = 0;
    // Each pool's sway position is accumulated, for the same reason as the
    // shader's flow time: the voice changes how fast it travels, never where.
    const sway = BLOBS.map((b) => b.phase);
    const breath = BLOBS.map((b) => b.phase * 2);
    let thinkBreath = 0;
    const tick = (now: number) => {
      const dt = last < 0 ? 0 : Math.min(0.1, (now - last) / 1000);
      last = now;
      thinkBreath += dt * BREATH_SPEED;
      const voice = Math.max(
        follower.step(dt, currentVoiceLevel(now)).level,
        thinkingLevel(thinking ? 1 : 0, thinkBreath),
      );
      BLOBS.forEach((b, i) => {
        const el = blobs[i];
        if (!el) return;
        sway[i] += dt * b.speed * (1 + voice * 1.2);
        breath[i] += dt * b.speed * 1.3;
        const x = Math.sin(sway[i]) * (b.drift + voice * 5);
        const glow = 0.5 + 0.5 * Math.sin(breath[i]);
        el.style.opacity = Math.min(1, 0.5 + voice * 0.45 + glow * 0.08).toFixed(3);
        el.style.transform =
          `translateX(calc(-50% + ${x.toFixed(2)}%)) ` +
          `scale(${(1 + voice * 0.1).toFixed(3)}, ${(1 + voice * b.lift).toFixed(3)})`;
      });
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [active, thinking]);

  return (
    <>
      {BLOBS.map((b, i) => (
        <div
          key={i}
          ref={(el) => {
            blobRefs.current[i] = el;
          }}
          // The fade only eases the rest <-> call hand-over (light AND shape, so
          // a hang-up mid-word settles instead of snapping back). While a call runs
          // the frame loop writes opacity every frame, and a transition there
          // restarted a 500 ms fade per frame — the light lagged the voice and
          // the browser re-planned a transition 60 times a second.
          className={cn(
            "absolute bottom-0 origin-bottom will-change-[transform,opacity]",
            !active && "transition-[opacity,transform] duration-700 ease-out",
          )}
          style={{
            left: `${b.x}%`,
            width: b.width,
            height: b.height,
            transform: "translateX(-50%)",
            opacity: 0.3,
            background: glowGradient(b.alpha),
          }}
        />
      ))}
    </>
  );
}

/** Three pools of light: one wide and calm in the middle, two smaller and livelier. */
const BLOBS = [
  { x: 50, width: "min(760px, 90%)", height: "100%", alpha: 0.26, speed: 0.35, phase: 0, drift: 2, lift: 0.3 },
  { x: 40, width: "min(420px, 55%)", height: "80%", alpha: 0.22, speed: 0.6, phase: 2.1, drift: 5, lift: 0.5 },
  { x: 60, width: "min(420px, 55%)", height: "75%", alpha: 0.2, speed: 0.75, phase: 4.2, drift: 5, lift: 0.55 },
] as const;

/**
 * One pool's light: a bell-shaped falloff from the bottom edge. With only a
 * bright core and a hard stop the ellipse's rim showed as an arc and the
 * pools read as shapes; many gently decaying stops blend into a haze.
 */
function glowGradient(alpha: number): string {
  const stops = [
    [1, 0],
    [0.86, 12],
    [0.66, 24],
    [0.45, 36],
    [0.27, 48],
    [0.14, 59],
    [0.06, 69],
    [0.02, 78],
    [0, 88],
  ]
    .map(([k, at]) => `rgb(var(--accent-rgb) / ${(alpha * k).toFixed(3)}) ${at}%`)
    .join(", ");
  return `radial-gradient(ellipse 50% 70% at 50% 100%, ${stops})`;
}
