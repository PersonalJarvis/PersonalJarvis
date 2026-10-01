/**
 * The full office map, game style: "M" (or the minimap's Map button) opens a
 * dialog over most of the stage with the whole floor fitted north-up, a light
 * lettered grid (columns A–H, rows 1–N), room and checkpoint names, every
 * agent with its name, and the person's arrow. A click anywhere sets a
 * waypoint (the character walks there) and closes the map; Escape or M closes.
 *
 * The painted floor, grid and labels are drawn once per size/layout/theme into
 * an offscreen canvas; the agents and the arrow repaint at ≤ 20 Hz while open.
 * The chrome (header, grid letters, readout) uses theme tokens.
 */
import { useCallback, useEffect, useId, useMemo, useRef, useState, type KeyboardEvent as ReactKeyboardEvent, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { X } from "lucide-react";
import { useReducedMotion } from "framer-motion";
import { useT } from "@/i18n";
import type { CheckpointKind, OfficeLayout, Rect, RoomKind } from "./officeLayout";
import { cameraView, player, useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { ownsKeyboard } from "./OfficePlayer";
import {
  MAP_PAINT, clampToRect, columnLabel, drawAgentToken, drawCheckpointBadge, drawFloorArt, drawMapLabel, drawPlayerArrow,
  drawViewCone, fitTransform, gridLabel, mapGrid, mapToWorld, placeAt, worldToMap, type AgentToken, type MapPoint, type MapTransform,
} from "./minimap";
import {
  agentColour, context2d, offscreenLayer, useElementSize, usePixelRatio, useThemeVersion, useThrottledFrames, type OfficeMapAgent,
} from "./mapHooks";

export interface OfficeFullMapProps {
  open: boolean;
  onOpen: () => void;
  onClose: () => void;
  layout: OfficeLayout;
  agents: ReadonlyMap<string, OfficeMapAgent>;
  selectedId: string | null;
}

const FRAME_MS = 50;
/** Room around the grid for the column letters and row numbers (CSS px). */
const GRID_PAD = 30;
const GRID_COLUMNS = 8;

function typingIn(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false;
  const tag = target.tagName;
  return target.isContentEditable || tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT";
}

/** M toggles the map; Escape closes it before the office (or the view behind it) sees the key. */
function useMapKeys(open: boolean, onOpen: () => void, onClose: () => void): void {
  const live = useRef({ open, onOpen, onClose });
  live.current = { open, onOpen, onClose };
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.ctrlKey || event.metaKey || event.altKey) return;
      const { open: isOpen, onOpen: show, onClose: hide } = live.current;
      if (isOpen) {
        if (event.key === "Escape" || (event.code === "KeyM" && !typingIn(event.target))) {
          event.preventDefault();
          event.stopPropagation();
          hide();
        }
        return;
      }
      if (event.code !== "KeyM" || event.repeat || ownsKeyboard(event.target)) return;
      event.preventDefault();
      show();
    };
    // Window capture runs before the office's and the workspace's document listeners.
    window.addEventListener("keydown", onKey, true);
    return () => window.removeEventListener("keydown", onKey, true);
  }, []);
}

export function OfficeFullMap({ open, onOpen, onClose, layout, agents, selectedId }: OfficeFullMapProps) {
  useMapKeys(open, onOpen, onClose);
  if (!open) return null;
  return <FullMapDialog onClose={onClose} layout={layout} agents={agents} selectedId={selectedId} />;
}

interface Readout { cell: string; place: string }

function FullMapDialog({ onClose, layout, agents, selectedId }: Omit<OfficeFullMapProps, "open" | "onOpen">) {
  const t = useT();
  const reduced = useReducedMotion() ?? false;
  const titleId = useId();
  const hintId = useId();
  const dialogRef = useRef<HTMLDivElement>(null);
  const closeRef = useRef<HTMLButtonElement>(null);
  const bodyRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const layerRef = useRef<HTMLCanvasElement | null>(null);
  const [readout, setReadout] = useState<Readout | null>(null);
  const size = useElementSize(bodyRef);
  const dpr = usePixelRatio();
  const themeVersion = useThemeVersion();

  const grid = useMemo(() => mapGrid(layout.bounds, GRID_COLUMNS), [layout]);
  const gridRect = useMemo<Rect>(() => ({
    minX: grid.originX, maxX: grid.originX + grid.columns * grid.cell, minZ: grid.originZ, maxZ: grid.originZ + grid.rows * grid.cell,
  }), [grid]);
  const transform = useMemo<MapTransform | null>(
    () => (size.width > GRID_PAD * 2 && size.height > GRID_PAD * 2 ? fitTransform(gridRect, size.width, size.height, GRID_PAD) : null),
    [gridRect, size],
  );

  const checkpointLabels: Record<CheckpointKind, string> = {
    spawn: t("society.office.cp_spawn"),
    launch: t("society.office.cp_launch"),
    create: t("society.office.cp_create"),
    manage: t("society.office.cp_manage"),
    team: t("society.office.cp_team"),
    wardrobe: t("society.office.cp_wardrobe"),
    lead: t("society.office.cp_lead"),
    break: t("society.office.cp_break"),
    elevator: t("society.office.cp_elevator"),
    mission: t("society.office.cp_mission"),
  };
  const roomLabels: Record<RoomKind, string> = {
    lead: t("society.office.room_lead"),
    team: t("society.office.room_team"),
    wardrobe: t("society.office.room_wardrobe"),
    reception: t("society.office.room_reception"),
    break: t("society.office.room_break"),
    command: t("society.office.room_command"),
    server: t("society.office.room_server"),
  };
  const openSpace = t("society.office.open_space");
  const youLabel = t("society.office.minimap_you");
  const labelKey = JSON.stringify([checkpointLabels, roomLabels, openSpace]);
  const labels = useRef({ checkpointLabels, roomLabels, openSpace, youLabel });
  labels.current = { checkpointLabels, roomLabels, openSpace, youLabel };

  // Focus moves into the dialog and returns to where it was on close.
  useEffect(() => {
    const previous = document.activeElement instanceof HTMLElement ? document.activeElement : null;
    closeRef.current?.focus();
    return () => { if (previous && previous.isConnected) previous.focus(); };
  }, []);

  const live = useRef({ agents, selectedId, transform, reduced });
  live.current = { agents, selectedId, transform, reduced };

  const draw = useCallback((now: number) => {
    const canvas = canvasRef.current;
    const ctx = context2d(canvas);
    const { agents: roster, selectedId: sel, transform: tr, reduced: still } = live.current;
    if (!canvas || !ctx || !tr) return;
    const ratio = canvas.width / tr.width;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.globalAlpha = 1;
    ctx.fillStyle = MAP_PAINT.space;
    ctx.fillRect(0, 0, tr.width, tr.height);
    if (layerRef.current) ctx.drawImage(layerRef.current, 0, 0, tr.width, tr.height);

    const me = worldToMap(tr, player);
    if (cameraView.ready) drawViewCone(ctx, me, cameraView.yaw, cameraView.halfWidth, Math.max(40, tr.scale * 9));
    const radius = Math.max(6, Math.min(9, tr.scale * 0.5));
    const pulse = still ? null : (now % 1400) / 1400;
    let selected: AgentToken | null = null;
    const tokens: AgentToken[] = [];
    for (const agent of roster.values()) {
      const pos = agentPositions.get(agent.agentId);
      if (!pos) continue;
      const token: AgentToken = {
        id: agent.agentId, name: agent.name, state: agent.state, colour: agentColour(agent), selected: agent.agentId === sel,
        ...worldToMap(tr, pos),
      };
      if (token.selected) selected = token; else tokens.push(token);
    }
    if (selected) tokens.push(selected);
    for (const token of tokens) {
      drawAgentToken(ctx, token, radius, token.selected ? pulse : null);
      drawMapLabel(ctx, token.name, token.x, token.y + radius * (token.selected ? 1.3 : 1) + 11, 10, 700);
    }
    drawPlayerArrow(ctx, me.x, me.y, player.heading, 11);
    drawMapLabel(ctx, labels.current.youLabel, me.x, me.y + 18, 10, 800);
  }, []);

  // Static layer: painted floor, grid, room/department names and checkpoints.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !transform) return;
    canvas.width = Math.round(transform.width * dpr);
    canvas.height = Math.round(transform.height * dpr);
    const off = offscreenLayer(transform.width, transform.height, dpr);
    if (!off) {
      layerRef.current = null;
      return;
    }
    const ctx = off.ctx;
    const nw = mapToWorld(transform, { x: 0, y: 0 });
    const se = mapToWorld(transform, { x: transform.width, y: transform.height });
    drawFloorArt(ctx, layout, transform, { minX: nw.x, maxX: se.x, minZ: nw.z, maxZ: se.z });

    // The lettered grid: light lines over the painting, like a game's big map.
    const g0 = worldToMap(transform, { x: gridRect.minX, z: gridRect.minZ });
    const g1 = worldToMap(transform, { x: gridRect.maxX, z: gridRect.maxZ });
    ctx.strokeStyle = "rgba(255,255,255,0.38)";
    ctx.lineWidth = 1;
    ctx.beginPath();
    for (let c = 0; c <= grid.columns; c += 1) {
      const x = Math.round(g0.x + c * grid.cell * transform.scale) + 0.5;
      ctx.moveTo(x, g0.y);
      ctx.lineTo(x, g1.y);
    }
    for (let r = 0; r <= grid.rows; r += 1) {
      const y = Math.round(g0.y + r * grid.cell * transform.scale) + 0.5;
      ctx.moveTo(g0.x, y);
      ctx.lineTo(g1.x, y);
    }
    ctx.stroke();

    const { roomLabels: rooms, checkpointLabels: cps, openSpace: spare } = labels.current;
    const roomPx = Math.max(11, Math.min(15, transform.scale * 0.75));
    for (const room of layout.rooms) {
      const c = worldToMap(transform, { x: (room.minX + room.maxX) / 2, z: room.minZ + 1.1 });
      drawMapLabel(ctx, rooms[room.kind].toLocaleUpperCase(), c.x, c.y, roomPx, 800);
    }
    for (const dept of layout.departments) {
      const c = worldToMap(transform, { x: (dept.minX + dept.maxX) / 2, z: dept.minZ + 0.9 });
      drawMapLabel(ctx, (dept.label || spare).toLocaleUpperCase(), c.x, c.y, roomPx * 0.85, 800);
    }
    const badge = Math.max(14, Math.min(22, transform.scale * 1.1));
    for (const cp of layout.checkpoints) {
      const m = worldToMap(transform, cp);
      drawCheckpointBadge(ctx, m.x, m.y, badge, cp.id);
      drawMapLabel(ctx, cps[cp.id], m.x, m.y - badge / 2 - 8, 10, 700);
    }
    layerRef.current = off.canvas;
    draw(performance.now());
    // labelKey stands in for the translated names (read through a ref), which are new objects every render.
  }, [layout, transform, grid, gridRect, dpr, themeVersion, labelKey, draw]);

  useThrottledFrames(draw, FRAME_MS, transform !== null);

  const mapPoint = (event: { clientX: number; clientY: number }): MapPoint | null => {
    const canvas = canvasRef.current;
    if (!canvas || !transform) return null;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return { x: ((event.clientX - rect.left) / rect.width) * transform.width, y: ((event.clientY - rect.top) / rect.height) * transform.height };
  };

  const onClick = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    const at = mapPoint(event);
    if (!at || !transform) return;
    useOfficeStore.getState().requestWalk(clampToRect(mapToWorld(transform, at), layout.floor));
    onClose();
  };

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const at = mapPoint(event);
    if (!at || !transform) return;
    const world = mapToWorld(transform, at);
    const place = placeAt(layout, world);
    const name = place?.kind === "checkpoint" ? checkpointLabels[place.id]
      : place?.kind === "room" ? roomLabels[place.id]
        : place?.kind === "department" ? place.label || openSpace : "";
    const next = { cell: gridLabel(grid, world), place: name };
    setReadout((prev) => (prev && prev.cell === next.cell && prev.place === next.place ? prev : next));
  };

  // Tab stays inside the dialog: the close button is its only control.
  const onKeyDown = (event: ReactKeyboardEvent<HTMLDivElement>) => {
    if (event.key !== "Tab") return;
    event.preventDefault();
    closeRef.current?.focus();
  };

  const g0 = transform ? worldToMap(transform, { x: gridRect.minX, z: gridRect.minZ }) : null;
  const cellPx = transform ? grid.cell * transform.scale : 0;

  return (
    <div className="office-fullmap-backdrop" data-office-ui onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
      <div ref={dialogRef} className="office-card office-fullmap" role="dialog" aria-modal="true" data-state="open"
        aria-labelledby={titleId} aria-describedby={hintId} onKeyDown={onKeyDown}>
        <header className="office-fullmap-head">
          <div className="office-fullmap-heading">
            <h2 id={titleId}>{t("society.office.map_title")}</h2>
            <span id={hintId}>{t("society.office.map_hint")}</span>
          </div>
          <span className="office-fullmap-readout" aria-live="off">
            {readout ? (readout.place ? `${readout.cell} · ${readout.place}` : readout.cell) : ""}
          </span>
          <button ref={closeRef} type="button" className="office-icon-button office-fullmap-close" onClick={onClose}
            aria-label={t("society.office.map_close")} title={t("society.office.map_close")} aria-keyshortcuts="Escape M">
            <X aria-hidden size={16} />
          </button>
        </header>
        <div ref={bodyRef} className="office-fullmap-body">
          <canvas ref={canvasRef} className="office-fullmap-canvas" aria-label={t("society.office.map_aria")} role="img"
            style={transform ? { width: transform.width, height: transform.height } : undefined}
            onClick={onClick} onPointerMove={onPointerMove} onPointerLeave={() => setReadout(null)} />
          {g0 && Array.from({ length: grid.columns }, (_, c) => (
            <span key={`c${c}`} className="office-fullmap-axis" aria-hidden style={{ left: g0.x + (c + 0.5) * cellPx, top: g0.y - 14 }}>
              {columnLabel(c)}
            </span>
          ))}
          {g0 && Array.from({ length: grid.rows }, (_, r) => (
            <span key={`r${r}`} className="office-fullmap-axis" aria-hidden style={{ left: g0.x - 14, top: g0.y + (r + 0.5) * cellPx }}>
              {r + 1}
            </span>
          ))}
        </div>
      </div>
    </div>
  );
}
