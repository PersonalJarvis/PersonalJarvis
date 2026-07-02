import { Img, interpolate, spring, staticFile, useCurrentFrame, useVideoConfig } from "remotion";
import { AbsoluteFill } from "remotion";
import { SceneWrap } from "../components/SceneWrap";
import { Kicker, Subtitle, Title } from "../components/Text";
import { SpokenCommand } from "../components/SpokenCommand";
import { BrandTile } from "../components/BrandTile";
import { AgentCard } from "../components/AgentCard";
import { Mascot } from "../onboarding/Mascot";
import { Phrase } from "../onboarding/Phrase";
import { COLORS, FONT } from "../theme";
import { line, TimelineScene } from "./timeline";

/**
 * Scenes for the ~87s README promo film — same narrated, calm grammar as the
 * onboarding example (voiceover drives every reveal; real screenshots; real
 * brand logos), retold as a compact "what is Personal Jarvis" pitch.
 */

const Accent: React.FC<{ children: React.ReactNode }> = ({ children }) => (
  <span style={{ color: COLORS.primary }}>{children}</span>
);

/** Small yellow-bordered chip (OutroYT grammar). */
const Chip: React.FC<{ delay: number; children: React.ReactNode; size?: number }> = ({
  delay,
  children,
  size = 19,
}) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  return (
    <div
      style={{
        opacity: s,
        transform: `translateY(${interpolate(s, [0, 1], [14, 0])}px)`,
        padding: "9px 20px",
        borderRadius: 999,
        border: `1px solid rgba(255,214,10,0.35)`,
        backgroundColor: "rgba(255,214,10,0.10)",
        fontFamily: FONT,
        fontSize: size,
        fontWeight: 700,
        color: COLORS.primary,
        whiteSpace: "nowrap",
      }}
    >
      {children}
    </div>
  );
};

/* ── 1 · HOOK ──────────────────────────────────────────────────────────── */
export const HookPromo: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const l2 = line(scene, "hook_2").localStart;
  return (
    <AbsoluteFill>
      <AbsoluteFill style={{ alignItems: "center", justifyContent: "flex-start", paddingTop: 58 }}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 8 }}>
          <Mascot size={104} />
          <span
            style={{
              fontFamily: FONT,
              fontSize: 15,
              letterSpacing: 4,
              textTransform: "uppercase",
              fontWeight: 700,
              color: COLORS.textFaint,
            }}
          >
            Personal Jarvis
          </span>
        </div>
      </AbsoluteFill>
      <Phrase start={line(scene, "hook_1").localStart} end={l2}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 14, paddingTop: 90 }}>
          <Title size={58}>
            A <Accent>free, open-source</Accent> AI assistant
          </Title>
          <Subtitle size={30}>that lives on your own computer.</Subtitle>
        </div>
      </Phrase>
      <Phrase start={l2} end={scene.dur}>
        <div style={{ paddingTop: 90 }}>
          <Title size={62}>
            You talk. <Accent>It gets things done.</Accent>
          </Title>
        </div>
      </Phrase>
    </AbsoluteFill>
  );
};

/* ── 2 · REAL APP (real screenshot + Outputs callout) ─────────────────── */
const SRC_W = 1920;
const DW = 900;
const SCALE = DW / SRC_W;
const OUTPUTS = { x: 14, y: 884, w: 244, h: 38 };

export const RealAppPromo: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.8 } });
  const highlightAt = line(scene, "app_2").localStart;

  return (
    <SceneWrap padding={64}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
        <Kicker>The real desktop app</Kicker>
        <div
          style={{
            position: "relative",
            width: DW,
            borderRadius: 14,
            overflow: "hidden",
            border: `1px solid ${COLORS.borderStrong}`,
            boxShadow: `0 30px 80px rgba(0,0,0,0.55), 0 0 46px ${COLORS.primaryGlow}`,
            opacity: enter,
            transform: `translateY(${interpolate(enter, [0, 1], [28, 0])}px) scale(${interpolate(
              enter,
              [0, 1],
              [0.96, 1],
            )})`,
          }}
        >
          <Img src={staticFile("app-home.png")} style={{ width: DW, display: "block" }} />
          <OutputsCallout delay={highlightAt} />
        </div>
      </div>
    </SceneWrap>
  );
};

const OutputsCallout: React.FC<{ delay: number }> = ({ delay }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const s = spring({ frame: frame - delay, fps, config: { damping: 200 } });
  const pulse = (Math.sin((frame - delay) / 12) + 1) / 2;
  const box = {
    left: OUTPUTS.x * SCALE,
    top: OUTPUTS.y * SCALE,
    width: OUTPUTS.w * SCALE,
    height: OUTPUTS.h * SCALE,
  };
  return (
    <>
      <div
        style={{
          position: "absolute",
          left: box.left,
          top: box.top,
          width: box.width,
          height: box.height,
          borderRadius: 8,
          border: `2px solid ${COLORS.primary}`,
          boxShadow: `0 0 ${10 + pulse * 14}px ${COLORS.primaryGlow}`,
          backgroundColor: "rgba(255,214,10,0.10)",
          opacity: s,
        }}
      />
      <div
        style={{
          position: "absolute",
          left: box.left + box.width + 16,
          top: box.top + box.height / 2 - 19,
          display: "flex",
          alignItems: "center",
          gap: 10,
          padding: "8px 14px",
          borderRadius: 999,
          backgroundColor: COLORS.bgCard,
          border: `1px solid ${COLORS.primary}`,
          fontFamily: FONT,
          fontSize: 17,
          fontWeight: 700,
          color: COLORS.text,
          whiteSpace: "nowrap",
          opacity: s,
          transform: `translateX(${interpolate(s, [0, 1], [-10, 0])}px)`,
        }}
      >
        <span style={{ color: COLORS.primary, fontSize: 20 }}>←</span>
        Every result lands here
      </div>
    </>
  );
};

/* ── 3 · VOICE (spoken command + sub-second answer) ───────────────────── */
export const VoicePromo: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const ask = line(scene, "voice_1").localStart;
  const reply = line(scene, "voice_2").localStart;
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 30 }}>
        <div style={{ alignSelf: "center" }}>
          <Kicker delay={ask}>Just talk</Kicker>
        </div>
        <SpokenCommand
          delay={ask + 8}
          wake="Hey Jarvis"
          text="book a table for two at eight."
          size={34}
        />
        <SpokenCommand
          delay={reply}
          speaker="jarvis"
          text="Done — table for two, tonight at eight."
          size={34}
        />
      </div>
    </SceneWrap>
  );
};

/* ── 4 · AGENTS (background missions + critic) ────────────────────────── */
export const AgentsPromo: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const a1 = line(scene, "agents_1").localStart;
  const a2 = line(scene, "agents_2").localStart;
  return (
    <SceneWrap padding={64}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 30 }}>
        <Kicker delay={a1}>Background missions</Kicker>
        <div style={{ display: "flex", gap: 22 }}>
          <AgentCard title="Code" task="Fix the wake-word bug · open a PR" delay={a1 + 14} doneAt={a2 + 42} width={340} />
          <AgentCard title="Research" task="Compare flight prices for Friday" delay={a1 + 26} doneAt={a2 + 74} width={340} />
          <AgentCard title="Call" task="Book the restaurant · confirm at 8" delay={a1 + 38} doneAt={a2 + 106} width={340} />
        </div>
        <Chip delay={a2 + 10}>A critic reviews every result — nothing ships unchecked</Chip>
      </div>
    </SceneWrap>
  );
};

/* ── 5 · MEMORY (real wiki graph screenshot) ──────────────────────────── */
const WIKI_DW = 960; // shot-wiki-map.png is 6824x3928 — scaled to fit the 720p stage

export const MemoryPromo: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const enter = spring({ frame, fps, config: { damping: 200, mass: 0.8 } });
  const m2 = line(scene, "memory_2").localStart;
  const s2 = spring({ frame: frame - m2, fps, config: { damping: 200 } });

  return (
    <SceneWrap padding={64}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
        <Kicker>It remembers</Kicker>
        <div
          style={{
            position: "relative",
            width: WIKI_DW,
            borderRadius: 14,
            overflow: "hidden",
            border: `1px solid ${COLORS.borderStrong}`,
            boxShadow: `0 30px 80px rgba(0,0,0,0.55), 0 0 46px ${COLORS.primaryGlow}`,
            opacity: enter,
            transform: `translateY(${interpolate(enter, [0, 1], [28, 0])}px)`,
          }}
        >
          <Img src={staticFile("shot-wiki-map.png")} style={{ width: WIKI_DW, display: "block" }} />
          <div
            style={{
              position: "absolute",
              right: 18,
              bottom: 16,
              padding: "8px 14px",
              borderRadius: 999,
              backgroundColor: COLORS.bgCard,
              border: `1px solid ${COLORS.primary}`,
              fontFamily: FONT,
              fontSize: 17,
              fontWeight: 700,
              color: COLORS.text,
              opacity: s2,
              transform: `translateY(${interpolate(s2, [0, 1], [10, 0])}px)`,
            }}
          >
            Plain markdown — Obsidian-compatible
          </div>
        </div>
      </div>
    </SceneWrap>
  );
};

/* ── 6 · PRIVATE (local wake/STT, no telemetry) ───────────────────────── */
export const PrivatePromo: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const p1 = line(scene, "private_1").localStart;
  const p2 = line(scene, "private_2").localStart;
  const p3 = line(scene, "private_3").localStart;
  return (
    <AbsoluteFill>
      <Phrase start={p1} end={p2}>
        <Title size={78}>
          <Accent>Private</Accent> by design.
        </Title>
      </Phrase>
      <Phrase start={p2} end={p3}>
        <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 16 }}>
          <Title size={50}>Wake word + speech recognition run fully local.</Title>
          <Subtitle size={28}>Your audio never has to leave the machine.</Subtitle>
        </div>
      </Phrase>
      <Phrase start={p3} end={scene.dur}>
        <div style={{ display: "flex", gap: 18 }}>
          <Chip delay={p3 + 2} size={22}>No account</Chip>
          <Chip delay={p3 + 12} size={22}>No telemetry</Chip>
          <Chip delay={p3 + 22} size={22}>Your files stay yours</Chip>
        </div>
      </Phrase>
    </AbsoluteFill>
  );
};

/* ── 7 · PROVIDERS (real logos, no lock-in) ───────────────────────────── */
export const ProvidersPromo: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const p1 = line(scene, "prov_1").localStart;
  const p2 = line(scene, "prov_2").localStart;
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 34 }}>
        <Kicker delay={p1}>Bring your own brain</Kicker>
        <Title delay={p1 + 6} size={44}>
          Your keys — or the subscription <Accent>you already pay for.</Accent>
        </Title>
        <div style={{ display: "flex", gap: 34, marginTop: 8 }}>
          <BrandTile slug="googlegemini" label="Gemini" delay={p2} />
          <BrandTile slug="claude" label="Claude" delay={p2 + 10} />
          <BrandTile slug="openai" label="OpenAI" delay={p2 + 20} />
          <BrandTile slug="openrouter" label="OpenRouter" delay={p2 + 30} />
        </div>
        <Subtitle delay={p2 + 44} size={24}>
          Pick any brain. Switch anytime. Never locked in.
        </Subtitle>
      </div>
    </SceneWrap>
  );
};

/* ── 8 · OUTRO (wordmark, platforms, repo link) ───────────────────────── */
export const OutroPromo: React.FC<{ scene: TimelineScene }> = ({ scene }) => {
  const o1 = line(scene, "outro_1").localStart;
  const o2 = line(scene, "outro_2").localStart;
  const o3 = line(scene, "outro_3").localStart;
  const frame = useCurrentFrame();
  const { fps } = useVideoConfig();
  const link = spring({ frame: frame - o3, fps, config: { damping: 200 } });
  const plat = spring({ frame: frame - o2, fps, config: { damping: 200 } });
  return (
    <SceneWrap>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 20 }}>
        <Mascot size={148} />
        <Title delay={6} size={68}>
          Personal Jarvis
        </Title>
        <Chip delay={o1 + 8}>★ Free &amp; open source · MIT</Chip>
        <div
          style={{
            fontFamily: FONT,
            fontSize: 24,
            fontWeight: 600,
            color: COLORS.textMuted,
            opacity: plat,
            transform: `translateY(${interpolate(plat, [0, 1], [12, 0])}px)`,
          }}
        >
          Windows · macOS · Linux — or a €5/mo server
        </div>
        <div
          style={{
            opacity: link,
            marginTop: 4,
            fontFamily: FONT,
            fontSize: 27,
            fontWeight: 700,
            color: COLORS.text,
            letterSpacing: 0.3,
          }}
        >
          github.com/<span style={{ color: COLORS.primary }}>PersonalJarvis</span>/PersonalJarvis
        </div>
      </div>
    </SceneWrap>
  );
};
