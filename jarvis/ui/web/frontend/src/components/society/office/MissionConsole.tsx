/**
 * Mission Control on the coding floor: one slim display on a floor stand, in
 * the office's contemporary kit (black steel, a thin bezel, a low base plate).
 * Its screen is a live board of the floor — how many coding agents there are
 * and how many work, wait or idle — and invites starting a new one.
 *
 * Built in local space centred on the origin, front facing +z, and kept
 * inside the `missionConsole` footprint. A click anywhere on it opens Mission
 * Control.
 */
import { useEffect, useMemo, useRef } from "react";
import type { ThreeEvent } from "@react-three/fiber";
import { CanvasTexture, MeshBasicMaterial, SRGBColorSpace } from "three";
import { useT } from "@/i18n";
import { useWorkspacePanesStore } from "@/store/workspacePanes";
import { Box, MAT, Rounded } from "./OfficeFurniture";
import { paneOccupants } from "./codingFloor";
import type { Furniture, FurnitureKind } from "./officeLayout";
import { useOfficeStore } from "./officeStore";

/** The display: a 55-inch-ish landscape panel, its centre height and backward tilt. */
const SCREEN_W = 1.3, SCREEN_H = 0.76, SCREEN_Y = 1.36, TILT = -0.07;
const CANVAS_W = 1024, CANVAS_H = Math.round((CANVAS_W * SCREEN_H) / SCREEN_W);

export interface FloorCounts { total: number; working: number; waiting: number; idle: number }

/** What the screen shows, in the viewer's language. */
export interface ScreenLabels { title: string; agents: string; working: string; waiting: string; idle: string; start: string }

const INK = "#f4f6f8", MUTED = "#8b929c", LINE = "rgba(255,255,255,0.08)";
const DOT = { working: "#4ade80", waiting: "#fbbf24", idle: "#8b929c" } as const;
const FONT = "Inter, system-ui, -apple-system, 'Segoe UI', sans-serif";

/** Draw the live board. Pure drawing: the same counts always give the same picture. */
export function drawMissionScreen(ctx: CanvasRenderingContext2D, w: number, h: number, counts: FloorCounts, labels: ScreenLabels): void {
  const bg = ctx.createLinearGradient(0, 0, 0, h);
  bg.addColorStop(0, "#15181d");
  bg.addColorStop(1, "#0b0d10");
  ctx.fillStyle = bg;
  ctx.fillRect(0, 0, w, h);
  const pad = 56;
  ctx.textBaseline = "alphabetic";
  ctx.textAlign = "left";

  // Header: the name, and a quiet live dot.
  ctx.fillStyle = MUTED;
  ctx.font = `600 30px ${FONT}`;
  ctx.fillText(labels.title, pad, pad + 26);
  ctx.fillStyle = DOT.working;
  ctx.beginPath(); ctx.arc(w - pad - 8, pad + 16, 8, 0, Math.PI * 2); ctx.fill();

  // The one big number.
  ctx.fillStyle = INK;
  ctx.font = `700 200px ${FONT}`;
  ctx.fillText(String(counts.total), pad - 8, pad + 250);
  ctx.fillStyle = MUTED;
  ctx.font = `500 32px ${FONT}`;
  ctx.fillText(labels.agents, pad, pad + 300);

  // Three state rows on the right, divided by hairlines.
  const x0 = w * 0.55, rowH = 78;
  const rows: [string, number, string][] = [
    [labels.working, counts.working, DOT.working], [labels.waiting, counts.waiting, DOT.waiting], [labels.idle, counts.idle, DOT.idle],
  ];
  rows.forEach(([label, n, colour], i) => {
    const y = pad + 90 + i * rowH;
    ctx.fillStyle = LINE;
    ctx.fillRect(x0, y - 50, w - pad - x0, 2);
    ctx.fillStyle = colour;
    ctx.beginPath(); ctx.arc(x0 + 10, y - 12, 9, 0, Math.PI * 2); ctx.fill();
    ctx.fillStyle = INK;
    ctx.font = `500 32px ${FONT}`;
    ctx.fillText(label, x0 + 36, y);
    ctx.textAlign = "right";
    ctx.font = `700 40px ${FONT}`;
    ctx.fillText(String(n), w - pad, y + 2);
    ctx.textAlign = "left";
  });

  // The call to action along the bottom: a white button with a plus.
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
  ctx.fillText(labels.start, bx + 80, py + 11);
}

/** The floor's live counts, from the same shared pane poll the figures ride. */
function useFloorCounts(): FloorCounts {
  const panes = useWorkspacePanesStore((s) => s.panes);
  return useMemo(() => {
    const counts: FloorCounts = { total: 0, working: 0, waiting: 0, idle: 0 };
    for (const { agent } of paneOccupants(panes)) {
      counts.total += 1;
      if (agent.state === "working") counts.working += 1;
      else if (agent.state === "waiting") counts.waiting += 1;
      else counts.idle += 1;
    }
    return counts;
  }, [panes]);
}

function useScreenMaterial(counts: FloorCounts, labels: ScreenLabels): MeshBasicMaterial {
  const canvas = useMemo(() => (typeof document !== "undefined" ? document.createElement("canvas") : null), []);
  const material = useMemo(() => {
    if (!canvas) return new MeshBasicMaterial({ color: "#0b0d10" });
    canvas.width = CANVAS_W;
    canvas.height = CANVAS_H;
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.anisotropy = 8;
    return new MeshBasicMaterial({ map: texture, toneMapped: false });
  }, [canvas]);
  const key = JSON.stringify([counts, labels]);
  useEffect(() => {
    let ctx: CanvasRenderingContext2D | null = null;
    try { ctx = canvas?.getContext("2d") ?? null; } catch {
      // jsdom without the canvas package: the plain dark screen is the right fallback.
      ctx = null;
    }
    if (!ctx || !material.map) return;
    drawMissionScreen(ctx, CANVAS_W, CANVAS_H, counts, labels);
    material.map.needsUpdate = true;
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key, canvas, material]);
  useEffect(() => () => { material.map?.dispose(); material.dispose(); }, [material]);
  return material;
}

export function MissionScreen() {
  const t = useT();
  const counts = useFloorCounts();
  const labels: ScreenLabels = {
    title: t("society.office.cp_mission"),
    agents: t("society.office.mission_screen_agents"),
    working: t("society.office.state_working"),
    waiting: t("society.office.state_waiting"),
    idle: t("society.office.state_idle"),
    start: t("society.office.mission_screen_start"),
  };
  const screen = useScreenMaterial(counts, labels);
  const hovered = useRef(false);
  useEffect(() => () => { if (hovered.current) document.body.style.cursor = ""; }, []);
  const open = (event: ThreeEvent<MouseEvent>) => {
    event.stopPropagation();
    useOfficeStore.getState().select({ kind: "checkpoint", id: "mission" });
  };
  const over = (event: ThreeEvent<PointerEvent>) => { event.stopPropagation(); hovered.current = true; document.body.style.cursor = "pointer"; };
  const out = () => { hovered.current = false; document.body.style.cursor = ""; };

  return (
    <group name="mission-screen" onClick={open} onPointerOver={over} onPointerOut={out}>
      {/* Low base plate and a slim column, set back so the screen overhangs it a little. */}
      <Rounded size={[0.64, 0.035, 0.42]} radius={0.015} position={[0, 0.018, -0.02]} material={MAT.steel} />
      <Box size={[0.07, SCREEN_Y - 0.03, 0.05]} position={[0, (SCREEN_Y + 0.03) / 2, -0.09]} material={MAT.steel} />
      {/* The display: rear housing, thin bezel, the live screen flush in front. */}
      <group position={[0, SCREEN_Y, -0.04]} rotation={[TILT, 0, 0]}>
        <Rounded size={[0.42, 0.3, 0.05]} radius={0.015} position={[0, 0, -0.045]} material={MAT.steel} />
        <Rounded size={[SCREEN_W + 0.03, SCREEN_H + 0.03, 0.035]} radius={0.012} position={[0, 0, 0]} material={MAT.monitor} />
        <mesh position={[0, 0, 0.0185]} material={screen}>
          <planeGeometry args={[SCREEN_W, SCREEN_H]} />
        </mesh>
      </group>
    </group>
  );
}

/** Mission Control's renderers; merged into OfficeProps' exhaustive table. */
export const MISSION_RENDERERS = {
  missionConsole: () => <MissionScreen />,
} satisfies Partial<Record<FurnitureKind, (props: { item: Furniture }) => JSX.Element>>;
