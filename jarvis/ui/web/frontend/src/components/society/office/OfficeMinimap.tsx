/**
 * The office minimap: a north-up floor plan in the bottom-right corner.
 *
 * The static floor is drawn once into an offscreen canvas and only redrawn
 * when the layout, the size, the pixel ratio or the theme changes. The moving
 * layer (agents, the person's character, the camera's view) is painted over
 * it at ~10 Hz from the per-frame module state (`agentPositions`, `player`),
 * never from React state, and pauses while collapsed or the document is hidden.
 *
 * Click focuses the camera on a spot (or selects the agent under the pointer),
 * double-click walks the character there.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type MouseEvent as ReactMouseEvent, type PointerEvent as ReactPointerEvent } from "react";
import { ChevronDown, ChevronUp } from "lucide-react";
import { useT } from "@/i18n";
import type { CheckpointKind, OfficeLayout, RoomKind } from "./officeLayout";
import { player, useOfficeStore } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import {
  agentAt, clampToRect, drawMinimapBase, drawMinimapDynamic, mapToWorld, minimapHeightFor, minimapTransform, placeAt,
  resolveMinimapColours, worldToMap, type MapPoint, type MinimapAgentDot, type MinimapAgentState, type MinimapCamera,
  type MinimapColours,
} from "./minimap";

export interface OfficeMinimapAgent { agentId: string; name: string; state: MinimapAgentState }

export interface OfficeMinimapProps {
  layout: OfficeLayout;
  agents: ReadonlyMap<string, OfficeMinimapAgent>;
  selectedId: string | null;
  /** Optional live camera view (read at ~10 Hz); omit to hide the view wedge. */
  camera?: () => MinimapCamera | null;
}

const MAP_WIDTH = 220;
const PADDING = 6;
const FRAME_MS = 100;
const HIT_PX = 8;
const STORAGE_KEY = "jarvis.office.minimap.collapsed";

function readCollapsed(): boolean {
  try {
    return window.localStorage.getItem(STORAGE_KEY) === "1";
  } catch {
    // Storage blocked (private mode, sandboxed WebView): the map just starts expanded.
    return false;
  }
}

function writeCollapsed(collapsed: boolean): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, collapsed ? "1" : "0");
  } catch {
    // Storage blocked: the choice lasts for this session only, which is harmless.
  }
}

function pixelRatio(): number {
  return typeof window !== "undefined" && window.devicePixelRatio > 0 ? window.devicePixelRatio : 1;
}

/** Bumps whenever light/dark mode may have changed: OS preference, or the app's class/data-theme on <html>. */
function useThemeVersion(): number {
  const [version, setVersion] = useState(0);
  useEffect(() => {
    const bump = () => setVersion((v) => v + 1);
    const media = typeof window.matchMedia === "function" ? window.matchMedia("(prefers-color-scheme: dark)") : null;
    media?.addEventListener?.("change", bump);
    const observer = typeof MutationObserver === "function" ? new MutationObserver(bump) : null;
    observer?.observe(document.documentElement, { attributes: true, attributeFilter: ["class", "data-theme"] });
    return () => {
      media?.removeEventListener?.("change", bump);
      observer?.disconnect();
    };
  }, []);
  return version;
}

/** The device pixel ratio, refreshed when the window moves to another screen or zooms. */
function usePixelRatio(): number {
  const [dpr, setDpr] = useState(pixelRatio);
  useEffect(() => {
    const check = () => setDpr((prev) => (prev === pixelRatio() ? prev : pixelRatio()));
    window.addEventListener("resize", check);
    return () => window.removeEventListener("resize", check);
  }, []);
  return dpr;
}

interface Tooltip { x: number; y: number; text: string }

export function OfficeMinimap({ layout, agents, selectedId, camera }: OfficeMinimapProps) {
  const t = useT();
  const [collapsed, setCollapsed] = useState(readCollapsed);
  const [tooltip, setTooltip] = useState<Tooltip | null>(null);
  const cardRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const baseRef = useRef<HTMLCanvasElement | null>(null);
  const coloursRef = useRef<MinimapColours | null>(null);
  const themeVersion = useThemeVersion();
  const dpr = usePixelRatio();

  const height = useMemo(() => minimapHeightFor(layout.bounds, MAP_WIDTH, PADDING), [layout]);
  const transform = useMemo(() => minimapTransform(layout.bounds, MAP_WIDTH, height, PADDING), [layout, height]);

  // The render loop reads these through refs, so a roster refetch never restarts it.
  const live = useRef({ agents, selectedId, camera, transform });
  live.current = { agents, selectedId, camera, transform };

  const dots = useCallback((): MinimapAgentDot[] => {
    const out: MinimapAgentDot[] = [];
    const { agents: roster, selectedId: sel } = live.current;
    for (const agent of roster.values()) {
      const pos = agentPositions.get(agent.agentId);
      if (!pos) continue;
      out.push({ id: agent.agentId, x: pos.x, z: pos.z, state: agent.state, selected: agent.agentId === sel });
    }
    return out;
  }, []);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const base = baseRef.current;
    const colours = coloursRef.current;
    const ctx = canvas?.getContext("2d");
    if (!canvas || !ctx || !colours) return;
    const { transform: tr, camera: cam } = live.current;
    ctx.setTransform(1, 0, 0, 1, 0, 0);
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    if (base) ctx.drawImage(base, 0, 0);
    const ratio = canvas.width / tr.width;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    drawMinimapDynamic(ctx, tr, {
      agents: dots(),
      player: { x: player.x, z: player.z, heading: player.heading },
      camera: cam ? cam() : null,
      colours,
      clear: false,
    });
  }, [dots]);

  // Static layer: layout, size, pixel ratio or theme changed (or the map was just expanded).
  useEffect(() => {
    if (collapsed) return;
    const canvas = canvasRef.current;
    const card = cardRef.current;
    if (!canvas || !card) return;
    canvas.width = Math.round(MAP_WIDTH * dpr);
    canvas.height = Math.round(height * dpr);
    const style = getComputedStyle(card);
    const colours = resolveMinimapColours((name) => style.getPropertyValue(name));
    coloursRef.current = colours;
    const base = document.createElement("canvas");
    base.width = canvas.width;
    base.height = canvas.height;
    const ctx = base.getContext("2d");
    if (!ctx) {
      // No 2D canvas (headless test DOM): the card still renders, just without a picture.
      baseRef.current = null;
      return;
    }
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    drawMinimapBase(ctx, layout, transform, colours);
    baseRef.current = base;
    draw();
  }, [collapsed, layout, transform, height, dpr, themeVersion, draw]);

  // Moving layer at ~10 Hz; stops while collapsed or while the document is hidden.
  useEffect(() => {
    if (collapsed) return;
    let raf = 0;
    let last = 0;
    const tick = (now: number) => {
      raf = requestAnimationFrame(tick);
      if (now - last < FRAME_MS) return;
      last = now;
      draw();
    };
    const start = () => { if (!raf && !document.hidden) raf = requestAnimationFrame(tick); };
    const stop = () => { cancelAnimationFrame(raf); raf = 0; };
    const onVisibility = () => (document.hidden ? stop() : start());
    document.addEventListener("visibilitychange", onVisibility);
    start();
    return () => {
      stop();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [collapsed, draw]);

  const toggle = () => {
    setCollapsed((prev) => {
      writeCollapsed(!prev);
      return !prev;
    });
    setTooltip(null);
  };

  const mapPoint = (event: { clientX: number; clientY: number }): MapPoint | null => {
    const canvas = canvasRef.current;
    if (!canvas) return null;
    const rect = canvas.getBoundingClientRect();
    if (rect.width <= 0 || rect.height <= 0) return null;
    return { x: ((event.clientX - rect.left) / rect.width) * MAP_WIDTH, y: ((event.clientY - rect.top) / rect.height) * height };
  };

  const onClick = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    const at = mapPoint(event);
    if (!at) return;
    const store = useOfficeStore.getState();
    const hit = agentAt(transform, dots(), at, HIT_PX);
    if (hit) {
      store.select({ kind: "agent", id: hit.id });
      store.focusOn({ x: hit.x, z: hit.z });
      return;
    }
    store.focusOn(clampToRect(mapToWorld(transform, at), layout.floor));
  };

  const onDoubleClick = (event: ReactMouseEvent<HTMLCanvasElement>) => {
    const at = mapPoint(event);
    if (!at) return;
    useOfficeStore.getState().requestWalk(clampToRect(mapToWorld(transform, at), layout.floor));
  };

  const checkpointLabels: Record<CheckpointKind, string> = {
    create: t("society.office.cp_create"),
    manage: t("society.office.cp_manage"),
    team: t("society.office.cp_team"),
    wardrobe: t("society.office.cp_wardrobe"),
    lead: t("society.office.cp_lead"),
    break: t("society.office.cp_break"),
  };
  const roomLabels: Record<RoomKind, string> = {
    lead: t("society.office.room_lead"),
    team: t("society.office.room_team"),
    wardrobe: t("society.office.room_wardrobe"),
    reception: t("society.office.room_reception"),
    break: t("society.office.room_break"),
  };
  const youLabel = t("society.office.minimap_you");
  const openSpace = t("society.office.open_space");

  const onPointerMove = (event: ReactPointerEvent<HTMLCanvasElement>) => {
    const at = mapPoint(event);
    if (!at) return;
    let text: string | null = null;
    const hit = agentAt(transform, dots(), at, HIT_PX);
    if (hit) {
      text = agents.get(hit.id)?.name ?? null;
    } else {
      const me = worldToMap(transform, player);
      if ((me.x - at.x) ** 2 + (me.y - at.y) ** 2 <= HIT_PX * HIT_PX) {
        text = youLabel;
      } else {
        const place = placeAt(layout, mapToWorld(transform, at));
        if (place?.kind === "checkpoint") text = checkpointLabels[place.id];
        else if (place?.kind === "room") text = roomLabels[place.id];
        else if (place?.kind === "department") text = place.label || openSpace;
      }
    }
    setTooltip((prev) => {
      if (!text) return null;
      const next = { x: Math.round(at.x), y: Math.round(at.y), text };
      return prev && prev.text === next.text && prev.x === next.x && prev.y === next.y ? prev : next;
    });
  };

  let working = 0;
  for (const agent of agents.values()) if (agent.state === "working") working += 1;
  const summary = t("society.office.minimap_aria").replace("{0}", String(agents.size)).replace("{1}", String(working));
  const title = t("society.office.minimap_title");

  return (
    <div ref={cardRef} className="office-hud office-card office-minimap" data-office-ui data-collapsed={collapsed ? "true" : "false"}>
      <div className="office-minimap-head">
        <span className="office-minimap-title">{title}</span>
        <button type="button" className="office-icon-button office-minimap-toggle" onClick={toggle} aria-expanded={!collapsed}
          aria-label={collapsed ? t("society.office.minimap_expand") : t("society.office.minimap_collapse")}
          title={collapsed ? t("society.office.minimap_expand") : t("society.office.minimap_collapse")}>
          {collapsed ? <ChevronUp aria-hidden size={14} /> : <ChevronDown aria-hidden size={14} />}
        </button>
      </div>
      {!collapsed && (
        <div className="office-minimap-map" style={{ width: MAP_WIDTH, height }}>
          <canvas ref={canvasRef} className="office-minimap-canvas" role="img" aria-label={summary}
            style={{ width: MAP_WIDTH, height }}
            onClick={onClick} onDoubleClick={onDoubleClick} onPointerMove={onPointerMove} onPointerLeave={() => setTooltip(null)} />
          {tooltip && (
            // Flip to the pointer's left on the right half so the label never runs off the card.
            <span className="office-minimap-tooltip" role="presentation" data-side={tooltip.x > MAP_WIDTH / 2 ? "left" : "right"}
              style={{ left: tooltip.x > MAP_WIDTH / 2 ? tooltip.x - 10 : tooltip.x + 10, top: Math.max(tooltip.y - 8, 4) }}>
              {tooltip.text}
            </span>
          )}
        </div>
      )}
    </div>
  );
}
