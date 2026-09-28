/**
 * Agents at their desks: the stored character, seated, with a floating name
 * pill and a floor ring in the colour of its run state. The office is a
 * projection of the roster — nothing here moves an agent or starts work.
 */
import { useMemo, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import { DoubleSide, Vector3, type Group, type Mesh, type MeshBasicMaterial } from "three";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { FigureRig, type FigureDrive } from "../figures/FigureRig";
import { shufflePalette, type FigureRecipe } from "../figures/figureRecipe";
import { OFFICE } from "./officePalette";
import type { DeskSlot } from "./officeLayout";

/** Seated figures share one toy scale, so the desks read the same everywhere. */
export const OFFICE_FIGURE_HEIGHT_M = 1.3;

/** Agents without a stored character get a stable chibi look from their id. */
export function fallbackRecipe(agentId: string): FigureRecipe {
  let hash = 0;
  for (const ch of agentId) hash = (hash * 31 + ch.charCodeAt(0)) >>> 0;
  return { contract: 1, archetype: "biped", base: "chibi", parts: {}, palette: shufflePalette((hash % 10_000) / 10_000) };
}

const RING_COLOUR = {
  working: OFFICE.ringWorking,
  idle: OFFICE.ringIdle,
  waiting: OFFICE.ringWaiting,
  paused: OFFICE.ringPaused,
} as const;

function StatusRing({ state, animate }: { state: SocietyAgent["state"]; animate: boolean }) {
  const ref = useRef<Mesh>(null);
  useFrame(({ clock }) => {
    if (!ref.current || !animate || state !== "working") return;
    (ref.current.material as MeshBasicMaterial).opacity = 0.55 + Math.sin(clock.elapsedTime * 3) * 0.3;
  });
  return (
    <mesh ref={ref} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0.015, 0]}>
      <ringGeometry args={[0.46, 0.54, 40]} />
      <meshBasicMaterial color={RING_COLOUR[state]} transparent opacity={0.8} side={DoubleSide} depthWrite={false} />
    </mesh>
  );
}

/** Nameplate scale by camera distance: readable up close, compact in the overview, never huge. */
export function plateScale(distance: number): number {
  return Math.min(1.05, Math.max(0.8, 24 / Math.max(1, distance)));
}

function Nameplate({ agent, onSelect }: { agent: SocietyAgent; onSelect: (id: string) => void }) {
  const t = useT();
  const lead = agent.tier === "lead";
  const anchor = useRef<Group>(null);
  const plate = useRef<HTMLButtonElement>(null);
  const last = useRef(0);
  const world = useMemo(() => new Vector3(), []);
  useFrame(({ camera }) => {
    if (!anchor.current || !plate.current) return;
    const scale = plateScale(camera.position.distanceTo(anchor.current.getWorldPosition(world)));
    if (Math.abs(scale - last.current) < 0.02) return;
    last.current = scale;
    plate.current.style.transform = `scale(${scale.toFixed(2)})`;
  });
  return (
    <group ref={anchor} position={[0, OFFICE_FIGURE_HEIGHT_M + 0.35, 0]}>
    <Html center zIndexRange={[20, 0]}>
      <button ref={plate} type="button" data-office-ui className="office-plate" data-state={agent.state}
        onClick={(event) => { event.stopPropagation(); onSelect(agent.agentId); }}
        aria-label={t("society.office.open_agent").replace("{0}", agent.name)}>
        <span className="office-plate-badge" style={{ background: agent.palette.primary }} aria-hidden>
          {lead ? "★" : agent.name.slice(0, 1).toUpperCase()}
        </span>
        <span className="office-plate-name">{agent.name}</span>
        <span className="office-plate-state" title={t(`society.office.state_${agent.state}`)}>
          <i aria-hidden />
          {agent.state === "idle" ? null : <em>{t(`society.office.state_${agent.state}`)}</em>}
        </span>
      </button>
    </Html>
    </group>
  );
}

function SeatedAgent({ agent, desk, awake, reduced, onSelect }: {
  agent: SocietyAgent; desk: DeskSlot; awake: boolean; reduced: boolean; onSelect: (id: string) => void;
}) {
  const recipe = useMemo(() => agent.figure ?? fallbackRecipe(agent.agentId), [agent.figure, agent.agentId]);
  // Seated at every state; the monitor and the ring carry the difference.
  const drive = useRef<FigureDrive>({ mode: "sit", speed: 0 });
  return (
    <group position={[desk.x, 0, desk.z]} rotation={[0, desk.facing === "north" ? 0 : Math.PI, 0]}>
      <group position={[0, 0, 0.72]}>
        <StatusRing state={agent.state} animate={awake && !reduced} />
        {/* Figures face +z; the agent looks at its monitor on the local -z side. */}
        <group rotation={[0, Math.PI, 0]} position={[0, SEAT_LIFT_M, SEAT_BACK_M]}
          onClick={(event) => { event.stopPropagation(); onSelect(agent.agentId); }}
          onPointerOver={() => { document.body.style.cursor = "pointer"; }}
          onPointerOut={() => { document.body.style.cursor = ""; }}>
          <FigureRig recipe={recipe} drive={drive} paused={!awake || reduced} heightM={OFFICE_FIGURE_HEIGHT_M} />
        </group>
        <Nameplate agent={agent} onSelect={onSelect} />
      </group>
    </group>
  );
}

/** Seat offsets for the shared "sit" clip, measured in the runtime against the chair. */
export const SEAT_LIFT_M = 0.12;
export const SEAT_BACK_M = 0.04;

export function OfficeAgents({ desks, agents, awake, reduced, onSelect }: {
  desks: DeskSlot[]; agents: ReadonlyMap<string, SocietyAgent>; awake: boolean; reduced: boolean; onSelect: (id: string) => void;
}) {
  return (
    <group>
      {desks.map((desk) => {
        const agent = desk.agentId ? agents.get(desk.agentId) : undefined;
        return agent ? <SeatedAgent key={agent.agentId} agent={agent} desk={desk} awake={awake} reduced={reduced} onSelect={onSelect} /> : null;
      })}
    </group>
  );
}
