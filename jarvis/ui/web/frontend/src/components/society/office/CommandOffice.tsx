/**
 * Mission Control, the coding floor's own office: a walnut slat wall with a
 * live video wall, and a desk with three live monitors and an executive chair.
 * Working it (E in the room, or a click on the video wall or the desk) opens
 * the Mission Control panel: start new coding agents, brief several at once.
 *
 * The video wall is a live board of the floor, drawn from the same shared pane
 * poll the figures ride: each agent as a tile. The desk's three monitors run
 * real app sections live (MissionScreens): Spend (left), the Agents view
 * (centre) and the Agentic IDE (right). A click on one dives the camera into
 * its glass and opens that section; from afar each shows a quiet title card.
 *
 * Pieces are built in local space centred on the origin, front facing +z, and
 * stay inside their FURNITURE_SIZE box.
 */
import { useEffect, useMemo, useRef, useState, type RefObject } from "react";
import { Html } from "@react-three/drei";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { CanvasTexture, CylinderGeometry, MeshBasicMaterial, MeshStandardMaterial, Quaternion, SRGBColorSpace, Vector3, type Group } from "three";
import { useT } from "@/i18n";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import type { AgentRunState } from "../data";
import { Box, MAT, matte, Rounded } from "./OfficeFurniture";
import { paneOccupants } from "./codingFloor";
import { COMMAND_DESK, COMMAND_DESK_OFFSET, type Furniture, type FurnitureKind } from "./officeLayout";
import { useLeadSeat } from "./leadSeat";
import { MissionLiveScreen, preloadMissionScreens } from "./MissionScreens";
import { useOfficeStore, type MonitorSection } from "./officeStore";

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
  title: string; agents: string; none: string;
  /** The desk monitors' sections, named as the navigation names them. */
  costs: string; agentsView: string; ide: string;
  working: string; waiting: string; idle: string;
  state: (key: string) => string;
  since: (at: number | null) => string;
}

function useScreenLabels(): ScreenLabels {
  const t = useT();
  return {
    title: t("society.office.cp_mission"),
    agents: t("society.office.mission_screen_agents"),
    costs: t("nav.costs"),
    agentsView: t("nav.agents"),
    ide: t("nav.agentic_ide"),
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

const INK = "#f4f6f8", MUTED = "#8b929c";
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

/** A desk monitor seen from afar: the section it runs, and one line of what it holds. */
export function drawTitleCard(ctx: Ctx, w: number, h: number, title: string, detail: string): void {
  ground(ctx, w, h);
  const pad = 56;
  heading(ctx, detail, pad, pad + 26);
  ctx.fillStyle = INK;
  ctx.font = `700 96px ${FONT}`;
  ctx.fillText(fit(ctx, title, w - pad * 2), pad, h - pad - 10);
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
const MUG = new CylinderGeometry(0.04, 0.036, 0.1, 16);

/** The boss chair's leather and chrome. */
const BOSS = {
  leather: new MeshStandardMaterial({ color: "#17181b", roughness: 0.42, metalness: 0.05 }),
  seam: new MeshStandardMaterial({ color: "#0c0d0f", roughness: 0.6 }),
  chrome: new MeshStandardMaterial({ color: "#d4d8de", roughness: 0.16, metalness: 0.95 }),
  caster: new MeshStandardMaterial({ color: "#101113", roughness: 0.5 }),
};
const GAS_LIFT = new CylinderGeometry(0.032, 0.032, 0.26, 16);
const SHROUD = new CylinderGeometry(0.055, 0.075, 0.1, 20);
/** Mission Control's chair is a seat the person can take (leadSeat); its id is the desk's. */
const COMMAND_SEAT = "command";

/**
 * A high-back executive chair in black leather on a polished five-star base:
 * a thick seat, four channel-stitched back cushions leaning back, a headrest,
 * side bolsters and chrome armrests with leather pads. Local space: seat on
 * the floor at the origin, the sitter faces -z, the back is on the +z side.
 */
function BossChair() {
  return (
    <group>
      {/* Chrome star base with casters, a shroud and the gas lift. */}
      {[0, 1, 2, 3, 4].map((i) => (
        <group key={i} rotation={[0, (i * Math.PI * 2) / 5 + Math.PI / 5, 0]}>
          <Box size={[0.055, 0.035, 0.33]} position={[0, 0.085, 0.165]} material={BOSS.chrome} />
          <Box size={[0.05, 0.06, 0.07]} position={[0, 0.03, 0.31]} material={BOSS.caster} cast={false} />
        </group>
      ))}
      <mesh geometry={SHROUD} material={BOSS.leather} position={[0, 0.14, 0]} castShadow />
      <mesh geometry={GAS_LIFT} material={BOSS.chrome} position={[0, 0.27, 0]} castShadow />
      {/* Seat: a thick cushion on a dark frame, a stitch line round its top. */}
      <Box size={[0.5, 0.04, 0.46]} position={[0, 0.4, 0]} material={BOSS.seam} />
      <Rounded size={[0.62, 0.13, 0.58]} radius={0.055} position={[0, 0.48, -0.01]} material={BOSS.leather} />
      <Box size={[0.52, 0.006, 0.48]} position={[0, 0.547, -0.01]} material={BOSS.seam} cast={false} />
      {/* High back, leaning back: a shell, four stitched cushions, bolsters, the headrest. */}
      <group position={[0, 0.56, 0.27]} rotation={[0.14, 0, 0]}>
        <Rounded size={[0.6, 0.94, 0.07]} radius={0.04} position={[0, 0.47, 0.05]} material={BOSS.leather} />
        {[0.14, 0.34, 0.54, 0.74].map((y) => (
          <Rounded key={y} size={[0.48, 0.19, 0.1]} radius={0.045} position={[0, y, -0.01]} material={BOSS.leather} />
        ))}
        {[-0.28, 0.28].map((x) => (
          <Rounded key={x} size={[0.07, 0.86, 0.13]} radius={0.03} position={[x, 0.45, 0.0]} material={BOSS.leather} />
        ))}
        <Rounded size={[0.42, 0.18, 0.12]} radius={0.05} position={[0, 0.98, 0.0]} material={BOSS.leather} />
      </group>
      {/* Chrome loop armrests with leather pads. */}
      {[-0.34, 0.34].map((x) => (
        <group key={x}>
          <Box size={[0.03, 0.26, 0.03]} position={[x, 0.6, 0.12]} material={BOSS.chrome} />
          <Box size={[0.03, 0.2, 0.03]} position={[x, 0.58, -0.14]} material={BOSS.chrome} />
          <Rounded size={[0.08, 0.05, 0.36]} radius={0.02} position={[x, 0.74, -0.01]} material={BOSS.leather} />
        </group>
      ))}
    </group>
  );
}

/**
 * The chair behind the desk: click it (or press E beside it) to sit down. While
 * seated the camera looks through the person's eyes at the monitors, so the
 * chair steps out of the way; any movement stands them up again.
 */
function ExecutiveChair() {
  const t = useT();
  const seated = useLeadSeat((s) => s.seated === COMMAND_SEAT);
  const near = useLeadSeat((s) => s.near === COMMAND_SEAT);
  const hovered = useRef(false);
  useEffect(() => () => { if (hovered.current) document.body.style.cursor = ""; }, []);
  const takeSeat = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 6) return;
    event.stopPropagation();
    const at = new Vector3(0, 0, CHAIR_Z);
    event.eventObject.parent?.localToWorld(at);
    useLeadSeat.getState().set({ pending: COMMAND_SEAT });
    useOfficeStore.getState().requestWalk({ x: at.x, z: at.z });
  };
  return (
    <group position={[0, 0, CHAIR_Z]}>
      <group visible={!seated} onClick={takeSeat}
        onPointerOver={(event) => { event.stopPropagation(); hovered.current = true; document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { hovered.current = false; document.body.style.cursor = ""; }}>
        <BossChair />
      </group>
      {near && !seated && (
        <Html center position={[0, 1.75, 0]} zIndexRange={[24, 0]}>
          <span className="office-plate office-seat-prompt" data-office-ui><kbd>E</kbd>{t("society.office.seat_sit")}</span>
        </Html>
      )}
      {seated && (
        // In first person this floats just under the monitors, where the eyes are.
        <Html center position={[0, 0.86, -0.42]} zIndexRange={[24, 0]}>
          <span className="office-plate office-seat-prompt" data-office-ui>{t("society.office.seat_seated_mission")}</span>
        </Html>
      )}
    </group>
  );
}

/** Run the live sections within this camera distance of the desk, stop beyond the second (no flicker at the edge). */
const LIVE_NEAR_M = 22;
const LIVE_FAR_M = 26;

/** Should the desk run its live sections, given the camera distance and whether it already does? Pure. */
export function deskLive(distance: number, live: boolean): boolean {
  return distance <= (live ? LIVE_FAR_M : LIVE_NEAR_M);
}

/** True while the camera is close enough to `anchor` to read the monitors. */
function useNearCamera(anchor: RefObject<Group | null>): boolean {
  const [near, setNear] = useState(false);
  const nextCheck = useRef(0);
  const at = useMemo(() => new Vector3(), []);
  useFrame(({ camera, clock }) => {
    if (!anchor.current || clock.elapsedTime < nextCheck.current) return;
    nextCheck.current = clock.elapsedTime + 0.3;
    anchor.current.getWorldPosition(at);
    const next = deskLive(camera.position.distanceTo(at), near);
    if (next !== near) setNear(next);
  });
  return near;
}

/** The glass sits this far in front of a monitor's centre (see Screen). */
const GLASS_Z = 0.0145;

/** A click on a monitor: dive into its glass (`size` metres, `glassZ` in front of its centre), then open its section. */
export function useMonitorDive(section: MonitorSection, size: [number, number] = [MONITOR.w, MONITOR.h], glassZ = GLASS_Z) {
  const hovered = useRef(false);
  useEffect(() => () => { if (hovered.current) document.body.style.cursor = ""; }, []);
  return {
    onClick: (event: ThreeEvent<MouseEvent>) => {
      if (event.delta > 6) return;
      event.stopPropagation();
      const monitor = event.eventObject;
      const centre = monitor.getWorldPosition(new Vector3());
      const normal = new Vector3(0, 0, 1).applyQuaternion(monitor.getWorldQuaternion(new Quaternion()));
      const glass = centre.addScaledVector(normal, glassZ);
      useOfficeStore.getState().diveToSection(section, [glass.x, glass.y, glass.z], Math.atan2(normal.x, normal.z), size);
    },
    onPointerOver: (event: ThreeEvent<PointerEvent>) => { event.stopPropagation(); hovered.current = true; document.body.style.cursor = "zoom-in"; },
    onPointerOut: (event: ThreeEvent<PointerEvent>) => { event.stopPropagation(); hovered.current = false; document.body.style.cursor = ""; },
  };
}

function DeskMonitor({ section, title, detail, live, position, turn = 0 }: {
  section: MonitorSection; title: string; detail: string; live: boolean; position: [number, number, number]; turn?: number;
}) {
  const card = useLiveScreen(MONITOR_CANVAS.w, MONITOR_CANVAS.h, `${title}|${detail}`, (ctx, w, h) => drawTitleCard(ctx, w, h, title, detail));
  const dive = useMonitorDive(section);
  return (
    <group position={position} rotation={[0, turn, 0]} {...dive}>
      <Screen w={MONITOR.w} h={MONITOR.h} material={card} />
      {live && <MissionLiveScreen section={section} widthM={MONITOR.w} position={[0, 0, GLASS_Z + 0.002]} />}
    </group>
  );
}

export function CommandDesk() {
  const data = useFloorData();
  const labels = useScreenLabels();
  const open = useOpenMission();
  const anchor = useRef<Group>(null);
  const live = useNearCamera(anchor);
  // The floor is open: fetch the sections' code now, not when the camera arrives.
  useEffect(() => { preloadMissionScreens(); }, []);
  const { w, d } = COMMAND_DESK;
  const agentCount = `${data.counts.total} ${labels.agents}`;
  return (
    <group ref={anchor}>
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
        <DeskMonitor section="agents" title={labels.agentsView} detail={labels.title} live={live}
          position={[0, MONITOR.y, DESK_Z - 0.26]} />
        <DeskMonitor section="costs" title={labels.costs} detail={labels.title} live={live}
          position={[-0.76, MONITOR.y, DESK_Z - 0.17]} turn={0.38} />
        <DeskMonitor section="agentic-ide" title={labels.ide} detail={agentCount} live={live}
          position={[0.76, MONITOR.y, DESK_Z - 0.17]} turn={-0.38} />
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
