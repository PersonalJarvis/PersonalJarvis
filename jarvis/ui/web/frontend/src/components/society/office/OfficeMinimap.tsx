/**
 * The office minimap, game style: a square card in the top-right corner that
 * follows the person's character (player-centred, north-up, zoomable), with
 * the camera's view as a soft cone, agents as portrait tokens with a status
 * ring, off-screen agents as arrows on the card edge, and gold checkpoint icons.
 *
 * The painted floor (with a margin of sky around it) is drawn once into an
 * offscreen canvas and only redrawn when the layout, the zoom, the pixel ratio
 * or the theme changes. Every frame (≤ 20 Hz, paused while the document is
 * hidden) just blits that layer at the player's offset and paints the moving
 * markers from the per-frame module state (`player`, `agentPositions`,
 * `cameraView`), never from React state.
 *
 * Click focuses the camera on a spot (or selects the agent under the pointer),
 * double-click walks the character there, the wheel zooms.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { Map as MapIcon, Minus, Plus } from "lucide-react";
import { useReducedMotion } from "framer-motion";
import { useT } from "@/i18n";
import type { CheckpointKind, OfficeLayout, Point, Rect, RoomKind } from "./officeLayout";
import { cameraView, player, useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import {
  MAP_PAINT, ZOOM_DEFAULT_M, ZOOM_MAX_M, ZOOM_MIN_M, centredTransform, clampToEdge, clampToRect, clampZoom, drawAgentToken, drawCheckpoints,
  drawEdgeArrow, drawFloorArt, drawPlayerArrow, drawViewCone, mapToWorld, pickNearest, placeAt, worldToMap, zoomStep,
  type AgentToken, type MapPoint, type MapTransform,
} from "./minimap";
import {
  agentColour, context2d, offscreenLayer, usePixelRatio, useThemeVersion, useThrottledFrames, type OfficeMapAgent,
} from "./mapHooks";

export type OfficeMinimapAgent = OfficeMapAgent;

export interface OfficeMinimapProps {
  layout: OfficeLayout;
  agents: ReadonlyMap<string, OfficeMapAgent>;
  selectedId: string | null;
  /** Opens the full map (the "Map (M)" button). */
  onOpenMap: () => void;
}

/** Card edge in CSS px (square). */
export const MINIMAP_SIZE = 200;
const FRAME_MS = 50;
const HIT_PX = 10;
const EDGE_INSET = 12;
const TOKEN_R = 6.5;
const CHECKPOINT_PX = 14;
const STORAGE_KEY = "jarvis.office.minimap.zoom";

function readZoom(): number {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY);
    return raw ? clampZoom(Number(raw)) : ZOOM_DEFAULT_M;
  } catch {
    // Storage blocked (private mode, sandboxed WebView): start at the default zoom.
    return ZOOM_DEFAULT_M;
  }
}

function writeZoom(metres: number): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, String(metres));
  } catch {
    // Storage blocked: the zoom lasts for this visit only, which is harmless.
  }
}

interface Layer { canvas: HTMLCanvasElement; area: Rect; scale: number; cssW: number; cssH: number }

interface Hit extends MapPoint { id: string; world: Point }

interface Tooltip { x: number; y: number; text: string }

export function OfficeMinimap({ layout, agents, selectedId, onOpenMap }: OfficeMinimapProps) {
  const t = useT();
  const reduced = useReducedMotion() ?? false;
  const [zoom, setZoom] = useState(readZoom);
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);
  const mapRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const layerRef = useRef<Layer | null>(null);
  const hitsRef = useRef<Hit[]>([]);
  const transformRef = useRef<MapTransform>(centredTransform(player, zoom, MINIMAP_SIZE, MINIMAP_SIZE));
  const themeVersion = useThemeVersion();
  const dpr = usePixelRatio();

  // The render loop reads these through a ref, so a roster refetch never restarts it.
  const live = useRef({ agents, selectedId, zoom, reduced });
  live.current = { agents, selectedId, zoom, reduced };

  const draw = useCallback((now: number) => {
    const canvas = canvasRef.current;
    const ctx = context2d(canvas);
    if (!canvas || !ctx) return;
    const { agents: roster, selectedId: sel, zoom: metres, reduced: still } = live.current;
    const size = MINIMAP_SIZE;
    const tr = centredTransform(player, metres, size, size);
    transformRef.current = tr;
    const ratio = canvas.width / size;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.globalAlpha = 1;
    ctx.fillStyle = MAP_PAINT.space;
    ctx.fillRect(0, 0, size, size);
    const layer = layerRef.current;
    if (layer) {
      const origin = worldToMap(tr, { x: layer.area.minX, z: layer.area.minZ });
      ctx.drawImage(layer.canvas, origin.x, origin.y, layer.cssW, layer.cssH);
    }
    const centre = worldToMap(tr, player);
    if (cameraView.ready) drawViewCone(ctx, centre, cameraView.yaw, cameraView.halfWidth, size * 0.5);

    const hits: Hit[] = [];
    const pulse = still ? null : (now % 1400) / 1400;
    let selected: { token: AgentToken; edge: ReturnType<typeof clampToEdge> } | null = null;
    for (const agent of roster.values()) {
      const pos = agentPositions.get(agent.agentId);
      if (!pos) continue;
      const token: AgentToken = {
        id: agent.agentId, name: agent.name, state: agent.state, colour: agentColour(agent), selected: agent.agentId === sel,
        ...worldToMap(tr, pos),
      };
      const edge = clampToEdge(token, size, size, EDGE_INSET);
      hits.push({ id: token.id, x: edge.x, y: edge.y, world: { x: pos.x, z: pos.z } });
      if (token.selected) { selected = { token, edge }; continue; }
      if (edge.clamped) drawEdgeArrow(ctx, edge.x, edge.y, edge.angle, token);
      else drawAgentToken(ctx, token, TOKEN_R, null);
    }
    // The selected agent last, so it sits on top of its neighbours.
    if (selected) {
      if (selected.edge.clamped) drawEdgeArrow(ctx, selected.edge.x, selected.edge.y, selected.edge.angle, selected.token);
      else drawAgentToken(ctx, selected.token, TOKEN_R, pulse);
    }
    hitsRef.current = hits;
    drawPlayerArrow(ctx, centre.x, centre.y, player.heading, 9);
  }, []);

  // Static layer: layout, zoom, pixel ratio or theme changed.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    canvas.width = Math.round(MINIMAP_SIZE * dpr);
    canvas.height = Math.round(MINIMAP_SIZE * dpr);
    const scale = MINIMAP_SIZE / zoom;
    // Enough sky around the floor that the card is never empty at the railing.
    const margin = zoom / 2 + 2;
    const b = layout.bounds;
    const area: Rect = { minX: b.minX - margin, maxX: b.maxX + margin, minZ: b.minZ - margin, maxZ: b.maxZ + margin };
    const cssW = (area.maxX - area.minX) * scale;
    const cssH = (area.maxZ - area.minZ) * scale;
    const off = offscreenLayer(cssW, cssH, dpr);
    if (!off) {
      layerRef.current = null;
      return;
    }
    const lt: MapTransform = { scale, offsetX: -area.minX * scale, offsetY: -area.minZ * scale, width: cssW, height: cssH };
    drawFloorArt(off.ctx, layout, lt, area);
    drawCheckpoints(off.ctx, layout, lt, CHECKPOINT_PX);
    layerRef.current = { canvas: off.canvas, area, scale, cssW, cssH };
    draw(performance.now());
  }, [layout, zoom, dpr, themeVersion, draw]);

  useThrottledFrames(draw, FRAME_MS, true);

  const changeZoom = useCallback((direction: number) => {
    setZoom((prev) => {
      const next = zoomStep(prev, direction);
      if (next !== prev) writeZoom(next);
      return next;
    });
  }, []);

  // The wheel zooms the map instead of scrolling or zooming anything behind it.
  useEffect(() => {
    const el = mapRef.current;
    if (!el) return;
    const onWheel = (event: WheelEvent) => {
      event.preventDefault();
      if (event.deltaY !== 0) changeZoom(event.deltaY < 0 ? 1 : -1);
    };
    el.addEventListener("wheel", onWheel, { passive: false });
    return () => el.removeEventListener("wheel", onWheel);
  }, [changeZoom]);

  const mapPoint = (event: { clientX: number; clientY: number }): MapPoint | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return { x: ((event.clientX - rect.left) / rect.width) * MINIMAP_SIZE, y: ((event.clientY - rect.top) / rect.height) * MINIMAP_SIZE };
  };

  const onClick = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    const at = mapPoint(event);
    if (!at) return;
    const store = useOfficeStore.getState();
    const hit = pickNearest(hitsRef.current, at, HIT_PX);
    if (hit) {
      store.select({ kind: "agent", id: hit.id });
      store.focusOn(hit.world);
      return;
    }
    store.focusOn(clampToRect(mapToWorld(transformRef.current, at), layout.floor));
  };

  const onDoubleClick = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    const at = mapPoint(event);
    if (!at) return;
    useOfficeStore.getState().requestWalk(clampToRect(mapToWorld(transformRef.current, at), layout.floor));
  };

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
  const youLabel = t("society.office.minimap_you");
  const openSpace = t("society.office.open_space");

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const at = mapPoint(event);
    if (!at) return;
    let text: string | null = null;
    const hit = pickNearest(hitsRef.current, at, HIT_PX);
    if (hit) {
      text = agents.get(hit.id)?.name ?? null;
    } else if ((at.x - MINIMAP_SIZE / 2) ** 2 + (at.y - MINIMAP_SIZE / 2) ** 2 <= HIT_PX * HIT_PX) {
      text = youLabel;
    } else {
      const place = placeAt(layout, mapToWorld(transformRef.current, at));
      if (place?.kind === "checkpoint") text = checkpointLabels[place.id];
      else if (place?.kind === "room") text = roomLabels[place.id];
      else if (place?.kind === "department") text = place.label || openSpace;
    }
    setTooltip((prev) => {
      if (!text) return null;
      const next = { x: Math.round(at.x), y: Math.round(at.y), text };
      return prev && prev.text === next.text && prev.x === next.x && prev.y === next.y ? prev : next;
    });
  };

  const working = useMemo(() => {
    let n = 0;
    for (const agent of agents.values()) if (agent.state === "working") n += 1;
    return n;
  }, [agents]);
  const summary = t("society.office.minimap_aria").replace("{0}", String(agents.size)).replace("{1}", String(working));
  const zoomLabel = t("society.office.minimap_zoom").replace("{0}", String(Math.round(zoom)));

  return (
    <div className="office-hud office-minimap" data-office-ui role="group" aria-label={t("society.office.minimap_title")}>
      <div ref={mapRef} className="office-minimap-map" style={{ width: MINIMAP_SIZE, height: MINIMAP_SIZE }}>
        <canvas ref={canvasRef} className="office-minimap-canvas" role="img" aria-label={summary}
          style={{ width: MINIMAP_SIZE, height: MINIMAP_SIZE }}
          onClick={onClick} onDoubleClick={onDoubleClick} onPointerMove={onPointerMove} onPointerLeave={() => setTooltip(null)} />
        <span className="office-minimap-north" aria-hidden>{t("society.office.compass_n")}</span>
        {tooltip && (
          // Flip to the pointer's left on the right half so the label never runs off the card.
          <span className="office-minimap-tooltip" role="presentation" data-side={tooltip.x > MINIMAP_SIZE / 2 ? "left" : "right"}
            style={{ left: tooltip.x > MINIMAP_SIZE / 2 ? tooltip.x - 10 : tooltip.x + 10, top: Math.max(tooltip.y - 8, 22) }}>
            {tooltip.text}
          </span>
        )}
      </div>
      <div className="office-minimap-bar">
        <button type="button" className="office-minimap-btn" onClick={() => changeZoom(-1)} disabled={zoom >= ZOOM_MAX_M}
          aria-label={t("society.office.minimap_zoom_out")} title={t("society.office.minimap_zoom_out")}>
          <Minus aria-hidden size={13} />
        </button>
        <span className="office-minimap-zoom" aria-live="polite">{zoomLabel}</span>
        <button type="button" className="office-minimap-btn" onClick={() => changeZoom(1)} disabled={zoom <= ZOOM_MIN_M}
          aria-label={t("society.office.minimap_zoom_in")} title={t("society.office.minimap_zoom_in")}>
          <Plus aria-hidden size={13} />
        </button>
        <button type="button" className="office-minimap-btn office-minimap-open" onClick={onOpenMap}
          aria-keyshortcuts="M" title={t("society.office.minimap_open_map_label")}>
          <MapIcon aria-hidden size={13} />
          <span>{t("society.office.minimap_open_map")}</span>
        </button>
      </div>
    </div>
  );
}
