/**
 * Everything the level system draws inside the Verse's canvas: what the
 * person, their pet and every agent wear (trail, aura, gadget), the burst of
 * a level-up and the floating "+XP". It only reads the shared positions the
 * walkers already publish (`player`, `agentPositions`, `petBody`); it never
 * moves anyone.
 */
import { useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { OFFICE_FIGURE_HEIGHT_M } from "../office/OfficeAgents";
import { player } from "../office/officeStore";
import { agentPositions, petBody } from "../office/walkerRegistry";
import { CosmeticTrail, type FlairSource, type TrailKind } from "./effects/CosmeticTrail";
import { CosmeticAura, CosmeticGadget, type AuraKind, type GadgetKind } from "./effects/CosmeticWear";
import { LevelUpBurst, XpPopup } from "./effects/LevelUpBurst";
import { equippedFor, type Loadout } from "./cosmetics";
import { agentSubject, PERSON_SUBJECT, petSubject, reportWorldAction } from "./progressionApi";
import { useProgression } from "./progressionStore";
import type { SubjectKind } from "./levelCatalog";

/** The lead is the person's pet, never an agent with its own flair. */
const LEAD_AGENT_ID = "jarvis";

const personSource: FlairSource = () => ({ x: player.x, z: player.z, heading: player.heading });
const petSource: FlairSource = () => (petBody.active ? { x: petBody.x, z: petBody.z } : null);
const petTopSource: FlairSource = () => (petBody.active ? { x: petBody.x, z: petBody.z, y: petBody.top } : null);

/** Agents carry no heading in the registry; it is read off their motion, and held while they stand. */
function agentSource(agentId: string): FlairSource {
  let last: { x: number; z: number } | null = null;
  let heading = 0;
  return () => {
    const p = agentPositions.get(agentId);
    if (!p) return null;
    if (last) {
      const dx = p.x - last.x, dz = p.z - last.z;
      if (dx * dx + dz * dz > 1e-5) heading = Math.atan2(dx, dz);
    }
    last = { x: p.x, z: p.z };
    return { x: p.x, z: p.z, heading };
  };
}

function Flair({ loadout, source, topSource, top, scale, paused, reduced }: {
  loadout: Loadout; source: FlairSource; topSource?: FlairSource; top: number; scale: number; paused: boolean; reduced: boolean;
}) {
  return (
    <>
      {loadout.aura && <CosmeticAura kind={loadout.aura as AuraKind} source={source} scale={scale} paused={paused} reduced={reduced} />}
      {loadout.trail && !reduced && <CosmeticTrail kind={loadout.trail as TrailKind} source={source} scale={scale} paused={paused} />}
      {/* The wings are worn on the figure itself (AngelWings in ToyFigure's back slot), not floated beside it. */}
      {loadout.gadget && loadout.gadget !== "gadget_wings" && <CosmeticGadget kind={loadout.gadget as GadgetKind} source={topSource ?? source} top={top} scale={scale}
        paused={paused} reduced={reduced} />}
    </>
  );
}

function AgentFlair({ agentId, loadout, paused, reduced }: { agentId: string; loadout: Loadout; paused: boolean; reduced: boolean }) {
  const source = useMemo(() => agentSource(agentId), [agentId]);
  return <Flair loadout={loadout} source={source} top={OFFICE_FIGURE_HEIGHT_M + 0.02} scale={1} paused={paused} reduced={reduced} />;
}

/** Where a subject stands, for a burst or a "+XP", and how tall it is there. */
function locate(subjectId: string, petId: string): { source: FlairSource; height: number; scale: number } | null {
  if (subjectId === PERSON_SUBJECT) return { source: personSource, height: OFFICE_FIGURE_HEIGHT_M, scale: 1 };
  if (subjectId === petSubject(petId)) return petBody.active ? { source: petSource, height: petBody.top, scale: 0.6 } : null;
  if (subjectId.startsWith("agent:")) {
    const id = subjectId.slice("agent:".length);
    if (!agentPositions.has(id)) return null;
    return { source: () => agentPositions.get(id) ?? null, height: OFFICE_FIGURE_HEIGHT_M, scale: 1 };
  }
  return null;
}

/** Every this many metres walked with the pet at your side, the pet earns a little XP (metered on the server). */
export const WALK_REPORT_M = 50;

/** Counts the metres the person walks while their pet is drawn beside them. */
function useWalkTogether(awake: boolean) {
  const state = useRef({ last: null as { x: number; z: number } | null, metres: 0 });
  useFrame(() => {
    const s = state.current;
    if (!awake || !petBody.active) { s.last = null; return; }
    if (s.last) {
      const step = Math.hypot(player.x - s.last.x, player.z - s.last.z);
      // An elevator ride or a floor switch is no walk.
      if (step < 1) s.metres += step;
    }
    s.last = { x: player.x, z: player.z };
    if (s.metres >= WALK_REPORT_M) {
      s.metres = 0;
      void reportWorldAction("walk_together");
    }
  });
}

const BURST_LABEL: Record<SubjectKind, string> = { person: "society.level.burst_you", pet: "society.level.burst_pet", agent: "society.level.burst_agent" };

export function ProgressionLayer({ agents, awake, reduced }: { agents: ReadonlyMap<string, SocietyAgent>; awake: boolean; reduced: boolean }) {
  const t = useT();
  const rewards = useProgression((s) => s.snapshot?.rewards);
  const subjects = useProgression((s) => s.subjects);
  const choices = useProgression((s) => s.choices);
  const petId = useProgression((s) => s.petId);
  const bursts = useProgression((s) => s.bursts);
  const popups = useProgression((s) => s.popups);
  const paused = !awake;

  // Popups and bursts expire on the scene's own clock.
  useFrame(() => useProgression.getState().expire(performance.now()));
  useWalkTogether(awake);

  const personLevel = subjects[PERSON_SUBJECT]?.level ?? 1;
  const petLevel = subjects[petSubject(petId)]?.level ?? 1;
  const personLoadout = useMemo(() => (rewards ? equippedFor(rewards, "person", personLevel, choices.person) : {}), [rewards, personLevel, choices.person]);
  const petLoadout = useMemo(() => (rewards ? equippedFor(rewards, "pet", petLevel, choices.pet) : {}), [rewards, petLevel, choices.pet]);
  const agentLoadouts = useMemo(() => {
    if (!rewards) return [];
    const out: { agentId: string; loadout: Loadout }[] = [];
    for (const id of agents.keys()) {
      if (id === LEAD_AGENT_ID) continue;
      const level = subjects[agentSubject(id)]?.level;
      if (!level || level < 2) continue;
      const loadout = equippedFor(rewards, "agent", level);
      if (loadout.aura || loadout.trail || loadout.gadget) out.push({ agentId: id, loadout });
    }
    return out;
  }, [rewards, agents, subjects]);

  return (
    <group>
      <Flair loadout={personLoadout} source={personSource} top={OFFICE_FIGURE_HEIGHT_M + 0.02} scale={1} paused={paused} reduced={reduced} />
      <Flair loadout={petLoadout} source={petSource} topSource={petTopSource} top={0.5} scale={0.55} paused={paused} reduced={reduced} />
      {agentLoadouts.map(({ agentId, loadout }) => <AgentFlair key={agentId} agentId={agentId} loadout={loadout} paused={paused} reduced={reduced} />)}
      {bursts.map((burst) => {
        const where = locate(burst.subjectId, petId);
        if (!where) return null;
        return <LevelUpBurst key={burst.id} source={where.source} kind={burst.kind} level={burst.level} label={t(BURST_LABEL[burst.kind])}
          height={where.height} scale={where.scale} reduced={reduced} />;
      })}
      {popups.map((popup) => {
        const where = locate(popup.subjectId, petId);
        const at = where?.source();
        if (!where || !at) return null;
        const kind = popup.subjectId === PERSON_SUBJECT ? "person" : popup.subjectId.startsWith("pet:") ? "pet" : "agent";
        return <XpPopup key={popup.id} x={at.x} z={at.z} y={where.height + 0.3} xp={popup.xp} kind={kind} />;
      })}
    </group>
  );
}
