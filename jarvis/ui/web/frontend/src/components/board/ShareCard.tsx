import { forwardRef, type CSSProperties } from "react";

import { REPO_LABEL, type ShareStats } from "@/lib/shareImage";
import { useT } from "@/i18n";

// ── Palette ───────────────────────────────────────────────────────────
// A JARVIS-style HUD readout: amber-gold instrument lines on deep ink,
// a cool cyan secondary, monospace data. Deliberately NOT the generic
// "dark card + big number" look.
const INK = "#07080a";
const AMBER = "#ffcf2b";
const CYAN = "#5ad1ff";
const MONO = '"JetBrains Mono", ui-monospace, "SF Mono", Menlo, monospace';

export interface ShareCardProps extends ShareStats {
  handle?: string;
}

/**
 * The 1080×1080 shareable stats card — a HUD / instrument-readout design.
 * Pure presentation, rendered at full intrinsic size so it captures crisply.
 * All effects (radial glows, blueprint grid, corner brackets, tick ruler,
 * segmented meter) are plain CSS/SVG so html-to-image reproduces them 1:1.
 */
export const ShareCard = forwardRef<HTMLDivElement, ShareCardProps>(
  function ShareCard(
    { userWords, jarvisWords, conversationHours, sessionCount, longestStreak, handle },
    ref,
  ) {
    const t = useT();
    const nf = (n: number) => n.toLocaleString();
    const SEGMENTS = 28;
    const filled = Math.max(2, Math.min(SEGMENTS, Math.round((longestStreak / 30) * SEGMENTS)));

    return (
      <div
        ref={ref}
        data-testid="share-card"
        style={{
          position: "relative",
          width: 1080,
          height: 1080,
          boxSizing: "border-box",
          background: INK,
          color: "#fff",
          fontFamily: MONO,
          overflow: "hidden",
        }}
      >
        {/* Atmosphere: amber glow top-left, cyan whisper bottom-right */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            background:
              "radial-gradient(60% 50% at 18% 8%, rgba(255,207,43,0.16), transparent 60%)," +
              "radial-gradient(55% 45% at 92% 100%, rgba(90,209,255,0.10), transparent 60%)",
          }}
        />
        {/* Blueprint grid */}
        <div
          style={{
            position: "absolute",
            inset: 0,
            backgroundImage:
              "linear-gradient(rgba(255,255,255,0.035) 1px, transparent 1px)," +
              "linear-gradient(90deg, rgba(255,255,255,0.035) 1px, transparent 1px)",
            backgroundSize: "60px 60px",
          }}
        />
        {/* Corner registration brackets */}
        <Bracket pos="tl" />
        <Bracket pos="tr" />
        <Bracket pos="bl" />
        <Bracket pos="br" />

        {/* Content */}
        <div
          style={{
            position: "relative",
            height: "100%",
            boxSizing: "border-box",
            padding: 96,
            display: "flex",
            flexDirection: "column",
            justifyContent: "space-between",
          }}
        >
          {/* ── Header ───────────────────────────────────────────── */}
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 20 }}>
              <span
                style={{
                  display: "flex",
                  alignItems: "center",
                  justifyContent: "center",
                  width: 64,
                  height: 64,
                  borderRadius: "50%",
                  border: `1.5px solid ${AMBER}`,
                  background: "rgba(255,207,43,0.06)",
                  boxShadow: "0 0 24px -6px rgba(255,207,43,0.5)",
                }}
              >
                <img src="/jarvis-mark-256.png" width={38} height={38} alt="" style={{ display: "block" }} />
              </span>
              <span
                style={{
                  fontSize: 26,
                  fontWeight: 700,
                  letterSpacing: "0.34em",
                  color: "rgba(255,255,255,0.92)",
                }}
              >
                {t("board_view.share.card.brand")}
              </span>
            </div>
            <span style={{ fontSize: 18, letterSpacing: "0.26em", color: AMBER }}>
              {"// ALL-TIME"}
            </span>
          </div>

          {/* ── Hero ─────────────────────────────────────────────── */}
          <div>
            <div
              style={{
                display: "flex",
                alignItems: "center",
                gap: 16,
                fontSize: 26,
                fontWeight: 500,
                letterSpacing: "0.32em",
                textTransform: "uppercase",
                color: AMBER,
              }}
            >
              <span style={{ width: 40, height: 2, background: AMBER }} />
              {t("board_view.share.card.hero_label")}
            </div>

            <div
              style={{
                marginTop: 14,
                fontSize: 232,
                fontWeight: 800,
                lineHeight: 0.9,
                letterSpacing: "-0.04em",
                color: "#fff",
                textShadow: "0 0 60px rgba(255,207,43,0.22)",
              }}
            >
              {nf(userWords)}
            </div>

            {/* tick ruler under the hero number */}
            <div
              style={{
                marginTop: 22,
                height: 16,
                backgroundImage: `repeating-linear-gradient(90deg, ${AMBER} 0 2px, transparent 2px 26px)`,
                opacity: 0.55,
              }}
            />

            {/* secondary readouts */}
            <div style={{ marginTop: 40, display: "flex", flexDirection: "column", gap: 14 }}>
              <div style={{ fontSize: 38, color: "rgba(255,255,255,0.92)" }}>
                <span style={{ fontWeight: 800, color: CYAN }}>{nf(jarvisWords)}</span>{" "}
                <span style={{ color: "rgba(255,255,255,0.62)" }}>
                  {t("board_view.share.card.jarvis_suffix")}
                </span>
              </div>
              <div style={{ fontSize: 32, color: "rgba(255,255,255,0.5)", letterSpacing: "0.02em" }}>
                {t("board_view.share.card.talk_line")
                  .replace("{0}", conversationHours.toFixed(1))
                  .replace("{1}", nf(sessionCount))}
              </div>
            </div>
          </div>

          {/* ── Streak meter + footer ────────────────────────────── */}
          <div style={{ display: "flex", flexDirection: "column", gap: 30 }}>
            <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
              <div
                style={{
                  fontSize: 20,
                  letterSpacing: "0.24em",
                  textTransform: "uppercase",
                  color: "rgba(255,255,255,0.55)",
                }}
              >
                {t("board_view.share.card.streak").replace("{0}", nf(longestStreak))}
              </div>
              <div style={{ display: "flex", gap: 5 }}>
                {Array.from({ length: SEGMENTS }).map((_, i) => (
                  <span
                    key={i}
                    style={{
                      flex: 1,
                      height: 16,
                      background: i < filled ? AMBER : "rgba(255,255,255,0.09)",
                    }}
                  />
                ))}
              </div>
            </div>

            <div
              style={{
                borderTop: "1px solid rgba(255,255,255,0.1)",
                paddingTop: 26,
                display: "flex",
                alignItems: "center",
                justifyContent: "space-between",
                fontSize: 24,
                color: "rgba(255,255,255,0.55)",
              }}
            >
              <span>
                <span style={{ color: AMBER }}>{"▮ "}</span>
                {REPO_LABEL}
              </span>
              {handle && <span style={{ color: "rgba(255,255,255,0.7)" }}>{`@${handle}`}</span>}
            </div>
          </div>
        </div>
      </div>
    );
  },
);

// L-shaped HUD bracket pinned to a corner.
function Bracket({ pos }: { pos: "tl" | "tr" | "bl" | "br" }) {
  const size = 56;
  const off = 52;
  const v: Record<string, CSSProperties> = {
    tl: { top: off, left: off, borderTop: `2px solid ${AMBER}`, borderLeft: `2px solid ${AMBER}` },
    tr: { top: off, right: off, borderTop: `2px solid ${AMBER}`, borderRight: `2px solid ${AMBER}` },
    bl: { bottom: off, left: off, borderBottom: `2px solid ${AMBER}`, borderLeft: `2px solid ${AMBER}` },
    br: { bottom: off, right: off, borderBottom: `2px solid ${AMBER}`, borderRight: `2px solid ${AMBER}` },
  };
  return (
    <div
      style={{ position: "absolute", width: size, height: size, opacity: 0.85, ...v[pos] }}
    />
  );
}
