import { useEffect, useRef, useState } from "react";
import { useQueryClient } from "@tanstack/react-query";
import { useT } from "@/i18n";
import { MarsApiError } from "./api";
import { currentRoverRide, type NavigationSnapshot } from "./navigationApi";
import { clearRoverAttempt, readRoverAttempt, saveRoverAttempt, submitRoverAction, type RoverAttempt } from "./roverApi";
import { NAVIGATION_KEY } from "./useMarsNavigation";
import { WORLD } from "./world";

const buttonClass = "rounded border border-border px-2 py-1 disabled:opacity-50";

/** Rides move actual agents. The local player capsule has no server ride authority. */
export function MarsRoverPanel({ agentId, active, snapshot, online }: {
  agentId: string; active: boolean; snapshot?: NavigationSnapshot; online: boolean;
}) {
  const t = useT();
  const client = useQueryClient();
  const [pending, setPending] = useState(readRoverAttempt);
  const [busy, setBusy] = useState(false), [failed, setFailed] = useState(false);
  const sending = useRef(false);
  const [vehicleId, setVehicleId] = useState(WORLD.navigation.rovers[0]?.id ?? "");
  const [destination, setDestination] = useState("");
  const ride = currentRoverRide(snapshot, agentId);
  const vehicle = snapshot?.vehicles.find((row) => row.vehicle_id === (ride?.vehicle_id ?? vehicleId));
  const dock = WORLD.navigation.rover_docks.find((row) => row.node_id === vehicle?.current_node && !vehicle?.edge_id);
  const destinations = WORLD.navigation.rover_docks.filter((row) => row.id !== dock?.id);
  const selectedDestination = destinations.some((row) => row.id === destination) ? destination : destinations[0]?.id ?? "";
  const enabled = online && active && !!agentId && !busy;
  const available = !!vehicle && vehicle.presence === "placed" && !vehicle.ride_id && !!dock;
  const travelReady = ride?.attached && ["boarded", "arrived", "stopped", "exit_blocked"].includes(ride.state);
  const exitReady = travelReady;

  useEffect(() => {
    // Only reservation identities are represented in ride receipts. Never infer
    // acknowledgement of a later action from a coincidentally matching state.
    if (!online || busy || pending?.action !== "reserve") return;
    if (snapshot?.rides.some((row) => row.agent_id === pending.agent_id && row.request_id === pending.request_id)) {
      clearRoverAttempt(pending.request_id); setPending(null); setFailed(false);
    }
  }, [snapshot, online, busy, pending]);

  const send = async (attempt: RoverAttempt, recovery = false) => {
    if (sending.current || busy || !online) return;
    // Exact replay can recover a lost acknowledgement even after the original
    // agent is archived. The server replays accepted identities before checking
    // current authority; an unaccepted request still has to pass that authority.
    if (!recovery && (!enabled || pending || attempt.agent_id !== agentId)) return;
    sending.current = true; setBusy(true); setFailed(false); setPending(attempt); saveRoverAttempt(attempt);
    try {
      await submitRoverAction(attempt);
      clearRoverAttempt(attempt.request_id); setPending(null);
      await client.invalidateQueries({ queryKey: NAVIGATION_KEY });
    } catch (error) {
      if (error instanceof MarsApiError && [400, 401, 403, 404, 409, 422].includes(error.status)) {
        clearRoverAttempt(attempt.request_id); setPending(null);
        await client.invalidateQueries({ queryKey: NAVIGATION_KEY });
      }
      setFailed(true);
    } finally { sending.current = false; setBusy(false); }
  };
  const action = (kind: "board" | "travel" | "cancel" | "exit") => {
    if (!ride || pending) return;
    const base = { request_id: crypto.randomUUID(), agent_id: agentId, ride_id: ride.ride_id };
    void send(kind === "travel" ? { ...base, action: kind, destination_dock_id: selectedDestination } : { ...base, action: kind });
  };
  return <section className="grid gap-2 border-t border-border pt-3" aria-label={t("society.mars.rover_title")} data-mars-ui>
    <h3 className="text-sm font-semibold">{t("society.mars.rover_title")}</h3>
    <p className="text-xs text-muted-foreground">{t("society.mars.rover_scope")}</p>
    {!active && <p role="status" className="text-xs">{t("society.mars.rover_active_required")}</p>}
    {pending && <div className="grid gap-2 text-sm">
      <p>{t("society.mars.rover_pending")} · {pending.agent_id} · {t(`society.mars.rover_action_${pending.action}`)}</p>
      <button type="button" disabled={!online || busy} className={buttonClass} onClick={() => void send(pending, true)}>{t("society.mars.rover_retry")}</button>
      {pending.agent_id !== agentId && <p className="text-xs text-muted-foreground">{t("society.mars.rover_pending_other")}</p>}
    </div>}
    {!ride && <>
      <label className="grid gap-1 text-sm">{t("society.mars.rover_vehicle")}
        <select value={vehicleId} disabled={!enabled || !!pending} onChange={(event) => setVehicleId(event.target.value)} className="rounded border border-border bg-background p-2">
          {WORLD.navigation.rovers.map((row) => <option key={row.id} value={row.id}>{t(`society.mars.rover_vehicle_${row.id}`)}</option>)}
        </select>
      </label>
      <button type="button" disabled={!enabled || !!pending || !available} className={buttonClass} onClick={() => void send({ action: "reserve", agent_id: agentId, request_id: crypto.randomUUID(), vehicle_id: vehicleId })}>{t("society.mars.rover_reserve")}</button>
      {online && !available && <p role="status" className="text-xs">{t("society.mars.rover_unavailable")}</p>}
    </>}
    {ride && <>
      <p role="status" className="text-sm">{t(`society.mars.rover_state_${ride.state}`)}</p>
      {ride.attached && <p className="text-xs text-muted-foreground">{t("society.mars.rover_attached")}</p>}
      <label className="grid gap-1 text-sm">{t("society.mars.rover_route")}
        <select value={selectedDestination} disabled={!enabled || !!pending || !travelReady} onChange={(event) => setDestination(event.target.value)} className="rounded border border-border bg-background p-2">
          {destinations.map((row) => <option key={row.id} value={row.id}>{t(`society.mars.rover_dock_${row.id}`)}</option>)}
        </select>
      </label>
      <div className="flex flex-wrap gap-2 text-sm">
        {ride.state === "ready_to_board" && <button type="button" disabled={!enabled || !!pending} className={buttonClass} onClick={() => action("board")}>{t("society.mars.rover_board")}</button>}
        {travelReady && <button type="button" disabled={!enabled || !!pending || !selectedDestination} className={buttonClass} onClick={() => action("travel")}>{t("society.mars.rover_travel")}</button>}
        <button type="button" disabled={!enabled || !!pending || ride.state === "stopped"} className={buttonClass} onClick={() => action("cancel")}>{t(ride.attached ? "society.mars.rover_stop" : "society.mars.rover_cancel")}</button>
        {exitReady && <button type="button" disabled={!enabled || !!pending} className={buttonClass} onClick={() => action("exit")}>{t("society.mars.rover_exit")}</button>}
      </div>
      {ride.state === "exit_blocked" && <p className="text-xs text-muted-foreground">{t("society.mars.rover_exit_blocked")}</p>}
    </>}
    {!online && <p role="status" className="text-sm">{t("society.mars.rover_offline")}</p>}
    {failed && <p role="alert" className="text-sm">{t(pending ? "society.mars.rover_uncertain" : "society.mars.request_failed")}</p>}
  </section>;
}
