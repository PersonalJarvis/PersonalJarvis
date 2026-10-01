/**
 * Desk monitors on the coding floor: each shows its pane live, drawn as the
 * Agentic IDE draws it — the pane's header (status, CLI mark, title, what it is
 * doing) over its terminal in the IDE's font and colours — while the agent
 * sits at the desk, and the header over a quiet screen otherwise. A click on a
 * screen opens that agent's command panel, where it can be prompted.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useFrame, type ThreeEvent } from "@react-three/fiber";
import { CanvasTexture, SRGBColorSpace } from "three";
import { useT } from "@/i18n";
import { useThemeValue } from "@/hooks/useTheme";
import type { PaneScreen } from "@/lib/paneScreensApi";
import { usePaneTitle } from "@/store/paneRecaps";
import { storedTerminalAppearance } from "@/components/agentic/terminalThemes";
import type { DeskSlot, Point } from "./officeLayout";
import type { PaneOccupant } from "./codingFloor";
import { drawTerminalScreen, monitorPalette, paneDot, TERMINAL_H, TERMINAL_W, type MonitorPalette } from "./terminalScreen";
import { useAgentMark } from "./monitorMarks";
import { MAX_SCREEN_TARGETS, usePaneScreens, type PaneScreenTarget } from "./usePaneScreens";
import { seatedAtDesk } from "./walkerRegistry";

/** Mirrors LiveMonitors: same place as the instanced screen, a hair in front of it. */
const SCREEN_Y = 1.18, SCREEN_Z = -0.2095, SCREEN_W = 0.66, SCREEN_H = 0.38;
/** How often the nearest-seated set is re-read, in seconds. */
const NEAR_CHECK_S = 0.4;
/** The "for 3 min" in the headers moves on this clock. */
const META_TICK_MS = 30_000;

/** "Working · 3 min": the pane's state and how long it has held it at `nowMs`, as the IDE header says it. */
function useMeta(occupant: PaneOccupant, nowMs: number): string {
  const t = useT();
  const state = t(`society.office.pane_state_${occupant.stateKey}`);
  const at = occupant.pane.activity_since;
  if (!at) return state;
  const seconds = Math.max(0, nowMs / 1000 - at);
  const since = seconds < 60 ? t("society.office.since_now")
    : seconds < 3600 ? t("society.office.since_minutes").replace("{0}", String(Math.floor(seconds / 60)))
      : seconds < 86400 ? t("society.office.since_hours").replace("{0}", String(Math.floor(seconds / 3600)))
        : t("society.office.since_days").replace("{0}", String(Math.floor(seconds / 86400)));
  return `${state} · ${since}`;
}

/** The nearest seated agents to the camera, at most `limit`, as a sorted id list. Pure. */
export function nearestSeated(candidates: { id: string; distance: number }[], limit = MAX_SCREEN_TARGETS): string[] {
  return [...candidates].sort((a, b) => a.distance - b.distance).slice(0, limit).map((c) => c.id).sort();
}

function Screen({ desk, occupant, screen, palette, nowMs, onOpen }: {
  desk: DeskSlot; occupant: PaneOccupant; screen: PaneScreen | undefined; palette: MonitorPalette; nowMs: number;
  onOpen: (agentId: string, screen: Point & { y: number }, facing: number) => void;
}) {
  const hovered = useRef(false);
  const agentId = occupant.agent.agentId;
  const [seated, setSeated] = useState(() => seatedAtDesk.has(agentId));
  const turn = desk.facing === "north" ? 0 : Math.PI;
  const surface = useMemo(() => {
    const canvas = typeof document !== "undefined" ? document.createElement("canvas") : null;
    const ctx = canvas?.getContext("2d") ?? null;
    if (!canvas || !ctx) return null;
    canvas.width = TERMINAL_W; canvas.height = TERMINAL_H;
    const texture = new CanvasTexture(canvas);
    texture.colorSpace = SRGBColorSpace;
    texture.anisotropy = 4;
    return { ctx, texture };
  }, []);
  useEffect(() => () => {
    surface?.texture.dispose();
    if (hovered.current) document.body.style.cursor = "";
  }, [surface]);
  const { pane } = occupant;
  // The header reads exactly like the pane's IDE header: its title, else its topic, else the agent.
  const title = usePaneTitle(pane.workspace_id, pane.name) || pane.recap.trim() || occupant.agent.name;
  const meta = useMeta(occupant, nowMs);
  const mark = useAgentMark(pane.agent, palette.ink);
  const dot = paneDot(pane);
  const shown = seated ? screen : undefined;
  useEffect(() => {
    if (!surface) return;
    drawTerminalScreen(surface.ctx, { title, meta, dot, mark, palette }, shown);
    surface.texture.needsUpdate = true;
  }, [surface, title, meta, dot, mark, palette, shown]);
  // Seating is a per-frame module set; only its flips reach React.
  useFrame(() => {
    const now = seatedAtDesk.has(agentId);
    if (now !== seated) setSeated(now);
  });
  if (!surface) return null;
  // World position of the screen centre, for the zoom.
  const sx = desk.x + Math.sin(turn) * SCREEN_Z, sz = desk.z + Math.cos(turn) * SCREEN_Z;
  const click = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 6) return;
    event.stopPropagation();
    onOpen(agentId, { x: sx, y: SCREEN_Y, z: sz }, turn);
  };
  return (
    <group position={[desk.x, 0, desk.z]} rotation={[0, turn, 0]}>
      <mesh position={[0, SCREEN_Y, SCREEN_Z]} onClick={click}
        onPointerOver={() => { hovered.current = true; document.body.style.cursor = "pointer"; }}
        onPointerOut={() => { hovered.current = false; document.body.style.cursor = ""; }}>
        <planeGeometry args={[SCREEN_W, SCREEN_H]} />
        <meshBasicMaterial map={surface.texture} toneMapped={false} />
      </mesh>
    </group>
  );
}

export function TerminalMonitors({ desks, occupants, awake, onOpen }: {
  desks: DeskSlot[]; occupants: ReadonlyMap<string, PaneOccupant>; awake: boolean;
  onOpen: (agentId: string, screen: Point & { y: number }, facing: number) => void;
}) {
  const [near, setNear] = useState<string[]>([]);
  const nextCheck = useRef(0);
  useFrame(({ camera, clock }) => {
    if (clock.elapsedTime < nextCheck.current) return;
    nextCheck.current = clock.elapsedTime + NEAR_CHECK_S;
    const candidates: { id: string; distance: number }[] = [];
    for (const desk of desks) {
      if (!desk.agentId || !occupants.has(desk.agentId) || !seatedAtDesk.has(desk.agentId)) continue;
      candidates.push({ id: desk.agentId, distance: Math.hypot(camera.position.x - desk.x, camera.position.y - SCREEN_Y, camera.position.z - desk.z) });
    }
    const next = nearestSeated(candidates);
    if (next.length !== near.length || next.some((id, i) => id !== near[i])) setNear(next);
  });
  const targets = useMemo<PaneScreenTarget[]>(() => near.flatMap((agentId) => {
    const pane = occupants.get(agentId)?.pane;
    return pane ? [{ agentId, workspaceId: pane.workspace_id, key: pane.key }] : [];
  }), [near, occupants]);
  const screens = usePaneScreens(targets, awake);
  // The IDE's pane appearance: the reader's stored choice, else the app's theme.
  const appTheme = useThemeValue();
  const appearance = storedTerminalAppearance() ?? appTheme;
  const palette = useMemo(() => monitorPalette(appearance), [appearance]);
  const [nowMs, setNowMs] = useState(() => Date.now());
  useEffect(() => {
    const timer = setInterval(() => setNowMs(Date.now()), META_TICK_MS);
    return () => clearInterval(timer);
  }, []);
  return (
    <group>
      {desks.map((desk) => {
        const occupant = desk.agentId ? occupants.get(desk.agentId) : undefined;
        return occupant
          ? <Screen key={desk.id} desk={desk} occupant={occupant} screen={screens.get(occupant.agent.agentId)}
            palette={palette} nowMs={nowMs} onOpen={onOpen} />
          : null;
      })}
    </group>
  );
}
