/**
 * Desk monitors that show the seated agent's live chat. A click on a screen
 * zooms the camera into it and then opens that agent's chat view.
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { CanvasTexture, SRGBColorSpace, type Mesh } from "three";
import type { SocietyAgent } from "../data";
import type { DeskSlot, Point } from "./officeLayout";
import type { DeskChat } from "./useDeskChats";
import type { ChatLineKind } from "./deskChat";
import { seatedAtDesk } from "./walkerRegistry";

const W = 512, H = 296;
/** Same place as the instanced screen, a hair in front of it. */
const SCREEN_Y = 1.18, SCREEN_Z = -0.2095, SCREEN_W = 0.66, SCREEN_H = 0.38;

const PREFIX: Record<ChatLineKind, { mark: string; colour: string }> = {
  user: { mark: "›", colour: "#93c5fd" },
  agent: { mark: "●", colour: "#e2e8f0" },
  tool: { mark: "⚙", colour: "#fcd34d" },
  notice: { mark: "i", colour: "#a7f3d0" },
  error: { mark: "!", colour: "#fca5a5" },
};

function wrap(ctx: CanvasRenderingContext2D, text: string, width: number): string[] {
  const words = text.split(" ");
  const rows: string[] = [];
  let row = "";
  for (const word of words) {
    const next = row ? `${row} ${word}` : word;
    if (ctx.measureText(next).width > width && row) { rows.push(row); row = word; } else row = next;
  }
  if (row) rows.push(row);
  return rows;
}

export function drawChatScreen(ctx: CanvasRenderingContext2D, agent: Pick<SocietyAgent, "name" | "state">, chat: DeskChat | undefined): void {
  ctx.fillStyle = "#0f1420";
  ctx.fillRect(0, 0, W, H);
  // Title bar: the agent and its state, like a chat window header.
  ctx.fillStyle = "#1c2433";
  ctx.fillRect(0, 0, W, 34);
  ctx.fillStyle = agent.state === "working" ? "#4ade80" : agent.state === "waiting" ? "#fbbf24" : "#94a3b8";
  ctx.beginPath(); ctx.arc(18, 17, 6, 0, Math.PI * 2); ctx.fill();
  ctx.fillStyle = "#f8fafc";
  ctx.font = "600 17px system-ui, -apple-system, 'Segoe UI', sans-serif";
  ctx.textBaseline = "middle";
  ctx.fillText(agent.name.length > 34 ? `${agent.name.slice(0, 33)}…` : agent.name, 32, 18);
  ctx.font = "15px ui-monospace, 'Cascadia Mono', Menlo, Consolas, monospace";
  ctx.textBaseline = "alphabetic";
  const lineH = 19, left = 12, textLeft = 30, maxW = W - textLeft - 10;
  // Lay out from the newest line upwards, so the latest message is always visible.
  const rows: { colour: string; mark: string; text: string }[] = [];
  const lines = chat?.lines ?? [];
  if (lines.length === 0) rows.push({ colour: "#64748b", mark: "…", text: "" });
  for (const line of lines) {
    const p = PREFIX[line.kind];
    wrap(ctx, line.text, maxW).slice(0, 4).forEach((text, i) => rows.push({ colour: p.colour, mark: i === 0 ? p.mark : "", text }));
  }
  const fit = Math.floor((H - 44) / lineH);
  const visible = rows.slice(-fit);
  visible.forEach((row, i) => {
    const y = 44 + (i + 1) * lineH - 5;
    ctx.fillStyle = row.colour;
    ctx.fillText(row.mark, left, y);
    ctx.fillText(row.text, textLeft, y);
  });
  // A blinking-style caret block at the bottom while working.
  if (agent.state === "working") { ctx.fillStyle = "#4ade80"; ctx.fillRect(textLeft, H - 12, 9, 4); }
}

function Screen({ desk, agent, chat, onOpen }: {
  desk: DeskSlot; agent: SocietyAgent; chat: DeskChat | undefined; onOpen: (agentId: string, screen: Point & { y: number }, facing: number) => void;
}) {
  const mesh = useRef<Mesh>(null);
  const turn = desk.facing === "north" ? 0 : Math.PI;
  const surface = useMemo(() => {
    const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
    const ctx = canvas?.getContext("2d") ?? null;
    if (!canvas || !ctx) return null;
    canvas.width = W; canvas.height = H;
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.anisotropy = 4;
    return { ctx, texture };
  }, []);
  useEffect(() => () => surface?.texture.dispose(), [surface]);
  useEffect(() => {
    if (!surface) return;
    drawChatScreen(surface.ctx, agent, chat);
    surface.texture.needsUpdate = true;
  }, [surface, agent.name, agent.state, chat]);
  useFrame(() => {
    if (mesh.current) mesh.current.visible = seatedAtDesk.has(agent.agentId);
  });
  if (!surface) return null;
  // World position of the screen centre, for the zoom.
  const sx = desk.x + Math.sin(turn) * SCREEN_Z, sz = desk.z + Math.cos(turn) * SCREEN_Z;
  const click = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 6) return;
    event.stopPropagation();
    onOpen(agent.agentId, { x: sx, y: SCREEN_Y, z: sz }, turn);
  };
  return (
    <group position={[desk.x, 0, desk.z]} rotation={[0, turn, 0]}>
      <mesh ref={mesh} position={[0, SCREEN_Y, SCREEN_Z]} visible={false} onClick={click}
        onPointerOver={() => { document.body.style.cursor = "zoom-in"; }} onPointerOut={() => { document.body.style.cursor = ""; }}>
        <planeGeometry args={[SCREEN_W, SCREEN_H]} />
        <meshBasicMaterial map={surface.texture} toneMapped={false} />
      </mesh>
    </group>
  );
}

export function LiveMonitors({ desks, agents, chats, onOpen }: {
  desks: DeskSlot[]; agents: ReadonlyMap<string, SocietyAgent>; chats: ReadonlyMap<string, DeskChat>;
  onOpen: (agentId: string, screen: Point & { y: number }, facing: number) => void;
}) {
  return (
    <group>
      {desks.map((desk) => {
        const agent = desk.agentId ? agents.get(desk.agentId) : undefined;
        return agent ? <Screen key={desk.id} desk={desk} agent={agent} chat={chats.get(agent.agentId)} onOpen={onOpen} /> : null;
      })}
    </group>
  );
}
