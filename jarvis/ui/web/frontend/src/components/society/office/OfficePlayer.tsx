/**
 * The person's own character: walks with WASD/arrows (camera-relative, Shift
 * runs) or by clicking the floor, and interacts with whatever is nearby (E).
 * Zooming out never requires walking — everything stays clickable from afar.
 */
import { useEffect, useMemo, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { DoubleSide, Vector3, type Group, type Mesh, type MeshBasicMaterial } from "three";
import type { FigureDrive } from "../figures/FigureRig";
import { ToyFigure } from "./ToyFigure";
import type { ToyLook } from "./toyFigureModel";
import { findPath, isWalkable, nearestWalkable, type NavGrid } from "./officeNav";
import { applySeparation, clearOfBodies, separation, stepMover, turnToward } from "./officeMotion";
import { officeSession, player, sameSelection, useOfficeStore, type Selection } from "./officeStore";
import { agentPositions, bodiesExcept } from "./walkerRegistry";
import { OFFICE_FIGURE_HEIGHT_M } from "./OfficeAgents";
import type { OfficeLayout } from "./officeLayout";

/** The person's pace: a brisk walk, and a sprint on Shift (m/s). */
export const PLAYER_WALK_SPEED = 2.0;
export const PLAYER_SPRINT_SPEED = 4.4;

/** Talk range to an agent, in metres. */
export const AGENT_TALK_RANGE = 1.8;

/** Play range around the spot in front of an arcade screen, in metres. */
export const ARCADE_PLAY_RANGE = 0.9;

const MOVE_KEYS: Record<string, [number, number]> = {
  KeyW: [0, 1], ArrowUp: [0, 1], KeyS: [0, -1], ArrowDown: [0, -1],
  KeyA: [-1, 0], ArrowLeft: [-1, 0], KeyD: [1, 0], ArrowRight: [1, 0],
};

/** Keys typed into a field, a dialog or any other DOM control never walk the character. */
export function ownsKeyboard(target: EventTarget | null): boolean {
  // An open modal owns every key, wherever focus happens to sit.
  if (typeof document !== "undefined" && document.querySelector("[role='dialog'][data-state='open'], [role='alertdialog'][data-state='open']")) return true;
  if (!(target instanceof HTMLElement)) return false;
  if (target.isContentEditable) return true;
  const tag = target.tagName;
  return tag === "INPUT" || tag === "TEXTAREA" || tag === "SELECT" || !!target.closest("[role='dialog']");
}

/** Would stepping to `next` bring the character closer to any agent than it is now at `from`? */
function closerToAnyone(next: { x: number; z: number }, from: { x: number; z: number }): boolean {
  for (const p of bodiesExcept(null, null)) {
    if (Math.hypot(next.x - p.x, next.z - p.z) < Math.hypot(from.x - p.x, from.z - p.z)) return true;
  }
  return false;
}

/** Pressed movement keys, tracked on the window while the office is awake. */
function useMoveKeys(enabled: boolean, onInteract: () => void) {
  const pressed = useRef(new Set<string>());
  const run = useRef(false);
  useEffect(() => {
    if (!enabled) { pressed.current.clear(); return; }
    const down = (event: KeyboardEvent) => {
      if (ownsKeyboard(event.target) || event.ctrlKey || event.metaKey || event.altKey) return;
      run.current = event.shiftKey;
      if (event.code in MOVE_KEYS) { pressed.current.add(event.code); event.preventDefault(); }
      else if (event.code === "KeyE" && !event.repeat) { onInteract(); event.preventDefault(); }
    };
    const up = (event: KeyboardEvent) => { pressed.current.delete(event.code); run.current = event.shiftKey; };
    // A key held while the window loses focus never sees its keyup; forget
    // everything then, or the character walks on by itself. (Not on
    // visibilitychange: the desktop WebView reports visible windows as hidden.)
    const release = () => pressed.current.clear();
    window.addEventListener("keydown", down);
    window.addEventListener("keyup", up);
    window.addEventListener("blur", release);
    return () => {
      release();
      window.removeEventListener("keydown", down);
      window.removeEventListener("keyup", up);
      window.removeEventListener("blur", release);
    };
  }, [enabled, onInteract]);
  return { pressed, run };
}

function nearestInteractable(layout: OfficeLayout): Selection | null {
  let best: Selection | null = null;
  let bestDistance = Infinity;
  for (const cp of layout.checkpoints) {
    const d = Math.hypot(cp.x - player.x, cp.z - player.z);
    if (d <= cp.radius && d < bestDistance) { best = { kind: "checkpoint", id: cp.id }; bestDistance = d; }
  }
  for (const [id, p] of agentPositions) {
    const d = Math.hypot(p.x - player.x, p.z - player.z);
    if (d <= AGENT_TALK_RANGE && d < bestDistance) { best = { kind: "agent", id }; bestDistance = d; }
  }
  for (const item of layout.furniture) {
    if (item.kind !== "arcade") continue;
    // The cabinet's screen faces its local +z; you play standing in front of it.
    const fx = item.x + Math.sin(item.rotationY) * 0.85, fz = item.z + Math.cos(item.rotationY) * 0.85;
    const d = Math.hypot(fx - player.x, fz - player.z);
    if (d <= ARCADE_PLAY_RANGE && d < bestDistance) { best = { kind: "arcade", id: item.id }; bestDistance = d; }
  }
  return best;
}

export function OfficePlayer({ layout, grid, look, name, awake, reduced }: {
  layout: OfficeLayout; grid: NavGrid; look: ToyLook; name: string; awake: boolean; reduced: boolean;
}) {
  const group = useRef<Group>(null);
  const ring = useRef<Mesh>(null);
  const drive = useRef<FigureDrive>({ mode: "idle", speed: 0 });
  const camera = useThree((s) => s.camera);
  const forward = useMemo(() => new Vector3(), []);
  const lastWalk = useRef(0);
  const nearbyRef = useRef<Selection | null>(null);
  const interact = useMemo(() => () => {
    const nearby = nearbyRef.current;
    if (nearby) useOfficeStore.getState().select(nearby);
  }, []);
  const { pressed, run } = useMoveKeys(awake, interact);

  // Arrive by the elevator once per app run; coming back to the map keeps the
  // character where it was, unless a changed floor plan put that spot in a wall.
  useEffect(() => {
    player.path = []; player.moving = false;
    if (officeSession.playerPlaced && isWalkable(grid, player)) return;
    const start = (officeSession.playerPlaced ? nearestWalkable(grid, player) : null)
      ?? nearestWalkable(grid, layout.spawn) ?? layout.spawn;
    player.x = start.x; player.z = start.z;
    if (!officeSession.playerPlaced) player.heading = Math.PI;
    officeSession.playerPlaced = true;
  }, [grid, layout.spawn]);

  useFrame((_, rawDt) => {
    const dt = Math.min(rawDt, 0.1);
    const store = useOfficeStore.getState();
    // Click-to-move / "walk there" requests.
    if (store.walkTo && store.walkTo.seq !== lastWalk.current) {
      lastWalk.current = store.walkTo.seq;
      player.path = findPath(grid, player, store.walkTo.point) ?? [];
    }
    // Keyboard movement, relative to where the camera looks.
    let ix = 0, iz = 0;
    for (const code of pressed.current) { ix += MOVE_KEYS[code][0]; iz += MOVE_KEYS[code][1]; }
    const speed = run.current ? PLAYER_SPRINT_SPEED : PLAYER_WALK_SPEED;
    let moved = 0;
    if (ix !== 0 || iz !== 0) {
      player.path = [];
      camera.getWorldDirection(forward);
      forward.y = 0;
      if (forward.lengthSq() < 1e-6) forward.set(0, 0, -1);
      forward.normalize();
      // Right = forward × up.
      const rx = -forward.z, rz = forward.x;
      let dx = forward.x * iz + rx * ix, dz = forward.z * iz + rz * ix;
      const len = Math.hypot(dx, dz);
      dx /= len; dz /= len;
      const step = speed * dt;
      const nx = player.x + dx * step, nz = player.z + dz * step;
      // Slide along walls and around people: full move, else each axis alone.
      // Moving away from someone you already touch is always allowed, so nobody gets stuck.
      const free = (p: { x: number; z: number }) => isWalkable(grid, p)
        && (clearOfBodies(p, bodiesExcept(null, null)) || !closerToAnyone(p, player));
      if (free({ x: nx, z: nz })) { player.x = nx; player.z = nz; moved = step; }
      else if (free({ x: nx, z: player.z })) { player.x = nx; moved = Math.abs(dx * step); }
      else if (free({ x: player.x, z: nz })) { player.z = nz; moved = Math.abs(dz * step); }
      player.heading = turnToward(player.heading, Math.atan2(dx, dz), 12 * dt);
      if (!store.follow) store.setFollow(true);
    } else if (player.path.length > 0) {
      const result = stepMover(player, speed, dt);
      moved = result.moved;
      applySeparation(player, separation(player, player.heading, bodiesExcept(null, null)), dt, (q) => isWalkable(grid, q));
    }
    player.moving = moved > 0;
    drive.current.mode = moved > 0 ? "walk" : "idle";
    drive.current.speed = moved / Math.max(dt, 1e-3);
    if (group.current) {
      group.current.position.set(player.x, 0, player.z);
      group.current.rotation.y = player.heading;
    }
    if (ring.current) (ring.current.material as MeshBasicMaterial).opacity = reduced ? 0.8 : 0.6 + Math.sin(performance.now() / 400) * 0.2;
    // What can the character reach right now?
    const nearby = nearestInteractable(layout);
    if (!sameSelection(nearby, nearbyRef.current)) { nearbyRef.current = nearby; store.setNearby(nearby); }
  });

  return (
    <group ref={group}>
      <mesh ref={ring} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.02, 0]}>
        <ringGeometry args={[0.42, 0.52, 40]} />
        <meshBasicMaterial color="#f5b83d" transparent opacity={0.8} side={DoubleSide} depthWrite={false} />
      </mesh>
      <ToyFigure look={look} drive={drive} paused={!awake} heightM={OFFICE_FIGURE_HEIGHT_M} />
      <Html center position={[0, OFFICE_FIGURE_HEIGHT_M + 0.35, 0]} zIndexRange={[25, 0]}>
        <span className="office-plate office-plate-player" data-office-ui>
          <span className="office-plate-badge" style={{ background: "#f5b83d" }} aria-hidden>★</span>
          <span className="office-plate-name">{name}</span>
        </span>
      </Html>
    </group>
  );
}
