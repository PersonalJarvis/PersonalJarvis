/** Short departures share the ordinary collision-checked movement owner. */
import { useEffect, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { getMotionWorld } from "./locomotion";
import { buildIsland, tileToWorld } from "./islandLayout";
import { attachRetirement, buryRetired, useRetireStore } from "./retireStore";

export function RetirementScene({ paused }: { paused: boolean }) {
  const ceremony = useRetireStore(s => s.ceremony);
  const elapsed = useRef(0);
  useEffect(() => {
    if (!ceremony) return;
    elapsed.current = 0;
    attachRetirement();
    const world = getMotionWorld(), actor = world?.actors.get(ceremony.agentId);
    if (paused || !world || !actor) { useRetireStore.getState().cutShort(); return; }
    actor.exiting = true; actor.state = "idle";
    const positions = Object.values(buildIsland().content.places).map(p => tileToWorld(...p.standTile));
    positions.sort((a, b) => Math.hypot(a[0] - actor.x, a[1] - actor.z) - Math.hypot(b[0] - actor.x, b[1] - actor.z));
    world.target(actor.id, positions[0]);
    return () => { if (useRetireStore.getState().ceremony === ceremony) useRetireStore.getState().cutShort(); };
  }, [ceremony, paused]);
  useFrame((_, dt) => {
    if (!ceremony) return;
    const actor = getMotionWorld()?.actors.get(ceremony.agentId);
    elapsed.current += Math.min(dt, .1);
    if ((actor?.arrived && elapsed.current > 1) || elapsed.current >= 8) {
      // A blocked exit retires in place; never teleport or delay deletion.
      if (actor) actor.hidden = true;
      buryRetired(ceremony.agentId);
      useRetireStore.getState().finish();
    }
  });
  return null;
}
export default RetirementScene;
