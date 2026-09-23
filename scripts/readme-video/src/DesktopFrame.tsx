import type { ReactNode } from "react";
import { AbsoluteFill } from "remotion";
import { AppWindow, ArrowLeft, ArrowRight, CalendarClock, CircleUserRound, LayoutGrid, Mic, Minus, MoreHorizontal, PanelLeftClose, Plus, RotateCw, Shapes, Square, Store, Sun, Users, X } from "lucide-react";
import { GigiMark } from "@app/components/GigiMark";

/** Windowed desktop reference with synthetic history, never a private capture. */
export const DesktopFrame = ({ children, status, caption }: { children: ReactNode; status: string; caption: ReactNode }) => (
  <AbsoluteFill className="dark" data-demo-shell style={{ background: "#17191c", color: "#fafafa", fontFamily: '"Inter Variable", Inter, sans-serif' }}>
    <div style={{ width: 1280, height: 696, transform: "scale(1.5)", transformOrigin: "top left", position: "relative" }}>
      <div style={{ position: "absolute", inset: "16px 18px 66px", border: "1px solid #444", borderRadius: 10, overflow: "hidden", background: "#101010", boxShadow: "0 12px 32px #0008", display: "flex", flexDirection: "column" }}>
        <div style={{ height: 32, flexShrink: 0, display: "flex", alignItems: "center", color: "#a1a1a1" }}>
          {[PanelLeftClose, ArrowLeft, ArrowRight].map((Icon, index) => <span key={index} style={{ width: 32, display: "grid", placeItems: "center" }}><Icon size={16} /></span>)}
          <span style={{ margin: "auto", fontSize: 10, color: "#737373" }}>Personal Jarvis</span>
          {[Sun, AppWindow, RotateCw, Minus, Square, X].map((Icon, index) => <span key={index} style={{ width: 34, display: "grid", placeItems: "center" }}><Icon size={15} /></span>)}
        </div>
        <div style={{ flex: 1, minHeight: 0, display: "flex" }}>
          <aside style={{ width: 270, flexShrink: 0, padding: "16px 14px 10px", display: "flex", flexDirection: "column" }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginBottom: 22 }}>
              <GigiMark size={32} />
              <div><div style={{ fontSize: 17, fontWeight: 600 }}>George</div><div style={{ fontSize: 14, color: "#a1a1a1", marginTop: 2 }}><span style={{ color: "#22c55e" }}>●</span> {status}</div></div>
            </div>
            {[[Plus, "New chat"], [Users, "Agents"], [Mic, "George Voice"], [Shapes, "Artifacts"], [CalendarClock, "Scheduled"], [LayoutGrid, "Plugins / Skills / MCP"], [MoreHorizontal, "More"]].map(([Icon, label]) => {
              const Symbol = Icon as typeof Plus;
              return <div key={String(label)} style={{ display: "flex", alignItems: "center", gap: 12, height: 39, color: "#a1a1a1", fontSize: 15, fontWeight: 500 }}><Symbol size={17} />{String(label)}</div>;
            })}
            <div style={{ marginTop: 22, fontSize: 13, color: "#737373" }}>Recent</div>
            {["Plan a small project", "Ideas for the weekly brief", "Notes for my research agent"].map(label => <div key={label} style={{ marginTop: 15, fontSize: 13, color: "#d4d4d4" }}>{label}</div>)}
            <div style={{ display: "flex", alignItems: "center", gap: 10, marginTop: "auto", borderTop: "1px solid #252525", paddingTop: 12, color: "#a1a1a1", fontSize: 14 }}><CircleUserRound size={25} />Profile<Store size={19} style={{ marginLeft: "auto" }} /></div>
          </aside>
          <main style={{ flex: 1, minWidth: 0, position: "relative", borderLeft: "1px solid #292929", borderTop: "1px solid #292929", borderTopLeftRadius: 18, background: "#0a0a0a", overflow: "hidden" }}>{children}</main>
        </div>
      </div>
      <div style={{ position: "absolute", bottom: 21, left: 0, right: 0, textAlign: "center", fontSize: 18, color: "#fafafa", height: 26 }}>{caption}</div>
    </div>
  </AbsoluteFill>
);
