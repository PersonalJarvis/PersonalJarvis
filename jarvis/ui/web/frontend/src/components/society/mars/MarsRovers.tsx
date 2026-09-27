import { Html } from "@react-three/drei";
import { useT } from "@/i18n";
import type { RoverRideRecord, VehicleRecord } from "./navigationApi";

/** Confirmed vehicle locations, not final rover geometry or client simulation. */
export function MarsRovers({ vehicles, rides, names, stale, followAgentId, followAvailable, onFollow, onStopFollow }: {
  vehicles: VehicleRecord[]; rides: RoverRideRecord[]; names: ReadonlyMap<string, string>; stale: boolean;
  followAgentId: string | null; followAvailable: boolean; onFollow: (id: string) => void; onStopFollow: () => void;
}) {
  const t = useT();
  return <group>{vehicles.filter((vehicle) => vehicle.presence === "placed").map((vehicle) => {
    const rider = rides.find((ride) => ride.attached && ride.ride_id === vehicle.ride_id && ride.vehicle_id === vehicle.vehicle_id);
    const name = rider ? names.get(rider.agent_id) ?? rider.agent_id : "";
    const following = !!rider && followAgentId === rider.agent_id;
    return <group key={vehicle.vehicle_id} position={vehicle.position}>
      <Html center position={[0, 3, 0]} zIndexRange={[16, 1]}>
        <div className="grid gap-1 whitespace-nowrap rounded-md border border-border bg-popover px-2 py-1 text-xs text-popover-foreground shadow-sm" data-mars-ui>
          <strong>{t(`society.mars.rover_vehicle_${vehicle.vehicle_id}`)}</strong>
          <span className="text-muted-foreground">{t(stale ? "society.mars.last_known_position" : "society.mars.rover_marker")}</span>
          {rider && <span>{name} · {t(`society.mars.rover_state_${rider.state}`)}</span>}
          {rider && <button type="button" aria-pressed={following} disabled={!following && (!followAvailable || !names.has(rider.agent_id))}
            onClick={(event) => { event.stopPropagation(); if (following) onStopFollow(); else onFollow(rider.agent_id); }}
            className="rounded border border-border px-2 py-1 disabled:opacity-50">
            {t(following ? "society.mars.stop_follow_agent" : "society.mars.follow_named_agent").replace("{0}", name)}
          </button>}
        </div>
      </Html>
    </group>;
  })}</group>;
}
