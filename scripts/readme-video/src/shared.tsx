import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { AppWindow, ArrowLeft, ArrowRight, Minus, PanelLeftOpen, RotateCw, Square, Sun, X } from "lucide-react";

export type Surface = "chat" | "agents" | "swarm" | "dictation" | "local";
const muted = "hsl(var(--muted-foreground))";
const row: CSSProperties = { display: "flex", alignItems: "center", gap: 10 };

/** Keep authored motion continuous while retiming all scenes to nine seconds. */
export function useDemoFrame() {
  const frame = useCurrentFrame();
  const { durationInFrames } = useVideoConfig();
  return Math.max(0, Math.min(449, frame * 449 / Math.max(1, durationInFrames - 1)));
}

type Props = {
  children: ReactNode;
  active: Surface;
  title?: string;
  subtitle?: string;
  voiceState?: string;
};

/**
 * Section-only viewport: matches the reference with global navigation closed.
 * The Agents roster and Options pane belong to the section and remain visible.
 * Editorial copy and the illustrative-session disclosure live in the README.
 */
export const AppShell = ({ children, active }: Props) => (
  <AbsoluteFill className="dark" data-demo-shell data-readme-section={active}
    style={{ background: "hsl(var(--sidebar))", color: "hsl(var(--foreground))", fontFamily: '"Inter Variable", Inter, sans-serif' }}>
    <div style={{ ...row, height: 32, flexShrink: 0, gap: 0, color: muted, fontSize: 11 }}>
      {[PanelLeftOpen, ArrowLeft, ArrowRight].map((Icon, index) => <span key={index} style={{ display: "grid", placeItems: "center", width: 32, height: 32, opacity: index === 2 ? 0.4 : 1 }}><Icon size={16}/></span>)}
      {active === "agents" && <div style={{ position: "absolute", left: "50%", transform: "translateX(-50%)", ...row, gap: 8 }}>
        <div style={{ ...row, gap: 2, border: "1px solid hsl(var(--border))", borderRadius: 5, padding: 2 }}>
          <span style={{ padding: "1px 7px" }}>Map</span>
          <span style={{ padding: "1px 7px", background: "hsl(var(--secondary))", color: "hsl(var(--foreground))", borderRadius: 3 }}>Agents</span>
        </div>
        <span style={{ border: "1px solid hsl(var(--border))", borderRadius: 5, padding: "3px 7px" }}>Communications station</span>
      </div>}
      {active === "swarm" && <span style={{ position: "absolute", left: "50%", transform: "translateX(-50%)", fontSize: 10, letterSpacing: ".04em" }}>ULTRA AGENT SWARM · DEVELOPMENT PREVIEW</span>}
      <div style={{ marginLeft: "auto", ...row, gap: 0 }}>
        <span style={{ display: "grid", placeItems: "center", width: 32, height: 32 }}><Sun size={16}/></span>
        {active === "chat" && <span style={{ display: "grid", placeItems: "center", width: 32, height: 32 }}><AppWindow size={16}/></span>}
        <span style={{ display: "grid", placeItems: "center", width: 32, height: 32 }}><RotateCw size={16}/></span>
        {[Minus, Square, X].map((Icon, index) => <span key={index} style={{ display: "grid", placeItems: "center", width: 40, height: 32 }}><Icon size={index === 1 ? 14 : 16}/></span>)}
      </div>
    </div>
    {/* HomeHeader is now null. The native caption owns all window actions. */}
    <main style={{ flex: 1, minHeight: 0, minWidth: 0, position: "relative", display: "flex", flexDirection: "column", overflow: "hidden", background: "hsl(var(--background))", ...(active !== "agents" ? { borderTop: "1px solid hsl(var(--border))", borderLeft: "1px solid hsl(var(--border))", borderTopLeftRadius: 12 } : {}) }}>{children}</main>
  </AbsoluteFill>
);
