/**
 * Effect kit for the README film — the "digital clone of the website" cut.
 *
 * Every effect here is deterministic (frame-driven, seeded randomness) so the
 * render is reproducible. Colors come from theme.ts; display/mono faces from
 * fonts.ts. Motion follows the site's grammar: matte black, one signal-yellow
 * accent used sparingly, spring overshoot on emphasis, restraint over noise.
 */
import React from "react";
import {
  AbsoluteFill,
  Easing,
  Img,
  interpolate,
  random,
  spring,
  staticFile,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS, FONT } from "../theme";
import { FONT_DISPLAY, FONT_MONO } from "./fonts";
import type { CursorKey } from "../components/Cursor";

const Y = COLORS.primary;
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

/* ------------------------------------------------------------------ */
/* Global atmosphere layers (rendered once, over everything)           */
/* ------------------------------------------------------------------ */

/** Very subtle CRT scanlines + slow flicker — the "cyber butler" vibe. */
export const Scanlines: React.FC = () => {
  const frame = useCurrentFrame();
  const flicker = 0.03 + 0.012 * Math.sin(frame / 3.5);
  return (
    <AbsoluteFill
      style={{
        pointerEvents: "none",
        opacity: flicker,
        backgroundImage:
          "repeating-linear-gradient(0deg, rgba(255,255,255,0.9) 0px, rgba(255,255,255,0.9) 1px, transparent 1px, transparent 3px)",
        mixBlendMode: "overlay",
      }}
    />
  );
};

/** Thin signal-yellow progress hairline pinned to the very top of the frame. */
export const ProgressHairline: React.FC = () => {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  const p = interpolate(frame, [0, durationInFrames - 1], [0, 1], clamp);
  return (
    <div
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        height: 3,
        width: `${p * 100}%`,
        background: Y,
        boxShadow: `0 0 12px ${COLORS.primaryGlow}`,
        zIndex: 50,
      }}
    />
  );
};

/** Full-frame vignette that OPENS (dark → clear) as Jarvis "wakes up". */
export const VignetteOpen: React.FC<{ at?: number; dur?: number }> = ({ at = 0, dur = 40 }) => {
  const frame = useCurrentFrame();
  const k = interpolate(frame, [at, at + dur], [1, 0], clamp);
  return (
    <AbsoluteFill
      style={{
        pointerEvents: "none",
        background: `radial-gradient(ellipse 70% 65% at 50% 50%, transparent ${40 + 25 * (1 - k)}%, rgba(0,0,0,${0.7 * k + 0.25}) 100%)`,
      }}
    />
  );
};

/* ------------------------------------------------------------------ */
/* The ghost butler — Gigi                                             */
/* ------------------------------------------------------------------ */

/** Amber pixel-cubes that fly in and converge, then the ghost resolves. */
const AssembleMotes: React.FC<{ size: number; progress: number }> = ({ size, progress }) => {
  const n = 26;
  return (
    <>
      {new Array(n).fill(0).map((_, i) => {
        const a = random(`mote-${i}`) * Math.PI * 2;
        const dist = (0.6 + random(`d-${i}`) * 0.9) * size;
        const sq = 4 + random(`s-${i}`) * 6;
        const spread = 1 - progress;
        const x = Math.cos(a) * dist * spread;
        const y = Math.sin(a) * dist * spread;
        const op = interpolate(progress, [0, 0.35, 0.85, 1], [0, 0.9, 0.9, 0], clamp);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: "50%",
              top: "50%",
              width: sq,
              height: sq,
              marginLeft: -sq / 2,
              marginTop: -sq / 2,
              transform: `translate(${x}px, ${y}px)`,
              background: Y,
              opacity: op,
              borderRadius: 1,
            }}
          />
        );
      })}
    </>
  );
};

/** Concentric signal rings rippling outward (listening/awake). */
export const SignalRings: React.FC<{ size: number; count?: number; opacity?: number }> = ({
  size,
  count = 3,
  opacity = 0.28,
}) => {
  const frame = useCurrentFrame();
  return (
    <>
      {new Array(count).fill(0).map((_, i) => {
        const local = (frame / 34 + i * (1 / count)) % 1;
        const scale = interpolate(local, [0, 1], [0.7, 2.1]);
        const op = interpolate(local, [0, 0.2, 1], [0, opacity, 0]);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: "50%",
              top: "50%",
              width: size,
              height: size,
              marginLeft: -size / 2,
              marginTop: -size / 2,
              borderRadius: "50%",
              border: `2px solid ${Y}`,
              transform: `scale(${scale})`,
              opacity: op,
            }}
          />
        );
      })}
    </>
  );
};

/**
 * The Gigi ghost mark. `assembleAt` triggers a pixel-assemble intro; after that
 * it idle-floats, breathes a glow, and emits rippling signal rings.
 */
export const GhostMark: React.FC<{
  size?: number;
  assembleAt?: number | null;
  rings?: boolean;
  floaty?: boolean;
}> = ({ size = 200, assembleAt = null, rings = true, floaty = true }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();

  const assembling = assembleAt !== null;
  const prog = assembling
    ? spring({ frame: frame - (assembleAt as number), fps, config: { damping: 200, mass: 1.1 } })
    : 1;

  const bob = floaty ? Math.sin(frame / 26) * 8 : 0;
  const breathe = (Math.sin(frame / 20) + 1) / 2;
  const glow = 0.4 + breathe * 0.5;
  const entered = interpolate(prog, [0, 1], [0.6, 1], clamp);

  return (
    <div style={{ position: "relative", width: size, height: size, transform: `translateY(${bob}px)` }}>
      {rings && <SignalRings size={size} />}
      {/* halo */}
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          width: size * 1.7,
          height: size * 1.7,
          marginLeft: -(size * 1.7) / 2,
          marginTop: -(size * 1.7) / 2,
          borderRadius: "50%",
          background: `radial-gradient(circle, rgba(255,214,10,${0.32 * glow}), rgba(255,214,10,0) 62%)`,
        }}
      />
      {assembling && prog < 1 && <AssembleMotes size={size} progress={prog} />}
      <Img
        src={staticFile("jarvis-gigi.png")}
        style={{
          position: "relative",
          width: size,
          height: size,
          objectFit: "contain",
          opacity: interpolate(prog, [0.3, 0.85], [0, 1], clamp),
          transform: `scale(${entered})`,
          filter: `drop-shadow(0 0 ${14 + glow * 26}px ${COLORS.primaryGlow})`,
        }}
      />
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Kinetic type                                                        */
/* ------------------------------------------------------------------ */

const SCRAMBLE = "ABCDEFGHJKLMNPQRSTUVWXYZ0123456789#%&/<>*";

/** Text whose letters "decode" from random glyphs into the word, left→right. */
export const TextScramble: React.FC<{
  text: string;
  start?: number;
  perChar?: number;
  lock?: number;
  size?: number;
  color?: string;
  weight?: number;
  mono?: boolean;
  letterSpacing?: number;
}> = ({ text, start = 0, perChar = 1.4, lock = 8, size = 42, color = COLORS.text, weight = 700, mono = false, letterSpacing = 0 }) => {
  const frame = useCurrentFrame();
  const chars = text.split("");
  return (
    <span
      style={{
        fontFamily: mono ? FONT_MONO : FONT_DISPLAY,
        fontSize: size,
        fontWeight: weight,
        color,
        letterSpacing,
        whiteSpace: "pre",
      }}
    >
      {chars.map((c, i) => {
        if (c === " ") return " ";
        const revealAt = start + i * perChar;
        const done = frame >= revealAt + lock;
        const showing = frame >= start;
        if (!showing) return <span key={i} style={{ opacity: 0 }}>{c}</span>;
        if (done) return <span key={i}>{c}</span>;
        const g = SCRAMBLE[Math.floor(random(`sc-${i}-${Math.floor(frame / 2)}`) * SCRAMBLE.length)];
        return (
          <span key={i} style={{ color: Y, opacity: 0.85 }}>
            {g}
          </span>
        );
      })}
    </span>
  );
};

/**
 * TikTok-style word captions: words pop in one by one, the word being spoken is
 * highlighted amber. Timed evenly across [start, start+dur].
 */
export const WordCaptions: React.FC<{
  text: string;
  start: number;
  dur: number;
  size?: number;
  align?: "center" | "left";
  maxWidth?: number;
}> = ({ text, start, dur, size = 34, align = "center", maxWidth = 900 }) => {
  const frame = useCurrentFrame();
  const words = text.split(" ");
  const per = dur / words.length;
  return (
    <div
      style={{
        display: "flex",
        flexWrap: "wrap",
        gap: `6px 12px`,
        justifyContent: align === "center" ? "center" : "flex-start",
        maxWidth,
        fontFamily: FONT_DISPLAY,
        fontSize: size,
        fontWeight: 600,
        lineHeight: 1.3,
      }}
    >
      {words.map((w, i) => {
        const wStart = start + i * per;
        const shown = frame >= wStart;
        const active = frame >= wStart && frame < wStart + per * 1.25;
        const pop = interpolate(frame - wStart, [0, 6], [0.6, 1], clamp);
        if (!shown) return <span key={i} style={{ opacity: 0 }}>{w}</span>;
        return (
          <span
            key={i}
            style={{
              color: active ? Y : COLORS.text,
              opacity: active ? 1 : 0.9,
              transform: `scale(${active ? 1.06 : pop})`,
              transformOrigin: "center bottom",
              transition: "none",
              textShadow: active ? `0 0 18px ${COLORS.primaryGlow}` : "none",
            }}
          >
            {w}
          </span>
        );
      })}
    </div>
  );
};

/** A word that "slams" in with an RGB split + jitter, then settles. */
export const GlitchText: React.FC<{
  children: string;
  at?: number;
  size?: number;
  weight?: number;
}> = ({ children, at = 0, size = 64, weight = 700 }) => {
  const frame = useCurrentFrame();
  const t = frame - at;
  const glitch = t >= 0 && t < 8;
  const j = glitch ? (random(`g-${Math.floor(frame)}`) - 0.5) * 6 : 0;
  const split = glitch ? interpolate(t, [0, 8], [6, 0], clamp) : 0;
  const enter = interpolate(t, [0, 6], [0, 1], clamp);
  const base: React.CSSProperties = {
    fontFamily: FONT_DISPLAY,
    fontSize: size,
    fontWeight: weight,
    position: "relative",
    display: "inline-block",
    transform: `translateX(${j}px) scale(${interpolate(t, [0, 5], [1.08, 1], clamp)})`,
    opacity: enter,
  };
  return (
    <span style={base}>
      {split > 0 && (
        <>
          <span style={{ position: "absolute", left: -split, top: 0, color: "#FF3B3B", opacity: 0.7, mixBlendMode: "screen" }}>
            {children}
          </span>
          <span style={{ position: "absolute", left: split, top: 0, color: "#2ED1FF", opacity: 0.7, mixBlendMode: "screen" }}>
            {children}
          </span>
        </>
      )}
      <span style={{ position: "relative", color: Y, textShadow: `0 0 22px ${COLORS.primaryGlow}` }}>{children}</span>
    </span>
  );
};

/* ------------------------------------------------------------------ */
/* Terminal / install                                                  */
/* ------------------------------------------------------------------ */

/** A monospace command line, typed out char-by-char with a blinking caret. */
export const TerminalBlock: React.FC<{
  command: string;
  comment?: string;
  start?: number;
  cps?: number;
  width?: number;
  fontSize?: number;
}> = ({ command, comment = "# Works everywhere. Bring your own keys.", start = 0, cps = 34, width = 900, fontSize = 22 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const typed = Math.max(0, Math.floor(((frame - start) / fps) * cps));
  const shown = command.slice(0, typed);
  const done = typed >= command.length;
  const caretOn = Math.floor(frame / 8) % 2 === 0;
  const copied = done && frame > start + fps * 2.0;

  return (
    <div
      style={{
        width,
        borderRadius: 14,
        background: COLORS.bgElevated,
        border: `1px solid ${COLORS.borderStrong}`,
        boxShadow: `0 30px 80px rgba(0,0,0,0.55), 0 0 40px ${COLORS.primaryGlow}`,
        overflow: "hidden",
        fontFamily: FONT_MONO,
      }}
    >
      <div
        style={{
          height: 40,
          display: "flex",
          alignItems: "center",
          gap: 8,
          padding: "0 16px",
          background: COLORS.bgCard,
          borderBottom: `1px solid ${COLORS.border}`,
        }}
      >
        {["#FF5F57", "#FEBC2E", "#28C840"].map((c) => (
          <div key={c} style={{ width: 11, height: 11, borderRadius: "50%", background: c }} />
        ))}
        <div style={{ marginLeft: 10, color: COLORS.textFaint, fontSize: 14 }}>bash</div>
        <div
          style={{
            marginLeft: "auto",
            fontSize: 13,
            fontWeight: 700,
            color: copied ? COLORS.bg : Y,
            background: copied ? Y : "transparent",
            border: `1px solid ${Y}`,
            borderRadius: 6,
            padding: "3px 10px",
          }}
        >
          {copied ? "Copied!" : "Copy"}
        </div>
      </div>
      <div style={{ padding: "22px 24px", fontSize, lineHeight: 1.7 }}>
        <div style={{ color: COLORS.textFaint }}>{comment}</div>
        <div style={{ color: COLORS.text, whiteSpace: "nowrap" }}>
          <span style={{ color: Y }}>$ </span>
          {shown}
          <span style={{ opacity: caretOn && !copied ? 1 : 0, color: Y }}>▋</span>
        </div>
      </div>
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Router node-graph                                                   */
/* ------------------------------------------------------------------ */

/** Router-Brain visibly delegating to harnesses — pulses travel the wires. */
export const NodeGraph: React.FC<{
  at?: number;
  width?: number;
  height?: number;
  nodes?: string[];
}> = ({ at = 0, width = 460, height = 300, nodes = ["Computer-Use", "Worker", "Search", "Voice"] }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const cx = 70;
  const cy = height / 2;
  const targets = nodes.map((_, i) => ({
    x: width - 150,
    y: 40 + (i * (height - 80)) / (nodes.length - 1),
  }));
  const appear = spring({ frame: frame - at, fps, config: { damping: 200 } });

  return (
    <svg width={width} height={height} style={{ overflow: "visible", opacity: appear }}>
      {targets.map((t, i) => {
        const midX = (cx + t.x) / 2;
        const path = `M ${cx} ${cy} C ${midX} ${cy}, ${midX} ${t.y}, ${t.x} ${t.y}`;
        const pulse = (frame / fps - at / fps - i * 0.18) % 1.1;
        const showPulse = pulse >= 0 && pulse <= 1;
        const px = interpolate(Math.min(Math.max(pulse, 0), 1), [0, 1], [cx, t.x]);
        const py = interpolate(Math.min(Math.max(pulse, 0), 1), [0, 1], [cy, t.y]);
        return (
          <g key={i}>
            <path d={path} fill="none" stroke="rgba(255,214,10,0.28)" strokeWidth={2} />
            {showPulse && <circle cx={px} cy={py} r={4} fill={Y} style={{ filter: "drop-shadow(0 0 6px #FFD60A)" }} />}
            <g transform={`translate(${t.x}, ${t.y})`}>
              <rect x={0} y={-16} width={130} height={32} rx={16} fill={COLORS.bgCard} stroke="rgba(255,214,10,0.35)" />
              <text x={65} y={5} textAnchor="middle" fontFamily={FONT_MONO} fontSize={13} fill={COLORS.text}>
                {nodes[i]}
              </text>
            </g>
          </g>
        );
      })}
      {/* router core */}
      <circle cx={cx} cy={cy} r={30} fill={COLORS.bgElevated} stroke={Y} strokeWidth={2} />
      <circle cx={cx} cy={cy} r={30 + ((frame / 2) % 20)} fill="none" stroke="rgba(255,214,10,0.25)" strokeWidth={1} opacity={interpolate((frame / 2) % 20, [0, 20], [0.5, 0])} />
      <text x={cx} y={cy + 5} textAnchor="middle" fontFamily={FONT_MONO} fontSize={12} fontWeight={700} fill={Y}>
        ROUTER
      </text>
    </svg>
  );
};

/* ------------------------------------------------------------------ */
/* HUD, counters, transforms, cursor                                   */
/* ------------------------------------------------------------------ */

/** Targeting-system corner brackets that draw around a region. */
export const HudFrame: React.FC<{
  width: number;
  height: number;
  at?: number;
  label?: string;
  color?: string;
}> = ({ width, height, at = 0, label, color = Y }) => {
  const frame = useCurrentFrame();
  const g = interpolate(frame - at, [0, 12], [0, 1], clamp);
  const len = 26;
  const corner = (x: number, y: number, sx: number, sy: number, key: string) => (
    <g key={key} opacity={g}>
      <line x1={x} y1={y} x2={x + sx * len} y2={y} stroke={color} strokeWidth={2} />
      <line x1={x} y1={y} x2={x} y2={y + sy * len} stroke={color} strokeWidth={2} />
    </g>
  );
  return (
    <svg width={width} height={height} style={{ position: "absolute", left: 0, top: 0, overflow: "visible", pointerEvents: "none" }}>
      {corner(2, 2, 1, 1, "tl")}
      {corner(width - 2, 2, -1, 1, "tr")}
      {corner(2, height - 2, 1, -1, "bl")}
      {corner(width - 2, height - 2, -1, -1, "br")}
      {label && (
        <text x={10} y={-10} fontFamily={FONT_MONO} fontSize={12} fill={color} opacity={g} letterSpacing={2}>
          {label}
        </text>
      )}
    </svg>
  );
};

/** A number that rattles up from 0 to `to` over a window. */
export const Counter: React.FC<{
  to: number;
  start?: number;
  dur?: number;
  suffix?: string;
  decimals?: number;
  size?: number;
  color?: string;
}> = ({ to, start = 0, dur = 30, suffix = "", decimals = 0, size = 40, color = Y }) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame, [start, start + dur], [0, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  const v = t * to;
  return (
    <span
      style={{
        fontFamily: FONT_MONO,
        fontSize: size,
        fontWeight: 700,
        color,
        fontVariantNumeric: "tabular-nums",
      }}
    >
      {v.toFixed(decimals)}
      {suffix}
    </span>
  );
};

/** A quick spring "punch" scale on the wrapped content. */
export const ZoomPunch: React.FC<{
  at?: number;
  peak?: number;
  origin?: string;
  children: React.ReactNode;
}> = ({ at = 0, peak = 1.1, origin = "50% 50%", children }) => {
  const frame = useCurrentFrame();
  const t = frame - at;
  let scale = 1;
  if (t >= 0) {
    scale = t < 5 ? interpolate(t, [0, 5], [1, peak], clamp) : interpolate(t, [5, 18], [peak, 1], { ...clamp, easing: Easing.out(Easing.cubic) });
  }
  return <div style={{ transform: `scale(${scale})`, transformOrigin: origin }}>{children}</div>;
};

/** Slow directional scale+pan on an image region (Ken Burns). */
export const KenBurns: React.FC<{
  children: React.ReactNode;
  fromScale?: number;
  toScale?: number;
  dx?: number;
  dy?: number;
  dur?: number;
}> = ({ children, fromScale = 1, toScale = 1.12, dx = -20, dy = -12, dur = 120 }) => {
  const frame = useCurrentFrame();
  const t = interpolate(frame, [0, dur], [0, 1], { ...clamp, easing: Easing.inOut(Easing.quad) });
  const s = interpolate(t, [0, 1], [fromScale, toScale]);
  return (
    <div style={{ width: "100%", height: "100%", transform: `scale(${s}) translate(${dx * t}px, ${dy * t}px)`, transformOrigin: "50% 45%" }}>
      {children}
    </div>
  );
};

/** Fading particle trail behind a moving cursor (same keyframes as <Cursor>). */
export const CursorTrail: React.FC<{ keys: CursorKey[]; count?: number }> = ({ keys, count = 9 }) => {
  const frame = useCurrentFrame();
  const frames = keys.map((k) => k.frame);
  const xs = keys.map((k) => k.x);
  const ys = keys.map((k) => k.y);
  return (
    <>
      {new Array(count).fill(0).map((_, i) => {
        const f = frame - (i + 1) * 1.5;
        const x = interpolate(f, frames, xs, clamp);
        const y = interpolate(f, frames, ys, clamp);
        const op = interpolate(i, [0, count], [0.5, 0]) ;
        const sz = interpolate(i, [0, count], [8, 2]);
        return (
          <div
            key={i}
            style={{
              position: "absolute",
              left: x,
              top: y,
              width: sz,
              height: sz,
              marginLeft: -sz / 2,
              marginTop: -sz / 2,
              borderRadius: "50%",
              background: Y,
              opacity: op,
              filter: "blur(0.5px)",
            }}
          />
        );
      })}
    </>
  );
};

/** A single anamorphic lens flare — one bright horizontal streak + glints. */
export const LensFlare: React.FC<{ at?: number; x?: string; y?: string; w?: number }> = ({
  at = 0,
  x = "50%",
  y = "42%",
  w = 900,
}) => {
  const frame = useCurrentFrame();
  const t = frame - at;
  const k = interpolate(t, [0, 10, 40, 70], [0, 1, 1, 0.5], clamp);
  return (
    <div style={{ position: "absolute", left: x, top: y, transform: "translate(-50%,-50%)", pointerEvents: "none", opacity: k }}>
      <div
        style={{
          width: w,
          height: 3,
          background: `linear-gradient(90deg, transparent, ${Y}, #fff, ${Y}, transparent)`,
          filter: "blur(1px)",
          boxShadow: `0 0 24px ${Y}`,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: "50%",
          top: "50%",
          width: 120,
          height: 120,
          transform: "translate(-50%,-50%)",
          borderRadius: "50%",
          background: `radial-gradient(circle, rgba(255,255,255,0.8), rgba(255,214,10,0.2) 40%, transparent 70%)`,
        }}
      />
    </div>
  );
};

/* ------------------------------------------------------------------ */
/* Small building blocks                                               */
/* ------------------------------------------------------------------ */

/** The tiny selling label — amber pill, mono, ≤5 words. */
export const Label: React.FC<{ children: React.ReactNode; at?: number }> = ({ children, at = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - at, fps, config: { damping: 18, stiffness: 160 } });
  return (
    <div
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 8,
        fontFamily: FONT_MONO,
        fontSize: 15,
        fontWeight: 700,
        letterSpacing: 1,
        textTransform: "uppercase",
        color: COLORS.bg,
        background: Y,
        borderRadius: 6,
        padding: "6px 12px",
        opacity: interpolate(s, [0, 1], [0, 1], clamp),
        transform: `translateY(${interpolate(s, [0, 1], [12, 0])}px) scale(${interpolate(s, [0, 1], [0.9, 1])})`,
        boxShadow: `0 6px 24px rgba(255,214,10,0.28)`,
      }}
    >
      {children}
    </div>
  );
};

/** A small mono eyebrow (uppercase, tracked, amber). */
export const Eyebrow: React.FC<{ children: React.ReactNode; at?: number }> = ({ children, at = 0 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - at, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        fontFamily: FONT_MONO,
        fontSize: 15,
        letterSpacing: 4,
        textTransform: "uppercase",
        color: Y,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [10, 0])}px)`,
        display: "flex",
        alignItems: "center",
        gap: 10,
      }}
    >
      <span style={{ width: 22, height: 1, background: Y, opacity: 0.8 }} /> {children}
    </div>
  );
};

/** A framed real screenshot with a subtle floating parallax + soft border. */
export const Shot: React.FC<{
  src: string;
  width: number;
  height: number;
  at?: number;
  kenBurns?: boolean;
  radius?: number;
}> = ({ src, width, height, at = 0, kenBurns = false, radius = 14 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - at, fps, config: { damping: 200, mass: 0.9 } });
  const inner = (
    <Img src={staticFile(src)} style={{ width: "100%", height: "100%", objectFit: "cover", objectPosition: "top center" }} />
  );
  return (
    <div
      style={{
        width,
        height,
        borderRadius: radius,
        overflow: "hidden",
        border: `1px solid ${COLORS.borderStrong}`,
        boxShadow: `0 40px 100px rgba(0,0,0,0.6), 0 0 40px ${COLORS.primaryGlow}`,
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [26, 0])}px) scale(${interpolate(s, [0, 1], [0.97, 1])})`,
      }}
    >
      {kenBurns ? <KenBurns>{inner}</KenBurns> : inner}
    </div>
  );
};
