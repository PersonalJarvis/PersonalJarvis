/**
 * The README film — nine scenes, the landing page brought to life.
 * Black + one signal-yellow, Gigi the ghost, the site's diction and motion.
 * Each scene times its reveals to the narration via line(scene, id).localStart.
 */
import React from "react";
import {
  interpolate,
  spring,
  staticFile,
  Img,
  useCurrentFrame,
  useVideoConfig,
} from "remotion";
import { COLORS, FONT } from "../theme";
import { FONT_DISPLAY, FONT_MONO } from "./fonts";
import { SceneWrap } from "../components/SceneWrap";
import { WaveBars } from "../components/WaveBars";
import { Cursor, CursorKey } from "../components/Cursor";
import { line, TimelineScene } from "./timeline";
import {
  Counter,
  CursorTrail,
  Eyebrow,
  GhostMark,
  GlitchText,
  HudFrame,
  Label,
  LensFlare,
  NodeGraph,
  Shot,
  TerminalBlock,
  TextScramble,
  VignetteOpen,
  WordCaptions,
  ZoomPunch,
} from "./fx";

const Y = COLORS.primary;
const clamp = { extrapolateLeft: "clamp", extrapolateRight: "clamp" } as const;

type SP = { scene: TimelineScene };

/* ---- small shared bits ------------------------------------------- */

const MicBadge: React.FC<{ jarvis?: boolean; size?: number }> = ({ jarvis = false, size = 52 }) => (
  <div
    style={{
      width: size,
      height: size,
      borderRadius: "50%",
      background: jarvis ? COLORS.bgElevated : "rgba(255,214,10,0.12)",
      border: `1px solid ${Y}`,
      display: "flex",
      alignItems: "center",
      justifyContent: "center",
      flexShrink: 0,
      boxShadow: jarvis ? `0 0 16px ${COLORS.primaryGlow}` : "none",
    }}
  >
    {jarvis ? (
      <Img src={staticFile("jarvis-gigi.png")} style={{ width: size * 0.7, height: size * 0.7, objectFit: "contain" }} />
    ) : (
      <svg width={size * 0.44} height={size * 0.44} viewBox="0 0 24 24" fill="none">
        <rect x="9" y="2" width="6" height="12" rx="3" fill={Y} />
        <path d="M5 11a7 7 0 0 0 14 0M12 18v3" stroke={Y} strokeWidth="2" strokeLinecap="round" />
      </svg>
    )}
  </div>
);

/** A spoken reply bubble (Jarvis, the butler voice). */
const ReplyBubble: React.FC<{ text: string; at: number; width?: number }> = ({ text, at, width = 620 }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - at, fps, config: { damping: 18, stiffness: 150, mass: 0.7 } });
  return (
    <div
      style={{
        display: "flex",
        alignItems: "center",
        gap: 16,
        padding: "16px 22px",
        borderRadius: 18,
        background: COLORS.bgCard,
        border: `1px solid ${COLORS.border}`,
        maxWidth: width,
        opacity: interpolate(s, [0, 1], [0, 1], clamp),
        transform: `translateY(${interpolate(s, [0, 1], [18, 0])}px) scale(${interpolate(s, [0, 1], [0.96, 1])})`,
      }}
    >
      <MicBadge jarvis size={48} />
      <WaveBars width={70} height={26} active />
      <span style={{ fontFamily: FONT_DISPLAY, fontSize: 26, fontWeight: 500, color: COLORS.text, lineHeight: 1.3 }}>
        {text}
      </span>
    </div>
  );
};

/* ================================================================== */
/* 1 · OPEN — the ghost wakes                                          */
/* ================================================================== */
export const OpenScene: React.FC<SP> = ({ scene }) => {
  const at = line(scene, "open_1").localStart;
  return (
    <SceneWrap>
      <VignetteOpen at={0} dur={44} />
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30 }}>
        <GhostMark size={230} assembleAt={2} />
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14 }}>
          <div style={{ minHeight: 70 }}>
            <TextScramble text="Personal Jarvis" start={at} perChar={1.3} size={64} weight={600} letterSpacing={-1} />
          </div>
          <div style={{ opacity: interpolate(useCurrentFrame(), [at + 24, at + 40], [0, 1], clamp) }}>
            <Eyebrow at={at + 24}>voice-first · open source · yours</Eyebrow>
          </div>
        </div>
      </div>
    </SceneWrap>
  );
};

/* ================================================================== */
/* 2 · COMMAND — you just talk                                         */
/* ================================================================== */
export const CommandScene: React.FC<SP> = ({ scene }) => {
  const cmd = line(scene, "cmd_1");
  const frame = useCurrentFrame();
  const rowIn = spring({ frame: frame - (cmd.localStart - 6), fps: 30, config: { damping: 200 } });
  const wakeAt = cmd.localStart + 4;
  const capAt = cmd.localStart + 24;
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 34, width: "100%" }}>
        <Eyebrow at={4}>You just talk</Eyebrow>
        <ZoomPunch at={capAt} peak={1.05}>
          <div
            style={{
              display: "flex",
              flexDirection: "column",
              alignItems: "center",
              gap: 26,
              opacity: rowIn,
              transform: `translateY(${interpolate(rowIn, [0, 1], [18, 0])}px)`,
            }}
          >
            <div style={{ display: "flex", alignItems: "center", gap: 18 }}>
              <MicBadge />
              <WaveBars width={150} height={38} active />
              <span
                style={{
                  fontFamily: FONT_DISPLAY,
                  fontSize: 30,
                  fontWeight: 700,
                  color: Y,
                  opacity: interpolate(frame, [wakeAt, wakeAt + 8], [0, 1], clamp),
                }}
              >
                “Hey Jarvis”
              </span>
            </div>
            <div style={{ maxWidth: 960, textAlign: "center" }}>
              <WordCaptions text="Open Chrome and pull up the weather in Berlin." start={capAt} dur={cmd.dur - 24} size={46} />
            </div>
          </div>
        </ZoomPunch>
      </div>
    </SceneWrap>
  );
};

/* ================================================================== */
/* 3 · ACK — it answers before it's done                              */
/* ================================================================== */
export const AckScene: React.FC<SP> = ({ scene }) => {
  const n = line(scene, "ack_1");
  const reply = line(scene, "ack_reply");
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30, width: "100%" }}>
        <Eyebrow at={2}>Instant</Eyebrow>
        <div style={{ maxWidth: 900, textAlign: "center", minHeight: 52 }}>
          <WordCaptions text="You never wait in silence." start={n.localStart} dur={n.dur} size={40} />
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 40, marginTop: 6 }}>
          <ReplyBubble text="On it — checking Berlin now." at={reply.localStart} width={520} />
          <ZoomPunch at={reply.localStart} peak={1.08}>
            <div
              style={{
                position: "relative",
                padding: "18px 26px",
                borderRadius: 14,
                background: COLORS.bgElevated,
                border: `1px solid ${COLORS.borderStrong}`,
              }}
            >
              <HudFrame width={190} height={92} at={reply.localStart} label="WAKE → ACK" />
              <div style={{ display: "flex", alignItems: "baseline", gap: 4, justifyContent: "center" }}>
                <Counter to={0.9} start={reply.localStart} dur={26} decimals={1} size={54} />
                <span style={{ fontFamily: FONT_MONO, fontSize: 24, color: COLORS.textMuted }}>s</span>
              </div>
            </div>
          </ZoomPunch>
        </div>

        <div style={{ marginTop: 8 }}>
          <Label at={reply.localStart + 10}>sub-second ack</Label>
        </div>
      </div>
    </SceneWrap>
  );
};

/* ================================================================== */
/* 4 · ACTION — the crew takes over (parallel)                        */
/* ================================================================== */
const BrowserWeather: React.FC<{ demoAt: number }> = ({ demoAt }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - demoAt, fps, config: { damping: 200 } });
  const W = 520;
  const H = 300;
  const keys: CursorKey[] = [
    { frame: demoAt, x: W - 60, y: 40 },
    { frame: demoAt + 22, x: 190, y: 46 },
    { frame: demoAt + 30, x: 190, y: 46, click: true },
    { frame: demoAt + 60, x: 250, y: 200 },
    { frame: demoAt + 70, x: 250, y: 200, click: true },
    { frame: demoAt + 120, x: 250, y: 200 },
  ];
  const showResult = frame > demoAt + 40;
  return (
    <div style={{ position: "relative", width: W, height: H, opacity: enter, transform: `translateY(${interpolate(enter, [0, 1], [20, 0])}px)` }}>
      <HudFrame width={W} height={H} at={demoAt} label="COMPUTER-USE" />
      <div style={{ width: W, height: H, borderRadius: 12, overflow: "hidden", background: COLORS.bgElevated, border: `1px solid ${COLORS.borderStrong}` }}>
        <div style={{ height: 40, display: "flex", alignItems: "center", gap: 10, padding: "0 14px", background: COLORS.bgCard, borderBottom: `1px solid ${COLORS.border}` }}>
          <div style={{ display: "flex", gap: 12, color: COLORS.textFaint, fontSize: 15 }}>
            <span>←</span><span>→</span><span>⟳</span>
          </div>
          <div style={{ flex: 1, height: 26, borderRadius: 999, background: COLORS.bg, border: `1px solid ${COLORS.border}`, display: "flex", alignItems: "center", padding: "0 12px", fontFamily: FONT_MONO, fontSize: 12, color: COLORS.textMuted }}>
            google.com/search?q=weather berlin
          </div>
        </div>
        <div style={{ padding: 18 }}>
          {showResult && (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", padding: "14px 18px", borderRadius: 12, background: "rgba(255,214,10,0.06)", border: `1px solid rgba(255,214,10,0.25)` }}>
              <div>
                <div style={{ fontFamily: FONT, fontSize: 15, color: COLORS.textMuted }}>Berlin, Germany</div>
                <div style={{ fontFamily: FONT_DISPLAY, fontSize: 40, fontWeight: 700, color: COLORS.text }}>18°</div>
                <div style={{ fontFamily: FONT, fontSize: 15, color: COLORS.textMuted }}>Clear · feels like 17°</div>
              </div>
              <svg width="66" height="66" viewBox="0 0 24 24" fill="none">
                <circle cx="12" cy="12" r="5" fill={Y} />
                {[0, 45, 90, 135, 180, 225, 270, 315].map((a) => (
                  <line key={a} x1="12" y1="12" x2={12 + 10 * Math.cos((a * Math.PI) / 180)} y2={12 + 10 * Math.sin((a * Math.PI) / 180)} stroke={Y} strokeWidth="1.6" strokeLinecap="round" opacity="0.8" />
                ))}
              </svg>
            </div>
          )}
        </div>
      </div>
      <CursorTrail keys={keys} />
      <Cursor keys={keys} />
    </div>
  );
};

const STEP_LABELS = ["Looking", "Reading", "Clicking", "Verifying"];
const StepChips: React.FC<{ demoAt: number }> = ({ demoAt }) => {
  const frame = useCurrentFrame();
  const ats = [demoAt + 4, demoAt + 34, demoAt + 62, demoAt + 96];
  return (
    <div style={{ display: "flex", gap: 8, justifyContent: "center", flexWrap: "wrap" }}>
      {STEP_LABELS.map((l, i) => {
        const on = frame >= ats[i];
        return (
          <div key={l} style={{ display: "flex", alignItems: "center", gap: 7, padding: "5px 12px", borderRadius: 999, border: `1px solid ${on ? "rgba(255,214,10,0.45)" : COLORS.border}`, background: on ? "rgba(255,214,10,0.10)" : "transparent", fontFamily: FONT_MONO, fontSize: 13, fontWeight: 600, color: on ? Y : COLORS.textFaint }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: on ? Y : COLORS.textFaint }} />
            {l}
          </div>
        );
      })}
    </div>
  );
};

const MissionTicker: React.FC<{ at: number }> = ({ at }) => {
  const frame = useCurrentFrame();
  const lines = [
    "› spawning worker · isolated worktree",
    "› searching: vector databases 2026",
    "› comparing pgvector · qdrant · milvus",
    "› drafting comparison.md",
    "› critic pass 1 …",
  ];
  return (
    <div style={{ fontFamily: FONT_MONO, fontSize: 14, lineHeight: 1.8, color: COLORS.textMuted, minHeight: 150 }}>
      {lines.map((l, i) => {
        const on = frame >= at + i * 16;
        return (
          <div key={i} style={{ opacity: on ? 1 : 0, transform: `translateX(${on ? 0 : -8}px)`, color: i === lines.length - 1 ? Y : COLORS.textMuted }}>
            {l}
          </div>
        );
      })}
    </div>
  );
};

export const ActionScene: React.FC<SP> = ({ scene }) => {
  const cmd = line(scene, "cmd_2");
  const act3 = line(scene, "act_3");
  const demoAt = cmd.localStart + cmd.dur - 6;
  const frame = useCurrentFrame();
  return (
    <SceneWrap padding={64}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 20, width: "100%" }}>
        {/* the command that kicks it off */}
        <div style={{ display: "flex", alignItems: "center", gap: 14, opacity: interpolate(frame, [cmd.localStart - 6, cmd.localStart + 4], [0, 1], clamp) }}>
          <MicBadge size={40} />
          <span style={{ fontFamily: FONT_DISPLAY, fontSize: 24, fontWeight: 700, color: COLORS.text, maxWidth: 900 }}>
            “Research the best open-source vector databases. Put it in my Outputs.”
          </span>
        </div>

        <div style={{ display: "flex", alignItems: "flex-start", gap: 34, marginTop: 4 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 14, alignItems: "center" }}>
            <BrowserWeather demoAt={demoAt} />
            <StepChips demoAt={demoAt} />
          </div>
          <div style={{ width: 470, position: "relative" }}>
            <NodeGraph at={demoAt + 6} width={460} height={230} />
            <MissionTicker at={demoAt + 20} />
          </div>
        </div>

        <div style={{ marginTop: 2 }}>
          <Label at={act3.localStart}>router delegates</Label>
        </div>
      </div>
    </SceneWrap>
  );
};

/* ================================================================== */
/* 5 · CRITIC — it checks its own work                                */
/* ================================================================== */
export const CriticScene: React.FC<SP> = ({ scene }) => {
  const n = line(scene, "crit_1");
  const frame = useCurrentFrame();
  const passes = [
    { t: n.localStart + 10, label: "Critic pass 1", note: "found gaps · sent back" },
    { t: n.localStart + 34, label: "Critic pass 2", note: "tightened · sent back" },
    { t: n.localStart + 58, label: "Critic pass 3", note: "approved" },
  ];
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 26, width: "100%" }}>
        <Eyebrow at={2}>Self-healing</Eyebrow>
        <div style={{ maxWidth: 860, textAlign: "center" }}>
          <WordCaptions text="Every result is checked — up to three times — before you see it." start={n.localStart} dur={n.dur} size={36} />
        </div>
        <div style={{ display: "flex", flexDirection: "column", gap: 12, width: 560, marginTop: 6 }}>
          {passes.map((p, i) => {
            const on = frame >= p.t;
            const done = i === passes.length - 1 && frame >= p.t;
            return (
              <div key={i} style={{ display: "flex", alignItems: "center", gap: 16, padding: "14px 20px", borderRadius: 12, background: COLORS.bgCard, border: `1px solid ${done ? Y : COLORS.border}`, opacity: on ? 1 : 0.25, transform: `translateX(${on ? 0 : -10}px)` }}>
                <div style={{ width: 30, height: 30, borderRadius: "50%", border: `2px solid ${done ? Y : COLORS.borderStrong}`, display: "flex", alignItems: "center", justifyContent: "center", color: done ? Y : COLORS.textMuted, fontFamily: FONT_MONO, fontWeight: 700, fontSize: 15 }}>
                  {done ? "✓" : i + 1}
                </div>
                <span style={{ fontFamily: FONT_DISPLAY, fontSize: 20, fontWeight: 600, color: COLORS.text }}>{p.label}</span>
                <span style={{ fontFamily: FONT_MONO, fontSize: 14, color: done ? Y : COLORS.textFaint, marginLeft: "auto" }}>{p.note}</span>
              </div>
            );
          })}
        </div>
        <Label at={passes[2].t}>critic reviews · up to 3×</Label>
      </div>
    </SceneWrap>
  );
};

/* ================================================================== */
/* 6 · RESULT — something you keep (Outputs artifact)                 */
/* ================================================================== */
const OutputsPanel: React.FC<{ at: number }> = ({ at }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame: frame - at, fps, config: { damping: 200 } });
  const W = 720;
  const H = 340;
  return (
    <ZoomPunch at={at + 20} peak={1.04} origin="50% 40%">
      <div style={{ width: W, height: H, borderRadius: 16, overflow: "hidden", background: COLORS.bgElevated, border: `1px solid ${COLORS.borderStrong}`, boxShadow: `0 40px 100px rgba(0,0,0,0.6), 0 0 44px ${COLORS.primaryGlow}`, opacity: enter, transform: `translateY(${interpolate(enter, [0, 1], [24, 0])}px)`, display: "flex", flexDirection: "column" }}>
        <div style={{ height: 44, display: "flex", alignItems: "center", gap: 10, padding: "0 18px", background: COLORS.bgCard, borderBottom: `1px solid ${COLORS.border}` }}>
          <svg width="18" height="18" viewBox="0 0 24 24" fill="none"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2V7z" stroke={Y} strokeWidth="1.6" /></svg>
          <span style={{ fontFamily: FONT_DISPLAY, fontSize: 18, fontWeight: 600, color: COLORS.text }}>Outputs</span>
          <span style={{ fontFamily: FONT_MONO, fontSize: 13, color: COLORS.textFaint, marginLeft: 6 }}>· ready to use</span>
        </div>
        <div style={{ flex: 1, display: "flex" }}>
          <div style={{ width: 230, borderRight: `1px solid ${COLORS.border}`, padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", borderRadius: 10, background: "rgba(255,214,10,0.10)", border: `1px solid rgba(255,214,10,0.35)` }}>
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none"><path d="M6 2h9l5 5v15H6z" stroke={Y} strokeWidth="1.6" /></svg>
              <span style={{ fontFamily: FONT_MONO, fontSize: 13, color: Y }}>vector-databases.md</span>
            </div>
            <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "10px 12px", opacity: 0.6 }}>
              <span style={{ fontFamily: FONT_MONO, fontSize: 13, color: COLORS.textMuted }}>sources.json</span>
            </div>
          </div>
          <div style={{ flex: 1, padding: 20 }}>
            <div style={{ fontFamily: FONT_DISPLAY, fontSize: 24, fontWeight: 700, color: COLORS.text }}>Vector Databases — Comparison</div>
            <div style={{ height: 1, background: COLORS.border, margin: "12px 0" }} />
            {[
              "pgvector — simplest, rides on Postgres",
              "Qdrant — fast filtered search, Rust core",
              "Milvus — scales to billions of vectors",
            ].map((t, i) => (
              <div key={i} style={{ display: "flex", gap: 10, alignItems: "flex-start", marginBottom: 9, opacity: interpolate(frame, [at + 24 + i * 8, at + 34 + i * 8], [0, 1], clamp) }}>
                <span style={{ color: Y, marginTop: 2 }}>▪</span>
                <span style={{ fontFamily: FONT, fontSize: 15, color: COLORS.textMuted }}>{t}</span>
              </div>
            ))}
            <div style={{ marginTop: 16, display: "inline-flex", alignItems: "center", gap: 9, padding: "9px 16px", borderRadius: 8, background: Y, color: COLORS.bg, fontFamily: FONT_MONO, fontSize: 14, fontWeight: 700 }}>
              <svg width="15" height="15" viewBox="0 0 24 24" fill="none"><path d="M12 3v12m0 0l-4-4m4 4l4-4M4 21h16" stroke={COLORS.bg} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" /></svg>
              Download
            </div>
          </div>
        </div>
      </div>
    </ZoomPunch>
  );
};

export const ResultScene: React.FC<SP> = ({ scene }) => {
  const reply = line(scene, "res_reply");
  const n = line(scene, "res_1");
  return (
    <SceneWrap padding={64}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 22, width: "100%" }}>
        <ReplyBubble text="Berlin's clear and 18°. Your report is in Outputs." at={reply.localStart} width={640} />
        <OutputsPanel at={reply.localStart + 8} />
        <div style={{ minHeight: 40 }}>
          <WordCaptions text="Not just an answer — something you keep." start={n.localStart} dur={n.dur} size={30} />
        </div>
      </div>
    </SceneWrap>
  );
};

/* ================================================================== */
/* 7 · PROOF — a real desktop app (montage + signature line)          */
/* ================================================================== */
const SHOTS = [
  { src: "app-home.png", label: "READY FOR COMMANDS" },
  { src: "shot-wiki-page.png", label: "KNOWLEDGE WIKI" },
  { src: "shot-apikeys.png", label: "BRING YOUR OWN KEYS" },
];
export const ProofScene: React.FC<SP> = ({ scene }) => {
  const p1 = line(scene, "proof_1");
  const p2 = line(scene, "proof_2");
  const frame = useCurrentFrame();
  const each = 44;
  const start = p1.localStart;
  const idx = Math.min(SHOTS.length - 1, Math.max(0, Math.floor((frame - start) / each)));
  return (
    <SceneWrap padding={56}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 22, width: "100%" }}>
        <Eyebrow at={2}>Real, not a demo</Eyebrow>
        <div style={{ position: "relative", width: 900, height: 380 }}>
          {SHOTS.map((sh, i) => {
            const isLast = i === SHOTS.length - 1;
            const local = frame - (start + i * each);
            const visible = isLast ? local > -8 : local > -8 && local < each + 8;
            const enterSlide = interpolate(local, [-8, 4], [70, 0], clamp);
            const exitSlide = isLast ? 0 : interpolate(local, [each - 6, each + 8], [0, -70], clamp);
            const slide = enterSlide + exitSlide;
            const blur = isLast
              ? Math.abs(interpolate(local, [-8, 2], [8, 0], clamp))
              : Math.abs(interpolate(local, [-8, 2, each - 6, each + 8], [8, 0, 0, 8], clamp));
            const shotOpacity = isLast
              ? interpolate(local, [-8, 2], [0, 1], clamp)
              : interpolate(local, [-8, 2, each - 4, each + 8], [0, 1, 1, 0], clamp);
            if (!visible) return null;
            return (
              <div key={sh.src} style={{ position: "absolute", inset: 0, transform: `translateX(${slide}px)`, filter: `blur(${blur}px)`, opacity: shotOpacity }}>
                <Shot src={sh.src} width={900} height={380} at={start + i * each} kenBurns />
                <div style={{ position: "absolute", left: 16, bottom: 16 }}>
                  <div style={{ fontFamily: FONT_MONO, fontSize: 13, letterSpacing: 2, color: COLORS.bg, background: Y, padding: "5px 11px", borderRadius: 6, fontWeight: 700 }}>{sh.label}</div>
                </div>
              </div>
            );
          })}
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: 26 }}>
          <div style={{ minHeight: 40 }}>
            <TextScramble text="Your word is the command line." start={p2.localStart} perChar={0.9} size={34} weight={600} />
          </div>
          <div style={{ display: "flex", gap: 8, opacity: interpolate(frame, [p2.localStart + 20, p2.localStart + 34], [0, 1], clamp) }}>
            {["de", "en", "es"].map((l) => (
              <span key={l} style={{ fontFamily: FONT_MONO, fontSize: 14, color: Y, border: `1px solid rgba(255,214,10,0.4)`, borderRadius: 6, padding: "4px 10px", textTransform: "uppercase" }}>{l}</span>
            ))}
          </div>
        </div>
      </div>
    </SceneWrap>
  );
};

/* ================================================================== */
/* 8 · INSTALL — one command, your keys                               */
/* ================================================================== */
export const InstallScene: React.FC<SP> = ({ scene }) => {
  const n = line(scene, "inst_1");
  const frame = useCurrentFrame();
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 26, width: "100%" }}>
        <Eyebrow at={2}>Install</Eyebrow>
        <TerminalBlock
          command="irm https://raw.githubusercontent.com/PersonalJarvis/PersonalJarvis/main/install/install.ps1 | iex"
          start={n.localStart}
          width={1080}
          fontSize={16}
          cps={52}
        />
        <div style={{ display: "flex", alignItems: "center", gap: 14, marginTop: 4, opacity: interpolate(frame, [n.localStart + 30, n.localStart + 46], [0, 1], clamp) }}>
          {["Claude", "OpenAI", "Gemini", "OpenRouter"].map((p) => (
            <span key={p} style={{ fontFamily: FONT_MONO, fontSize: 15, color: COLORS.textMuted, border: `1px solid ${COLORS.border}`, borderRadius: 999, padding: "6px 14px" }}>{p}</span>
          ))}
        </div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 14, color: COLORS.textFaint, letterSpacing: 2, opacity: interpolate(frame, [n.localStart + 40, n.localStart + 54], [0, 1], clamp) }}>
          LINUX · macOS · WINDOWS
        </div>
      </div>
    </SceneWrap>
  );
};

/* ================================================================== */
/* 9 · OUTRO — the end card                                           */
/* ================================================================== */
export const OutroScene: React.FC<SP> = ({ scene }) => {
  const o1 = line(scene, "out_1");
  const o2 = line(scene, "out_2");
  const frame = useCurrentFrame();
  return (
    <SceneWrap>
      <LensFlare at={o2.localStart + 6} y="38%" w={1000} />
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 22 }}>
        <GhostMark size={150} assembleAt={null} />
        <div style={{ fontFamily: FONT_DISPLAY, fontSize: 58, fontWeight: 600, letterSpacing: -1, color: COLORS.text, opacity: interpolate(frame, [o1.localStart, o1.localStart + 14], [0, 1], clamp), transform: `translateY(${interpolate(frame, [o1.localStart, o1.localStart + 14], [16, 0], clamp)}px)` }}>
          Personal Jarvis
        </div>
        <div style={{ minHeight: 56, textAlign: "center" }}>
          {frame >= o2.localStart ? (
            <div style={{ fontFamily: FONT_DISPLAY, fontSize: 34, fontWeight: 500, color: COLORS.text, maxWidth: 900, lineHeight: 1.3 }}>
              Talk to your computer, and <GlitchText at={o2.localStart + 10} size={34} weight={700}>watch it do the work.</GlitchText>
            </div>
          ) : null}
        </div>
        <div style={{ fontFamily: FONT_MONO, fontSize: 16, letterSpacing: 3, color: COLORS.textMuted, opacity: interpolate(frame, [o2.localStart + 24, o2.localStart + 40], [0, 1], clamp), marginTop: 4 }}>
          <span style={{ color: Y, fontWeight: 700 }}>MIT</span> · runs on your machine
        </div>
      </div>
    </SceneWrap>
  );
};
