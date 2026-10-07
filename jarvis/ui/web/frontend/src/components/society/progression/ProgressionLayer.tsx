/**
 * What the level system draws inside the Verse's canvas besides the figures'
 * own uniforms and insignia (those are worn on the body, `regalia/dress.tsx`):
 * the moment of a level-up and the floating "+XP". It only reads the shared
 * positions the walkers already publish (`player`, `agentPositions`,
 * `petBody`); it never moves anyone.
 */
import { useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { OFFICE_FIGURE_HEIGHT_M } from "../office/OfficeAgents";
import { player } from "../office/officeStore";
import { agentPositions, petBody } from "../office/walkerRegistry";
import { LevelUpBurst, XpPopup, type FlairSource } from "./effects/LevelUpBurst";
import { isRankId, rankAt } from "./levelCatalog";
import { PERSON_SUBJECT, petSubject, reportWorldAction } from "./progressionApi";
import { useProgression } from "./progressionStore";

const personSource: FlairSource = () => ({ x: player.x, z: player.z, heading: player.heading });
const petSource: FlairSource = () => (petBody.active ? { x: petBody.x, z: petBody.z } : null);

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

export function ProgressionLayer({ awake, reduced }: { awake: boolean; reduced: boolean }) {
  const titles = useProgression((s) => s.snapshot?.titles);
  const petId = useProgression((s) => s.petId);
  const bursts = useProgression((s) => s.bursts);
  const popups = useProgression((s) => s.popups);

  // Popups and bursts expire on the scene's own clock.
  useFrame(() => useProgression.getState().expire(performance.now()));
  useWalkTogether(awake);

  return (
    <group>
      {bursts.map((burst) => {
        const where = locate(burst.subjectId, petId);
        if (!where) return null;
        const bands = titles?.[burst.kind];
        const rank = isRankId(burst.title) ? burst.title : rankAt(bands, burst.level);
        const promoted = rankAt(bands, burst.previousLevel) !== rank;
        return <LevelUpBurst key={burst.id} source={where.source} kind={burst.kind} level={burst.level} rank={rank} promoted={promoted}
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
