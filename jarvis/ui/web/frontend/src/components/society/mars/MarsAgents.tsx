import { useEffect, useRef } from "react";
import { Html } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { Group, Vector3 } from "three";
import { useT } from "@/i18n";
import { type NavigationRecord } from "./navigationApi";

function AgentLocation({ record, name, stale, awake, onSelect, following, followAvailable, onFollow, onStopFollow }: {
  record: NavigationRecord; name: string; stale: boolean; awake: boolean; onSelect?: (id: string) => void;
  following: boolean; followAvailable: boolean; onFollow: (id: string) => void; onStopFollow: () => void;
}) {
  const t = useT();
  const group = useRef<Group>(null);
  const initialPosition = useRef(record.position);
  const previous = useRef(record);
  const remaining = useRef(0);
  const target = useRef(new Vector3(...record.position));
  const { invalidate } = useThree();
  useEffect(() => {
    const old = previous.current;
    target.current.fromArray(record.position);
    // Interpolate only on the same verified segment. A straight shortcut across
    // a corner could pass through geometry; new segments use reported placement.
    remaining.current = !stale && old.edge_id !== null && old.edge_id === record.edge_id && old.current_node === record.current_node ? 0.18 : 0;
    if (!remaining.current) group.current?.position.copy(target.current);
    previous.current = record; invalidate();
  }, [record, stale, awake, invalidate]);
  useFrame((_state, delta) => {
    if (!awake || !group.current || remaining.current <= 0) return;
    const step = Math.min(Math.max(delta, 0), remaining.current);
    group.current.position.lerp(target.current, step / remaining.current);
    remaining.current -= step;
    if (remaining.current > 0) invalidate();
  });
  return <group ref={group} position={initialPosition.current}>
    <Html center position={[0, 2.2, 0]} zIndexRange={[16, 1]}>
      <div className="flex items-center gap-1" data-mars-ui>
      <button type="button" data-mars-ui onClick={(event) => { event.stopPropagation(); onSelect?.(record.agent_id); }}
        className="whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-sm">
        <strong>{name}</strong><span className="ml-2 text-muted-foreground">{t(stale ? "society.mars.last_known_position" : `society.mars.move_state_${record.state}`)}</span>
      </button>
      <button type="button" aria-label={t(following ? "society.mars.stop_follow_agent" : "society.mars.follow_named_agent").replace("{0}", name)} aria-pressed={following}
        disabled={!following && !followAvailable} onClick={(event) => { event.stopPropagation(); if (following) onStopFollow(); else onFollow(record.agent_id); }}
        className="whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-sm disabled:opacity-50">
        {t(following ? "society.mars.stop_follow" : "society.mars.follow")}
      </button>
      </div>
    </Html>
  </group>;
}

/** Truthful location markers; final worker models remain a separate art gate. */
export function MarsAgents({ records, names, stale, awake, onSelect, followAgentId, followAvailable, onFollow, onStopFollow }: {
  records: NavigationRecord[]; names: ReadonlyMap<string, string>; stale: boolean; awake: boolean; onSelect?: (id: string) => void;
  followAgentId: string | null; followAvailable: boolean; onFollow: (id: string) => void; onStopFollow: () => void;
}) {
  return <group>{records.filter((row) => row.presence === "placed").slice(0, 64).map((record) =>
    <AgentLocation key={record.agent_id} record={record} name={names.get(record.agent_id) ?? record.agent_id} stale={stale} awake={awake} onSelect={onSelect}
      following={followAgentId === record.agent_id} followAvailable={followAvailable && names.has(record.agent_id)} onFollow={onFollow} onStopFollow={onStopFollow} />,
  )}</group>;
}
