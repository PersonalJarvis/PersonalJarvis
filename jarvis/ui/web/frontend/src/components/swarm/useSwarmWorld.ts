import { startTransition, useEffect, useState } from "react";
import { jitteredDelay, requestConnect } from "@/lib/connectBudget";
import { validSnapshot, teamPath, world } from "./api";
import { terminalState, type WorldSnapshot } from "./types";

export type ConnectionState = "loading" | "live" | "reconnecting" | "background" | "history" | "error";
export interface WorldState { snapshot: WorldSnapshot | null; connection: ConnectionState; error: string }

/** Each mounted team owns exactly one subscription. No ordinary-agent stores. */
export function useSwarmWorld(teamId: string, group: string, refresh: number, awake: boolean): WorldState {
  const [state, setState] = useState<WorldState>({ snapshot: null, connection: "loading", error: "" });
  useEffect(() => {
    let disposed = false;
    let socket: WebSocket | null = null;
    let cancelConnect: (() => void) | undefined;
    let retry = 0;
    let revision = "0";
    let generation: string | undefined;
    let pending: WorldSnapshot | null = null;
    let flushTimer: ReturnType<typeof setTimeout> | undefined;
    let lastFlush = 0;
    let currentConnection: ConnectionState = awake ? "loading" : "background";
    let currentError = "";
    const abort = new AbortController();
    setState(current => ({ snapshot: current.snapshot?.team.id === teamId ? current.snapshot : null, connection: awake ? "loading" : "background", error: "" }));
    const status = (connection: ConnectionState, error = "") => {
      currentConnection = connection; currentError = error;
      if (!disposed) setState(s => ({ ...s, connection, error }));
    };
    const accept = (snapshot: WorldSnapshot) => {
      if (disposed || !validSnapshot(snapshot, teamId)) return;
      const nextGeneration = snapshot.team.storage_generation ?? "";
      if (generation === nextGeneration && BigInt(snapshot.revision) < BigInt(revision)) return;
      generation = nextGeneration;
      revision = snapshot.revision;
      pending = snapshot;
      if (flushTimer) return;
      flushTimer = setTimeout(() => {
        flushTimer = undefined;
        if (disposed || !pending) return;
        const next = pending; pending = null; lastFlush = performance.now();
        startTransition(() => setState({ snapshot: next, connection: terminalState(next.team.state) ? "history" : currentConnection, error: currentError }));
      }, Math.max(0, 500 - (performance.now() - lastFlush)));
    };
    const reconnect = () => {
      if (disposed || !awake) return;
      status("reconnecting", currentError);
      cancelConnect?.();
      cancelConnect = requestConnect(() => { void synchronize(); }, jitteredDelay(retry++, 700, 30000));
    };
    const openSocket = () => {
      if (disposed || !awake) return;
      const url = new URL(`${teamPath(teamId)}/ws`, window.location.href);
      url.protocol = url.protocol === "https:" ? "wss:" : "ws:";
      url.searchParams.set("after", revision);
      if (group) url.searchParams.set("group", group);
      try { socket = new WebSocket(url); }
      catch (error) { status("error", String(error)); reconnect(); return; }
      const current = socket;
      current.onmessage = event => {
        if (disposed || socket !== current || typeof event.data !== "string") return;
        if (new TextEncoder().encode(event.data).byteLength > 65536) { status("error", "Swarm update exceeded the projection budget"); current.close(); return; }
        try {
          const frame = JSON.parse(event.data);
          if (frame.team_id !== teamId) { status("error", "Rejected a mismatched team update"); current.close(); return; }
          if (frame.type === "gap") { current.close(); return; }
          if (frame.type === "snapshot" && validSnapshot(frame.snapshot, teamId)) {
            retry = 0; status("live"); accept(frame.snapshot);
            if (terminalState(frame.snapshot.team.state)) { current.onclose = null; current.close(); socket = null; }
          }
          else if (frame.type !== "heartbeat") { status("error", "Invalid Swarm update"); current.close(); }
        } catch { status("error", "Invalid Swarm update"); current.close(); }
      };
      current.onerror = () => current.close();
      current.onclose = () => { if (socket === current) { socket = null; reconnect(); } };
    };
    async function synchronize() {
      try {
        const snapshot = await world(teamId, group, abort.signal);
        if (disposed) return;
        status("live");
        accept(snapshot);
        if (!terminalState(snapshot.team.state)) {
          cancelConnect = requestConnect(openSocket, jitteredDelay(0, 250));
        }
      } catch (error) { if (!disposed) { status("error", String(error)); reconnect(); } }
    }
    if (awake) void synchronize();
    return () => {
      disposed = true; abort.abort(); cancelConnect?.();
      if (flushTimer) clearTimeout(flushTimer);
      if (socket) { socket.onclose = null; socket.onmessage = null; socket.onerror = null; socket.close(); }
    };
  }, [teamId, group, refresh, awake]);
  return state;
}
