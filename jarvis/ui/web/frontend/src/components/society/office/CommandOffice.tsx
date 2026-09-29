/**
 * Mission Control, the coding floor's own office: a walnut slat wall with a
 * live video wall, and a desk with three live monitors and an executive chair.
 * Working it (E in the room, or a click on any of its screens) opens the
 * Mission Control panel: start new coding agents, brief several at once.
 *
 * Every screen is a live board of the floor, drawn from the same shared pane
 * poll the figures ride: the video wall shows each agent as a tile; the desk
 * shows the workspaces (left), the floor's counts and a "New agent" button
 * (centre) and the latest prompts (right).
 *
 * Pieces are built in local space centred on the origin, front facing +z, and
 * stay inside their FURNITURE_SIZE box.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import { CanvasTexture, CylinderGeometry, MeshBasicMaterial, MeshStandardMaterial, SRGBColorSpace } from "three";
import { useT } from "@/i18n";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import type { AgentRunState } from "../data";
import { Box, MAT, matte, Rounded } from "./OfficeFurniture";
import { paneOccupants } from "./codingFloor";
import { COMMAND_DESK, COMMAND_DESK_OFFSET, type Furniture, type FurnitureKind } from "./officeLayout";
import { useOfficeStore } from "./officeStore";

// ---------------------------------------------------------------------------
// Live data
// ---------------------------------------------------------------------------

export interface FloorAgent {
  name: string;
  state: AgentRunState;
  stateKey: string;
  cli: string;
  workspace: string;
  prompt: string;
  promptAt: number | null;
  topic: string;
}

export interface FloorData {
  counts: { total: number; working: number; waiting: number; idle: number };
  agents: FloorAgent[];
  workspaces: { name: string; total: number; working: number }[];
}

function useFloorData(): FloorData {
  const panes = useWorkspacePanesStore((s) => s.panes);
  return useMemo(() => {
    const counts = { total: 0, working: 0, waiting: 0, idle: 0 };
    const agents: FloorAgent[] = [];
    const byWorkspace = new Map<string, { name: string; total: number; working: number }>();
    for (const { agent, pane, stateKey } of paneOccupants(panes)) {
      counts.total += 1;
      if (agent.state === "working") counts.working += 1;
      else if (agent.state === "waiting") counts.waiting += 1;
      else counts.idle += 1;
      agents.push({
        name: agent.name, state: agent.state, stateKey, cli: pane.display_name || pane.agent,
        workspace: pane.workspace_name, prompt: pane.last_prompt.trim(), promptAt: pane.last_prompt_at, topic: pane.recap.trim(),
      });
      const ws = byWorkspace.get(pane.workspace_id) ?? { name: pane.workspace_name, total: 0, working: 0 };
      ws.total += 1;
      if (agent.state === "working") ws.working += 1;
      byWorkspace.set(pane.workspace_id, ws);
    }
    return { counts, agents, workspaces: [...byWorkspace.values()] };
  }, [panes]);
}

/** Everything the screens say, in the viewer's language. */
export interface ScreenLabels {
  title: string; agents: string; start: string; workspaces: string; activity: string; none: string;
  working: string; waiting: string; idle: string;
  state: (key: string) => string;
  since: (at: number | null) => string;
}

function useScreenLabels(): ScreenLabels {
  const t = useT();
  return {
    title: t("society.office.cp_mission"),
    agents: t("society.office.mission_screen_agents"),
    start: t("society.office.mission_screen_start"),
    workspaces: t("society.office.mission_screen_workspaces"),
    activity: t("society.office.mission_screen_activity"),
    none: t("society.office.mission_screen_none"),
    working: t("society.office.state_working"),
    waiting: t("society.office.state_waiting"),
    idle: t("society.office.state_idle"),
    state: (key) => t(`society.office.pane_state_${key}`),
    since: (at) => {
      if (!at) return "";
      const s = Math.max(0, Date.now() / 1000 - at);
      if (s < 60) return t("society.office.since_now");
      if (s < 3600) return t("society.office.since_minutes").replace("{0}", String(Math.floor(s / 60)));
      if (s < 86400) return t("society.office.since_hours").replace("{0}", String(Math.floor(s / 3600)));
      return t("society.office.since_days").replace("{0}", String(Math.floor(s / 86400)));
    },
  };
}

// ---------------------------------------------------------------------------
// Drawing: one quiet dark UI, white type, colour only for state.
// ---------------------------------------------------------------------------

const INK = "#f4f6f8", MUTED = "#8b929c", FAINT = "rgba(255,255,255,0.07)";
const FONT = "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif";
const STATE: Record<AgentRunState, string> = { working: "#4ade80", waiting: "#fbbf24", idle: "#8b929c", paused: "#8b929c" };

type Ctx = CanvasRenderingContext2D;

function ground(ctx: Ctx, w: number, h: number): void {
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, "#15181d");
  bg.addColorStop(1, "#0b0d10");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";
}

/** `text` cut with an ellipsis so it fits `max` pixels in the current font. */
function fit(ctx: Ctx, text: string, max: number): string {
  if (ctx.measureText(text).width <= max) return text;
  let lo = 0, hi = text.length;
  while (lo < hi) {
    const mid = Math.ceil((lo + hi) / 2);
    if (ctx.measureText(`${text.slice(0, mid)}…`).width <= max) lo = mid; else hi = mid - 1;
  }
  return `${text.slice(0, lo)}…`;
}

function dot(ctx: Ctx, x: number, y: number, r: number, colour: string): void {
  ctx.fillStyle = colour;
  ctx.beginPath(); ctx.arc(x, y, r, 0, Math.PI * 2); ctx.fill();
}

function heading(ctx: Ctx, text: string, x: number, y: number): void {
  ctx.fillStyle = MUTED;
  ctx.font = `600 30px ${FONT}`;
  ctx.fillText(text, x, y);
}

/** Centre monitor: the one big number, three state rows and the "New agent" button. */
export function drawBoard(ctx: Ctx, w: number, h: number, data: FloorData, l: ScreenLabels): void {
  ground(ctx, w, h);
  const pad = 56, { counts } = data;
  heading(ctx, l.title, pad, pad + 26);
  dot(ctx, w - pad - 8, pad + 16, 8, STATE.working);
  ctx.fillStyle = INK;
  ctx.font = `700 200px ${FONT}`;
  ctx.fillText(String(counts.total), pad - 8, pad + 250);
  ctx.fillStyle = MUTED;
  ctx.font = `500 30px ${FONT}`;
  ctx.fillText(fit(ctx, l.agents, w * 0.5 - pad), pad, pad + 298);
  const x0 = w * 0.55;
  ([[l.working, counts.working, STATE.working], [l.waiting, counts.waiting, STATE.waiting], [l.idle, counts.idle, STATE.idle]] as const)
    .forEach(([label, n, colour], i) => {
      const y = pad + 90 + i * 78;
      ctx.fillStyle = FAINT;
      ctx.fillRect(x0, y - 50, w - pad - x0, 2);
      dot(ctx, x0 + 10, y - 12, 9, colour);
      ctx.fillStyle = INK;
      ctx.font = `500 32px ${FONT}`;
      ctx.fillText(fit(ctx, label, w - pad - x0 - 110), x0 + 36, y);
      ctx.textAlign = "right";
      ctx.font = `700 40px ${FONT}`;
      ctx.fillText(String(n), w - pad, y + 2);
      ctx.textAlign = "left";
    });
  const bw = 330, bh = 76, bx = pad, by = h - pad - bh;
  ctx.fillStyle = INK;
  ctx.beginPath(); ctx.roundRect(bx, by, bw, bh, 18); ctx.fill();
  ctx.strokeStyle = "#0b0d10";
  ctx.lineWidth = 5;
  ctx.lineCap = "round";
  const px = bx + 44, py = by + bh / 2;
  ctx.beginPath(); ctx.moveTo(px - 13, py); ctx.lineTo(px + 13, py); ctx.moveTo(px, py - 13); ctx.lineTo(px, py + 13); ctx.stroke();
  ctx.fillStyle = "#0b0d10";
  ctx.font = `600 32px ${FONT}`;
  ctx.fillText(fit(ctx, l.start, bw - 100), bx + 80, py + 11);
}

/** Left monitor: every workspace with how many of its agents work. */
export function drawWorkspaces(ctx: Ctx, w: number, h: number, data: FloorData, l: ScreenLabels): void {
  ground(ctx, w, h);
  const pad = 56;
  heading(ctx, l.workspaces, pad, pad + 26);
  if (data.workspaces.length === 0) { ctx.fillStyle = MUTED; ctx.font = `500 32px ${FONT}`; ctx.fillText(l.none, pad, pad + 120); return; }
  const rows = data.workspaces.slice(0, 5), rowH = (h - pad * 2 - 60) / 5;
  rows.forEach((ws, i) => {
    const y = pad + 110 + i * rowH;
    ctx.fillStyle = INK;
    ctx.font = `600 34px ${FONT}`;
    ctx.fillText(fit(ctx, ws.name, w * 0.5), pad, y);
    ctx.textAlign = "right";
    ctx.fillStyle = MUTED;
    ctx.font = `500 30px ${FONT}`;
    ctx.fillText(`${ws.working} / ${ws.total}`, w - pad, y);
    ctx.textAlign = "left";
    const bx = pad, by = y + 18, bw = w - pad * 2;
    ctx.fillStyle = FAINT;
    ctx.beginPath(); ctx.roundRect(bx, by, bw, 10, 5); ctx.fill();
    if (ws.working > 0) {
      ctx.fillStyle = STATE.working;
      ctx.beginPath(); ctx.roundRect(bx, by, Math.max(10, (bw * ws.working) / ws.total), 10, 5); ctx.fill();
    }
  });
}

/** Right monitor: the latest prompts, newest first. */
export function drawActivity(ctx: Ctx, w: number, h: number, data: FloorData, l: ScreenLabels): void {
  ground(ctx, w, h);
  const pad = 56;
  heading(ctx, l.activity, pad, pad + 26);
  const recent = data.agents.filter((a) => a.prompt).sort((a, b) => (b.promptAt ?? 0) - (a.promptAt ?? 0)).slice(0, 4);
  if (recent.length === 0) { ctx.fillStyle = MUTED; ctx.font = `500 32px ${FONT}`; ctx.fillText(l.none, pad, pad + 120); return; }
  const rowH = (h - pad * 2 - 50) / 4;
  recent.forEach((a, i) => {
    const y = pad + 100 + i * rowH;
    if (i > 0) { ctx.fillStyle = FAINT; ctx.fillRect(pad, y - 44, w - pad * 2, 2); }
    dot(ctx, pad + 8, y - 10, 8, STATE[a.state]);
    const age = l.since(a.promptAt);
    ctx.font = `500 26px ${FONT}`;
    const ageW = ctx.measureText(age).width;
    ctx.fillStyle = INK;
    ctx.font = `600 30px ${FONT}`;
    ctx.fillText(fit(ctx, a.name, w - pad * 2 - ageW - 60), pad + 30, y);
    ctx.textAlign = "right";
    ctx.fillStyle = MUTED;
    ctx.font = `500 26px ${FONT}`;
    ctx.fillText(age, w - pad, y);
    ctx.textAlign = "left";
    ctx.font = `400 26px ${FONT}`;
    ctx.fillText(fit(ctx, a.prompt.replace(/\s+/g, " "), w - pad * 2 - 30), pad + 30, y + 36);
  });
}

/** The video wall: a header with the time, then one tile per agent on the floor. */
export function drawVideoWall(ctx: Ctx, w: number, h: number, data: FloorData, l: ScreenLabels, clock: string): void {
  ground(ctx, w, h);
  const pad = 48;
  ctx.fillStyle = INK;
  ctx.font = `700 44px ${FONT}`;
  ctx.fillText(l.title, pad, pad + 38);
  ctx.textAlign = "right";
  ctx.font = `600 44px ${FONT}`;
  ctx.fillText(clock, w - pad, pad + 38);
  ctx.fillStyle = MUTED;
  ctx.font = `500 28px ${FONT}`;
  const { counts } = data;
  const summary = `${counts.working} ${l.working} · ${counts.waiting} ${l.waiting} · ${counts.idle} ${l.idle}`;
  ctx.fillText(summary, w - pad - ctx.measureText(clock).width - 60, pad + 34);
  ctx.textAlign = "left";
  const top = pad + 80, cols = 4, rows = 2, gap = 20;
  const tw = (w - pad * 2 - gap * (cols - 1)) / cols, th = (h - top - pad - gap * (rows - 1)) / rows;
  if (data.agents.length === 0) {
    ctx.fillStyle = MUTED;
    ctx.font = `500 40px ${FONT}`;
    ctx.textAlign = "center";
    ctx.fillText(l.none, w / 2, top + (h - top) / 2);
    ctx.textAlign = "left";
    return;
  }
  const order: Record<AgentRunState, number> = { waiting: 0, working: 1, idle: 2, paused: 3 };
  const tiles = [...data.agents].sort((a, b) => order[a.state] - order[b.state]).slice(0, cols * rows);
  tiles.forEach((a, i) => {
    const x = pad + (i % cols) * (tw + gap), y = top + Math.floor(i / cols) * (th + gap);
    ctx.fillStyle = "rgba(255,255,255,0.045)";
    ctx.beginPath(); ctx.roundRect(x, y, tw, th, 16); ctx.fill();
    ctx.fillStyle = STATE[a.state];
    ctx.beginPath(); ctx.roundRect(x, y, 6, th, [16, 0, 0, 16]); ctx.fill();
    const ix = x + 28, iw = tw - 48;
    ctx.fillStyle = INK;
    ctx.font = `600 32px ${FONT}`;
    ctx.fillText(fit(ctx, a.name, iw), ix, y + 50);
    ctx.fillStyle = MUTED;
    ctx.font = `500 24px ${FONT}`;
    ctx.fillText(fit(ctx, `${a.cli} · ${a.workspace}`, iw), ix, y + 86);
    dot(ctx, ix + 7, y + th - 34, 7, STATE[a.state]);
    ctx.fillStyle = INK;
    ctx.font = `500 26px ${FONT}`;
    ctx.fillText(fit(ctx, l.state(a.stateKey), iw - 24), ix + 22, y + th - 25);
    if (a.topic && th > 170) {
      ctx.fillStyle = MUTED;
      ctx.font = `400 24px ${FONT}`;
      ctx.fillText(fit(ctx, a.topic, iw), ix, y + 124);
    }
  });
}

// ---------------------------------------------------------------------------
// Live screens
// ---------------------------------------------------------------------------

/** A canvas-backed screen material that redraws whenever `key` changes. */
function useLiveScreen(width: number, height: number, key: string, draw: (ctx: Ctx, w: number, h: number) => void): MeshBasicMaterial {
  const canvas = useMemo(() => (typeof document !== "undefined" ? document.createElement("canvas") : null), []);
  const material = useMemo(() => {
    if (!canvas) return new MeshBasicMaterial({ color: "#0b0d10" });
    canvas.width = width;
    canvas.height = height;
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.anisotropy = 8;
    return new MeshBasicMaterial({ map: texture, toneMapped: false });
  }, [canvas, width, height]);
  const drawRef = useRef(draw);
  drawRef.current = draw;
  useEffect(() => {
    let ctx: Ctx | null = null;
    try { ctx = canvas?.getContext("2d") ?? null; } catch {
      // jsdom without the canvas package: the plain dark screen is the right fallback.
      ctx = null;
    }
    if (!ctx || !material.map) return;
    drawRef.current(ctx, width, height);
    material.map.needsUpdate = true;
  }, [key, canvas, material, width, height]);
  useEffect(() => () => { material.map?.dispose(); material.dispose(); }, [material]);
  return material;
}

/** Minutes change the clock; a timer ticks it without any network. */
function useClock(): string {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const timer = setInterval(() => setNow(new Date()), 20_000);
    return () => clearInterval(timer);
  }, []);
  return now.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

/** Opening Mission Control from anything in the office that is clicked. */
function useOpenMission() {
  const hovered = useRef(false);
  useEffect(() => () => { if (hovered.current) document.body.style.cursor = ""; }, []);
  return {
    onClick: (event: ThreeEvent<MouseEvent>) => {
      event.stopPropagation();
      useOfficeStore.getState().select({ kind: "checkpoint", id: "mission" });
    },
    onPointerOver: (event: ThreeEvent<PointerEvent>) => { event.stopPropagation(); hovered.current = true; document.body.style.cursor = "pointer"; },
    onPointerOut: () => { hovered.current = false; document.body.style.cursor = ""; },
  };
}

function Screen({ w, h, material }: { w: number; h: number; material: MeshBasicMaterial }) {
  return (
    <group>
      <Rounded size={[w + 0.03, h + 0.03, 0.028]} radius={0.01} position={[0, 0, 0]} material={MAT.monitor} />
      <mesh position={[0, 0, 0.0145]} material={material}>
        <planeGeometry args={[w, h]} />
      </mesh>
    </group>
  );
}

// ---------------------------------------------------------------------------
// The slat wall with the video wall
// ---------------------------------------------------------------------------

const CM = {
  felt: matte("#202328", { roughness: 1 }),
  walnut: matte("#5b3b27", { roughness: 0.7 }),
  // Light strips: lit from within, so they read as light in any scene lighting.
  cove: new MeshStandardMaterial({ color: "#ffe6c4", emissive: "#ffe6c4", emissiveIntensity: 1.1, toneMapped: false }),
  ice: new MeshStandardMaterial({ color: "#cfe9ff", emissive: "#cfe9ff", emissiveIntensity: 1.1, toneMapped: false }),
  speaker: matte("#1b1d21", { roughness: 0.6 }),
};
const SLAT_PITCH = 0.13;
const SLATS = Array.from({ length: Math.floor(7.7 / SLAT_PITCH) }, (_, i) => -3.85 + SLAT_PITCH / 2 + i * SLAT_PITCH);
const WALL_SCREEN = { w: 3.4, h: 1.36, y: 1.6 };
const WALL_CANVAS = { w: 1600, h: Math.round((1600 * 1.36) / 3.4) };

export function CommandWall() {
  const data = useFloorData();
  const labels = useScreenLabels();
  const clock = useClock();
  const key = JSON.stringify([data, clock, labels.title, labels.none, labels.working]);
  const screen = useLiveScreen(WALL_CANVAS.w, WALL_CANVAS.h, key, (ctx, w, h) => drawVideoWall(ctx, w, h, data, labels, clock));
  const open = useOpenMission();
  return (
    <group>
      {/* Dark felt backing, a full-height walnut slat screen, warm light in coves top and bottom. */}
      <Box size={[7.8, 2.7, 0.06]} position={[0, 1.35, -0.12]} material={CM.felt} />
      {SLATS.map((x) => <Box key={x} size={[0.06, 2.62, 0.06]} position={[x, 1.35, -0.06]} material={CM.walnut} cast={false} />)}
      <Box size={[7.7, 0.012, 0.02]} position={[0, 0.03, -0.02]} material={CM.cove} cast={false} />
      <Box size={[7.7, 0.012, 0.02]} position={[0, 2.67, -0.02]} material={CM.cove} cast={false} />
      {/* The video wall, flush on the slats; a floating oak shelf with a pair of speakers under it. */}
      <group position={[0, WALL_SCREEN.y, 0.02]} {...open}>
        <Screen w={WALL_SCREEN.w} h={WALL_SCREEN.h} material={screen} />
      </group>
      <Rounded size={[2.6, 0.045, 0.24]} radius={0.01} position={[0, 0.66, 0.02]} material={MAT.deskTop} />
      {[-1.05, 1.05].map((x) => <Rounded key={x} size={[0.16, 0.24, 0.16]} radius={0.02} position={[x, 0.805, 0.03]} material={CM.speaker} />)}
    </group>
  );
}

// ---------------------------------------------------------------------------
// The desk with three monitors, and its chair
// ---------------------------------------------------------------------------

const MONITOR = { w: 0.72, h: 0.42, y: 1.14 };
const MONITOR_CANVAS = { w: 1024, h: Math.round((1024 * 0.42) / 0.72) };
const DESK_Z = -COMMAND_DESK_OFFSET;
const CHAIR_Z = DESK_Z + COMMAND_DESK.chairZ;
const GAS = new CylinderGeometry(0.028, 0.028, 0.3, 12);
const MUG = new CylinderGeometry(0.04, 0.036, 0.1, 16);

function ExecutiveChair() {
  // The person faces north, towards the monitors: the chair's back is on its +z side.
  return (
    <group position={[0, 0, CHAIR_Z]}>
      {[0, 1, 2, 3, 4].map((i) => (
        <group key={i} rotation={[0, (i * Math.PI * 2) / 5, 0]}>
          <Box size={[0.05, 0.035, 0.3]} position={[0, 0.07, 0.15]} material={MAT.chair} />
          <Box size={[0.05, 0.05, 0.05]} position={[0, 0.025, 0.29]} material={MAT.steel} cast={false} />
        </group>
      ))}
      <mesh geometry={GAS} material={MAT.steel} position={[0, 0.25, 0]} castShadow />
      <Rounded size={[0.54, 0.08, 0.5]} radius={0.035} position={[0, 0.46, 0]} material={MAT.chairSeat} />
      <group position={[0, 0.9, 0.24]} rotation={[0.12, 0, 0]}>
        <Rounded size={[0.5, 0.66, 0.06]} radius={0.03} position={[0, 0, 0]} material={MAT.chairMesh} />
        <Rounded size={[0.34, 0.13, 0.07]} radius={0.03} position={[0, 0.42, 0.02]} material={MAT.chair} />
      </group>
      {[-0.29, 0.29].map((x) => (
        <group key={x}>
          <Box size={[0.04, 0.2, 0.04]} position={[x, 0.58, 0.04]} material={MAT.chair} />
          <Rounded size={[0.07, 0.03, 0.28]} radius={0.012} position={[x, 0.69, 0.0]} material={MAT.chair} />
        </group>
      ))}
    </group>
  );
}

export function CommandDesk() {
  const data = useFloorData();
  const labels = useScreenLabels();
  const key = JSON.stringify([data, labels.title, labels.none]);
  const board = useLiveScreen(MONITOR_CANVAS.w, MONITOR_CANVAS.h, key, (ctx, w, h) => drawBoard(ctx, w, h, data, labels));
  const spaces = useLiveScreen(MONITOR_CANVAS.w, MONITOR_CANVAS.h, key, (ctx, w, h) => drawWorkspaces(ctx, w, h, data, labels));
  const activity = useLiveScreen(MONITOR_CANVAS.w, MONITOR_CANVAS.h, key, (ctx, w, h) => drawActivity(ctx, w, h, data, labels));
  const open = useOpenMission();
  const { w, d } = COMMAND_DESK;
  return (
    <group>
      <group {...open}>
        {/* Oak top on black steel sled legs, a cable panel at the back and a thin light under the front edge. */}
        <Rounded size={[w, 0.04, d]} radius={0.012} position={[0, 0.74, DESK_Z]} material={MAT.deskTop} />
        {[-(w / 2 - 0.12), w / 2 - 0.12].map((x) => (
          <group key={x}>
            <Box size={[0.05, 0.72, d - 0.12]} position={[x, 0.36, DESK_Z]} material={MAT.deskLeg} />
            <Box size={[0.07, 0.025, d - 0.06]} position={[x, 0.012, DESK_Z]} material={MAT.deskLeg} />
          </group>
        ))}
        <Box size={[w - 0.4, 0.3, 0.02]} position={[0, 0.5, DESK_Z - d / 2 + 0.06]} material={MAT.deskLeg} />
        <Box size={[w - 0.1, 0.01, 0.012]} position={[0, 0.715, DESK_Z + d / 2 - 0.02]} material={CM.ice} cast={false} />
        {/* One triple arm: a post at the back and a bar carrying the three monitors, the outer two turned in. */}
        <Box size={[0.05, 0.38, 0.05]} position={[0, 0.94, DESK_Z - 0.32]} material={MAT.monitorArm} />
        <Box size={[1.7, 0.035, 0.035]} position={[0, MONITOR.y - 0.02, DESK_Z - 0.3]} material={MAT.monitorArm} />
        <group position={[0, MONITOR.y, DESK_Z - 0.26]}>
          <Screen w={MONITOR.w} h={MONITOR.h} material={board} />
        </group>
        <group position={[-0.76, MONITOR.y, DESK_Z - 0.17]} rotation={[0, 0.38, 0]}>
          <Screen w={MONITOR.w} h={MONITOR.h} material={spaces} />
        </group>
        <group position={[0.76, MONITOR.y, DESK_Z - 0.17]} rotation={[0, -0.38, 0]}>
          <Screen w={MONITOR.w} h={MONITOR.h} material={activity} />
        </group>
        {/* On the desk: keyboard, mouse, a mug. */}
        <Rounded size={[0.46, 0.018, 0.15]} radius={0.006} position={[0, 0.769, DESK_Z + 0.18]} material={MAT.keyboard} />
        <Rounded size={[0.06, 0.022, 0.1]} radius={0.01} position={[0.36, 0.771, DESK_Z + 0.18]} material={MAT.keyboard} />
        <mesh geometry={MUG} material={MAT.mug} position={[-0.95, 0.81, DESK_Z + 0.1]} castShadow />
      </group>
      <ExecutiveChair />
    </group>
  );
}

/** Mission Control's renderers; merged into OfficeProps' exhaustive table. */
export const COMMAND_RENDERERS = {
  commandWall: () => <CommandWall />,
  commandDesk: () => <CommandDesk />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;
