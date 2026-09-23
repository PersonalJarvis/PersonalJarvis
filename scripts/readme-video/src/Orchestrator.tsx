import React from "react";
import { Easing, interpolate } from "remotion";
import { PhoneOff, Sparkles } from "lucide-react";
import { GigiMark } from "@app/components/GigiMark";
import { useDemoFrame } from "./shared";
import { DesktopFrame } from "./DesktopFrame";

/**
 * Deterministic presentation of the shipped voice UI, with illustrative text.
 * Source: frontend/src/components/home/{VoiceStage,JarvisBar,Greeting}.tsx.
 * StageWaveform uses requestAnimationFrame and microphone refs in production;
 * the SVG below preserves its capsule geometry on Remotion's frame clock.
 * No tools or completed actions are fabricated by this demonstration.
 */
const UI = {
  card: "#171717",
  border: "#383838",
  primary: "#FAFAFA",
  muted: "#A1A1A1",
  faint: "#737373",
  destructive: "#EF4444",
};

const ease = Easing.bezier(0.22, 1, 0.36, 1);
const progress = (frame: number, start: number, end: number) =>
  interpolate(frame, [start, end], [0, 1], {
    extrapolateLeft: "clamp",
    extrapolateRight: "clamp",
    easing: ease,
  });

type Phase = "idle" | "listening" | "thinking" | "speaking";

function wordReveal(text: string, frame: number, start: number, every: number) {
  const words = text.split(" ");
  return words.slice(0, Math.max(0, Math.floor((frame - start) / every) + 1)).join(" ");
}

/** StageWaveform.tsx: 4px dots, 13px capsules, 15px pitch, 62% height cap. */
const VoiceTape: React.FC<{ frame: number; phase: Phase }> = ({ frame, phase }) => {
  const width = 678;
  const height = 56;
  const count = Math.floor((width + 2) / 15);
  const rowWidth = (count - 1) * 15 + 13;
  const measured = phase === "listening" || phase === "speaking";
  return (
    <svg width="100%" height={height} viewBox={`0 0 ${width} ${height}`} aria-hidden>
      <defs>
        <linearGradient id="readme-voice-age" x1="12%" x2="88%">
          <stop offset="0%" stopColor={UI.muted} />
          <stop offset="100%" stopColor={UI.primary} />
        </linearGradient>
        <linearGradient id="readme-voice-light" x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="white" stopOpacity={0.36} />
          <stop offset="34%" stopColor="white" stopOpacity={0.72} />
          <stop offset="50%" stopColor="white" />
          <stop offset="66%" stopColor="white" stopOpacity={0.72} />
          <stop offset="100%" stopColor="white" stopOpacity={0.36} />
        </linearGradient>
        <mask id="readme-voice-mask">
          {Array.from({ length: count }, (_, index) => {
            const t = index / (count - 1);
            const rim = Math.min(1, Math.min(t, 1 - t) / 0.12);
            const edge = Math.min(1, Math.min(t, 1 - t) / 0.06);
            const sampleFrame = frame - (count - 1 - index) * 2;
            // Sample data drives the same right-to-left tape as the app. It is
            // illustrative, just like the conversation; no audio is recorded.
            const envelope = Math.max(0, Math.sin(sampleFrame * 0.082)) *
              (0.22 + 0.78 * Math.abs(Math.sin(sampleFrame * 0.29)));
            const sweep = Math.max(0, 1 - Math.abs(((t + frame / 54 + 1.5) % 1) - 0.5) / 0.22);
            const value = measured
              ? Math.pow(envelope, 1.6)
              : phase === "thinking"
                ? (0.08 + 0.62 * sweep) * rim
                : (0.018 + 0.032 * (1 + Math.sin(t * 7 - frame / 24)) / 2) * rim;
            const knee = Math.min(1, value / 0.14);
            const barWidth = 4 + 9 * knee * knee * (3 - 2 * knee);
            const barHeight = 5 + (height * 0.62 - 5) * value;
            return (
              <rect
                key={index}
                x={(width - rowWidth) / 2 + 6.5 + index * 15 - barWidth / 2}
                y={(height - barHeight) / 2}
                width={barWidth}
                height={barHeight}
                rx={Math.min(barWidth, barHeight) / 2}
                fill="url(#readme-voice-light)"
                opacity={(0.3 + 0.7 * value) * edge * edge * (3 - 2 * edge)}
              />
            );
          })}
        </mask>
      </defs>
      <rect width={width} height={height} fill={measured ? "url(#readme-voice-age)" : UI.muted} mask="url(#readme-voice-mask)" />
    </svg>
  );
};

const VoiceCard: React.FC<{ frame: number; phase: Phase }> = ({ frame, phase }) => {
  const active = phase !== "idle";
  const hint = phase === "idle" ? 'Say “Hey George” or tap the bar' : phase === "listening" ? "Listening…" : phase === "thinking" ? "Thinking…" : "Speaking…";
  return (
    <div style={{ width: "100%", padding: "16px 12px 10px", boxSizing: "border-box", borderRadius: 16, border: `1px solid ${active ? "rgba(250,250,250,0.5)" : UI.border}`, background: UI.card }}>
      <VoiceTape frame={frame} phase={phase} />
      <div style={{ display: "flex", alignItems: "center", gap: 8, marginTop: 8, height: 32 }}>
        <span style={{ display: "flex", alignItems: "center", gap: 8, padding: "0 4px", fontFamily: "'JetBrains Mono', monospace", fontSize: 10, letterSpacing: "0.08em", textTransform: "uppercase", color: active ? UI.primary : UI.muted }}>
          <span style={{ width: 8, height: 8, borderRadius: "50%", background: active ? UI.primary : UI.faint, opacity: active ? 0.7 : 0.4 }} />
          {phase === "idle" ? "Ready" : phase}
        </span>
        <span style={{ flex: 1, fontSize: 12, color: UI.muted }}>{hint}</span>
        <span style={{ height: 32, display: "inline-flex", alignItems: "center", gap: 6, padding: "0 8px", color: UI.muted, fontSize: 12 }}>
          <Sparkles size={14} /><span>Prompt</span>
        </span>
        <span style={{ height: 32, display: "inline-flex", alignItems: "center", gap: 6, padding: "0 8px", color: UI.muted, fontSize: 12 }}>
          <span style={{ color: UI.primary, fontWeight: 500 }}>OpenAI GPT-Live</span>
          <span style={{ fontFamily: "'JetBrains Mono', monospace", fontSize: 10 }}>GPT-Live</span>
        </span>
        {active && <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "0 10px", height: 32, borderRadius: 8, border: "1px solid rgba(239,68,68,0.3)", background: "rgba(239,68,68,0.1)", color: UI.destructive, fontSize: 12, fontWeight: 500 }}><PhoneOff size={14} />End</span>}
      </div>
    </div>
  );
};

const Transcript: React.FC<{ who: string; text: string; live?: boolean; user?: boolean }> = ({ who, text, live = false, user = false }) => (
  <div style={{ display: "grid", gridTemplateColumns: "64px 1fr", alignItems: "baseline", columnGap: 16 }}>
    <span style={{ textAlign: "right", fontFamily: "'JetBrains Mono', monospace", textTransform: "uppercase", fontSize: 10, letterSpacing: "0.12em", color: UI.muted }}>{who}</span>
    <span style={{ fontSize: 18, lineHeight: 1.375, color: user || live ? UI.muted : UI.primary, fontStyle: live ? "italic" : "normal" }}>
      {text}{live && <span style={{ display: "inline-block", height: 18, width: 2, marginLeft: 2, transform: "translateY(2px)", background: UI.primary }} />}
    </span>
  </div>
);

export const Orchestrator: React.FC = () => {
  const frame = useDemoFrame();
  const enterConversation = progress(frame, 85, 110);
  const phase: Phase = frame < 65 ? "idle" : frame < 180 ? "listening" : frame < 210 ? "thinking" : frame < 340 ? "speaking" : "listening";
  const userText = wordReveal("Help me plan a small project. Where should I start?", frame, 92, 7);
  const assistantText = wordReveal("Start with the outcome. What would you like to build, and who is it for? Then we can choose the right agent.", frame, 212, 5);
  const caption = frame < 25 ? "Your assistant, in your desktop workspace."
    : frame < 65 ? <><span style={{ color: "#a1a1a1", fontSize: 12, letterSpacing: "0.12em", marginRight: 14 }}>WAKE PHRASE</span>“Hey George”</>
    : frame < 92 ? "Wake phrase detected · Listening"
    : frame < 180 ? "“Help me plan a small project. Where should I start?”"
    : frame < 210 ? "George is thinking…"
    : frame < 340 ? "“What would you like to build, and who is it for?”"
    : "Keep talking. Your agents, tools, and knowledge are one sidebar away.";

  return (
    <DesktopFrame status={phase === "idle" ? "Ready" : phase.charAt(0).toUpperCase() + phase.slice(1)} caption={caption}>
      <div style={{ position: "absolute", width: "100%", left: 0, top: interpolate(enterConversation, [0, 1], [166, 50]), padding: "0 48px", boxSizing: "border-box" }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", textAlign: "center" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 12, fontSize: 28, fontWeight: 600, color: UI.primary }}><GigiMark size={38} /><span>Good afternoon</span></div>
          <p style={{ margin: "10px 0 0", maxWidth: 580, fontSize: 15, lineHeight: 1.5, color: UI.muted, opacity: 1 - enterConversation }}>Say your wake word or tap the bar — the conversation shows up here.</p>
        </div>
        {userText && <div style={{ marginTop: 24, display: "flex", flexDirection: "column", gap: 28 }}>
          <Transcript who="You" text={userText} user live={frame < 180} />
          {assistantText && <Transcript who="George" text={assistantText} live={frame < 340} />}
        </div>}
      </div>
      <div style={{ position: "absolute", left: 0, right: 0, top: interpolate(enterConversation, [0, 1], [286, 422]), padding: "0 30px" }}>
        <VoiceCard frame={frame} phase={phase} />
      </div>
    </DesktopFrame>
  );
};
