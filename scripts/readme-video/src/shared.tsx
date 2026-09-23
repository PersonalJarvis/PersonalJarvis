import type { CSSProperties, ReactNode } from "react";
import { AbsoluteFill, useCurrentFrame } from "remotion";
import { GigiMark } from "@app/components/GigiMark";
import { Box, CalendarDays, Mic, Minus, MoreHorizontal, PanelsTopLeft, Plus, RotateCw, Shapes, Square, Store, Sun, UserCircle2, Users, X } from "lucide-react";

export type Surface = "chat" | "agents" | "swarm" | "dictation" | "local";
const labels: Record<Surface, string> = { chat: "Jarvis", agents: "Agents", swarm: "Ultra Agent Swarm", dictation: "Jarvis Voice", local: "Local models" };
const muted = "hsl(var(--muted-foreground))";
const stroke = "1px solid hsl(var(--border))";
const row: CSSProperties = { display: "flex", alignItems: "center", gap: 10 };

/** Visual-only shell derived from Sidebar.tsx and TopBar.tsx. No live stores or APIs. */
export const AppShell = ({ children, active, title, subtitle }: { children: ReactNode; active: Surface; title?: string; subtitle?: string }) => {
  const frame = useCurrentFrame();
  const voiceState = active !== "chat" || frame < 72 ? "Ready" : frame < 166 ? "Listening" : frame < 204 ? "Thinking" : frame < 315 ? "Speaking" : "Listening";
  return (
  <AbsoluteFill className="dark" style={{ background: "#050505", color: "hsl(var(--foreground))", fontFamily: '"Inter Variable", Inter, sans-serif' }}>
    <header style={{ ...row, justifyContent: "space-between", padding: "22px 32px", height: 90 }}>
      <div style={{ ...row, gap: 16 }}><GigiMark size={38} /><span style={{ fontSize: 19, fontWeight: 600 }}>Personal Jarvis</span><span style={{ color: "#5c5c5c" }}>/</span><span style={{ fontSize: 25, fontWeight: 600 }}>{title || labels[active]}</span></div>
      <span style={{ fontSize: 16, color: "#A3A3A3" }}>{subtitle}</span>
    </header>
    <div data-demo-shell style={{ position: "absolute", top: 90, left: 28, right: 28, bottom: 38, overflow: "hidden", borderRadius: 12, border: stroke, background: "hsl(var(--background))", boxShadow: "0 18px 70px #0008" }}>
      <div style={{ ...row, height: 30, padding: "0 12px", background: "hsl(var(--sidebar))", borderBottom: stroke, fontSize: 11, color: muted }}><GigiMark size={16} />Personal Jarvis
        {active === "agents" && <div style={{ position: "absolute", left: "50%", transform: "translateX(-50%)", ...row, gap: 2, border: stroke, borderRadius: 6, padding: 2 }}><span style={{ padding: "2px 8px" }}>Map</span><span style={{ padding: "2px 8px", background: "hsl(var(--secondary))", color: "hsl(var(--foreground))", borderRadius: 4 }}>Agents</span></div>}
        <div style={{ marginLeft: "auto", ...row, gap: 24 }}><Minus size={11}/><Square size={10}/><X size={12}/></div></div>
      <div style={{ display: "flex", height: "calc(100% - 30px)" }}>
        <aside style={{ width: 220, flexShrink: 0, background: "hsl(var(--sidebar))", borderRight: stroke, display: "flex", flexDirection: "column", padding: "13px 10px" }}>
          <div style={{ ...row, padding: "0 6px 18px" }}><GigiMark size={28}/><div style={{ fontSize: 14, fontWeight: 500 }}>Jarvis<div style={{ ...row, gap: 6, fontSize: 11, fontWeight: 400, color: muted, marginTop: 3 }}><span style={{ width: 6, height: 6, borderRadius: "50%", background: "hsl(var(--success))" }}/>{voiceState}</div></div></div>
          {([
            ["chat", "New chat", Plus], ["agents", "Agents", Users], ["dictation", "Jarvis Voice", Mic],
            ["artifacts", "Artifacts", Shapes], ["scheduled", "Scheduled", CalendarDays], ["plugins", "Plugins / Skills / MCP", Box], ["more", "More", MoreHorizontal],
          ] as const).map(([id, label, Icon]) => <div key={id} style={{ ...row, padding: "10px 10px", fontSize: 12, borderRadius: 7, marginBottom: 3, color: id === active && id !== "chat" ? "hsl(var(--foreground))" : muted, background: id === active && id !== "chat" ? "hsl(var(--secondary))" : undefined }}><Icon size={16} strokeWidth={1.6}/>{label}</div>)}
          <div style={{ padding: "25px 10px 8px", fontSize: 11, color: muted }}>Recent</div><div style={{ padding: "4px 10px", fontSize: 11, color: muted }}>Example session</div>
          <div style={{ ...row, marginTop: "auto", padding: "12px 10px 0", borderTop: stroke, fontSize: 12, color: muted }}><UserCircle2 size={26}/>Profile<Store size={20} style={{ marginLeft: "auto" }}/></div>
        </aside>
        <div style={{ display: "flex", flexDirection: "column", flex: 1, minWidth: 0, minHeight: 0 }}>
          {active !== "agents" && <div style={{ ...row, height: 48, flexShrink: 0, padding: "0 16px", borderBottom: stroke }}>
            {active === "chat" && <GigiMark size={28}/>}
            <span style={{ fontSize: active === "chat" ? 14 : 16, fontWeight: 500 }}>{labels[active]}</span>
            {active === "chat" && <span style={{ fontFamily: "JetBrains Mono", fontSize: 10, letterSpacing: ".14em", color: muted, textTransform: "uppercase" }}>{voiceState}</span>}
            <div style={{ ...row, marginLeft: "auto", color: muted, fontSize: 12, gap: 14 }}>
              {active === "chat" && <>{[["Realtime", "OpenAI"], ["Model", "gpt-realtime"]].map(([label, value]) => <span key={label} style={{ ...row, gap: 6, border: stroke, borderRadius: 6, padding: "3px 8px", background: "hsl(var(--secondary))" }}><span style={{ textTransform: "uppercase", color: muted }}>{label}</span><span style={{ color: "hsl(var(--foreground))" }}>{value}</span></span>)}</>}
              <Sun size={15}/>{active === "chat" && <span style={row}><PanelsTopLeft size={15}/>Own window</span>}<span style={row}><RotateCw size={15}/>Restart</span>
            </div>
          </div>}
          <main style={{ flex: 1, minHeight: 0, position: "relative", display: "flex", flexDirection: "column", overflow: "hidden" }}>{children}</main>
        </div>
      </div>
    </div>
    <footer style={{ position: "absolute", left: 34, right: 34, bottom: 12, display: "flex", justifyContent: "space-between", color: "#8B8B8B", fontSize: 10, letterSpacing: ".035em" }}><span>UI recreation from app source · illustrative session</span><span>{active === "swarm" ? "ULTRA AGENT SWARM · DEVELOPMENT PREVIEW" : "PERSONAL JARVIS"}</span></footer>
  </AbsoluteFill>
  );
};
