import { navigationSnapshotSchema, roverRideSchema, vehicleRecordSchema } from "./navigationApi";
import { WORLD } from "./world";

export const roverAgent = {
  command_id: "approach-one", request_id: "approach-one", agent_id: "agent-one", trace_id: "trace",
  world_id: WORLD.world_id, station_id: "rover-a-board", mode: "pedestrian", graph_version: WORLD.navigation.version,
  graph_signature: "a".repeat(64), state: "arrived", presence: "placed", position: [282, 58.006, 75],
  current_node: "rover-a-board", edge_id: null, next_node: null, edge_progress: 0, reason: "",
};
export const roverVehicle = vehicleRecordSchema.parse({ ...roverAgent, vehicle_id: "outpost-rover", command_id: "vehicle-one",
  ride_id: null, mode: "rover", position: [282, 58.006, 74.8], current_node: "rover-a-dock" });
export const roverRide = roverRideSchema.parse({
  ride_id: "ride-one", request_id: "reservation-one", agent_id: "agent-one", vehicle_id: "outpost-rover", trace_id: "trace",
  origin_dock_id: "outpost-a", destination_dock_id: null, approach_command_id: "approach-one", state: "ready_to_board",
  attached: false, created_ms: 1, updated_ms: 2, deadline_ms: 10000, reason: "",
});
export function roverSnapshot(rides = [roverRide], vehicles = [{ ...roverVehicle, ride_id: "ride-one" }]) {
  return navigationSnapshotSchema.parse({ world_id: WORLD.world_id, schema_version: 1, graph_version: WORLD.navigation.version,
    graph_signature: "a".repeat(64), seq: 1, commands: [roverAgent], occupancies: [], vehicles, rides });
}
