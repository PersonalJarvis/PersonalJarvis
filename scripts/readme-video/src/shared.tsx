import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, useCurrentFrame, useVideoConfig } from "remotion";
import { GigiMark } from "@app/components/GigiMark";
import { ArrowLeft, ArrowRight, Minus, PanelLeftOpen, PanelsTopLeft, RotateCw, Square, Sun, X } from "lucide-react";

export type Surface = "chat" | "agents" | "swarm" | "dictation" | "local";
const labels: Record<Surface, string> = { chat: "Jarvis", agents: "Agents", swarm: "Ultra Agent Swarm", dictation: "Jarvis Voice", local: "Local models" };
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
export const AppShell = ({ children, active, voiceState = "Ready" }: Props) => (
  <AbsoluteFill className="dark" data-demo-shell data-readme-section={active}
    style={{ background: "hsl(var(--background))", color: "hsl(var(--foreground))", fontFamily: '"Inter Variable", Inter, sans-serif' }}>
    <div style={{ ...row, height: 28, flexShrink: 0, padding: "0 10px", background: "hsl(var(--sidebar))", color: muted, fontSize: 11 }}>
      <PanelLeftOpen size={13}/><ArrowLeft size={13}/><ArrowRight size={13} opacity={0.45}/>
      {active === "agents" && <div style={{ position: "absolute", left: "50%", transform: "translateX(-50%)", ...row, gap: 8 }}>
        <div style={{ ...row, gap: 2, border: "1px solid hsl(var(--border))", borderRadius: 5, padding: 2 }}>
          <span style={{ padding: "1px 7px" }}>Map</span>
          <span style={{ padding: "1px 7px", background: "hsl(var(--secondary))", color: "hsl(var(--foreground))", borderRadius: 3 }}>Agents</span>
        </div>
        <span style={{ border: "1px solid hsl(var(--border))", borderRadius: 5, padding: "3px 7px" }}>Communications station</span>
      </div>}
      {active === "swarm" && <span style={{ position: "absolute", left: "50%", transform: "translateX(-50%)", fontSize: 10, letterSpacing: ".04em" }}>ULTRA AGENT SWARM · DEVELOPMENT PREVIEW</span>}
      <div style={{ marginLeft: "auto", ...row, gap: 22 }}><Sun size={13}/><RotateCw size={13}/><Minus size={12}/><Square size={11}/><X size={13}/></div>
    </div>
    {active === "chat" && <div style={{ ...row, height: 48, flexShrink: 0, padding: "0 16px", borderBottom: "1px solid hsl(var(--border))" }}>
      <GigiMark size={28}/><span style={{ fontSize: 14, fontWeight: 500 }}>{labels[active]}</span>
      <span style={{ fontFamily: "JetBrains Mono", fontSize: 10, letterSpacing: ".14em", color: muted, textTransform: "uppercase" }}>{voiceState}</span>
      <div style={{ ...row, marginLeft: "auto", color: muted, fontSize: 12, gap: 14 }}>
        {["Realtime · OpenAI", "Model · gpt-realtime"].map(label => <span key={label} style={{ padding: "3px 8px", background: "hsl(var(--secondary))", border: "1px solid hsl(var(--border))", borderRadius: 6 }}>{label}</span>)}
        <span style={row}><PanelsTopLeft size={15}/>Own window</span>
      </div>
    </div>}
    <main style={{ flex: 1, minHeight: 0, minWidth: 0, position: "relative", display: "flex", flexDirection: "column", overflow: "hidden" }}>{children}</main>
  </AbsoluteFill>
);
