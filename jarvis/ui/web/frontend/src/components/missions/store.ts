/**
 * Zustand-Store fuer den Mission-Control-View.
 *
 * Wichtig: xterm.js-Terminal-Instanzen leben NICHT im Store. Sie werden in
 * einer Module-Level-Map (terminalRegistry) gehalten — sonst verursacht jeder
 * PTY-Stream-Chunk einen React-Re-Render der gesamten View. Der Store haelt
 * nur die seri­ali­sier­bare Mission-/Event-/Verdict-State.
 */
import { create } from "zustand";
import { subscribeWithSelector } from "zustand/middleware";
import type {
  CriticVerdictReady,
  EventEnvelope,
  MissionState,
  MissionStateChanged,
  MissionSummary,
  OpenClawWorkerSnapshot,
} from "@/types/missions";

const MAX_EVENTS_PER_MISSION = 500;

interface MissionsStore {
  missions: Record<string, MissionSummary>;
  eventsByMission: Record<string, EventEnvelope[]>;
  verdictsByMission: Record<string, CriticVerdictReady[]>;
  workerSnapshotsByMission: Record<string, OpenClawWorkerSnapshot[]>;
  selectedMissionId: string | null;
  selectedWorkerId: string | null;
  lastSeq: number;
  connected: boolean;

  setMissions: (m: MissionSummary[]) => void;
  upsertMission: (m: MissionSummary) => void;
  applyEvent: (env: EventEnvelope) => void;
  setMissionDetail: (
    missionId: string,
    events: EventEnvelope[],
    verdicts: CriticVerdictReady[],
    workerSnapshots?: OpenClawWorkerSnapshot[],
  ) => void;
  selectMission: (id: string | null) => void;
  selectWorker: (id: string | null) => void;
  setConnected: (b: boolean) => void;
  reset: () => void;
}

export const useMissionsStore = create<MissionsStore>()(
  subscribeWithSelector((set) => ({
    missions: {},
    eventsByMission: {},
    verdictsByMission: {},
    workerSnapshotsByMission: {},
    selectedMissionId: null,
    selectedWorkerId: null,
    lastSeq: 0,
    connected: false,

    setMissions: (list) =>
      set(() => {
        const missions: Record<string, MissionSummary> = {};
        for (const m of list) missions[m.id] = m;
        return { missions };
      }),

    upsertMission: (m) =>
      set((state) => ({
        missions: { ...state.missions, [m.id]: m },
      })),

    applyEvent: (env) =>
      set((state) => {
        const seq = env.seq ?? 0;
        const lastSeq = seq > state.lastSeq ? seq : state.lastSeq;

        const prevEvents = state.eventsByMission[env.mission_id] ?? [];
        const nextEvents = [...prevEvents, env];
        if (nextEvents.length > MAX_EVENTS_PER_MISSION) {
          nextEvents.splice(0, nextEvents.length - MAX_EVENTS_PER_MISSION);
        }

        const eventsByMission = {
          ...state.eventsByMission,
          [env.mission_id]: nextEvents,
        };

        let verdictsByMission = state.verdictsByMission;
        if (env.payload.event_type === "CriticVerdictReady") {
          const verdict = env.payload as CriticVerdictReady;
          const prev = verdictsByMission[env.mission_id] ?? [];
          verdictsByMission = {
            ...verdictsByMission,
            [env.mission_id]: [...prev, verdict],
          };
        }

        let missions = state.missions;
        const existing = missions[env.mission_id];
        if (env.payload.event_type === "MissionStateChanged") {
          const change = env.payload as MissionStateChanged;
          const newState = normalizeState(change.to_state);
          if (existing) {
            missions = {
              ...missions,
              [env.mission_id]: { ...existing, state: newState },
            };
          } else {
            missions = {
              ...missions,
              [env.mission_id]: {
                id: env.mission_id,
                prompt: "(unbekannt)",
                state: newState,
                language: "de",
                created_ms: env.ts_ms,
                iteration: 0,
                cost_usd: 0,
              },
            };
          }
        } else if (env.payload.event_type === "MissionDispatched" && !existing) {
          const dispatched = env.payload;
          missions = {
            ...missions,
            [env.mission_id]: {
              id: env.mission_id,
              prompt: dispatched.prompt,
              state: "PENDING",
              language: dispatched.language,
              created_ms: env.ts_ms,
              iteration: 0,
              cost_usd: 0,
              parent_mission_id: dispatched.parent_mission_id,
            },
          };
        } else if (env.payload.event_type === "MissionApproved" && existing) {
          const approved = env.payload;
          missions = {
            ...missions,
            [env.mission_id]: {
              ...existing,
              state: "APPROVED",
              cost_usd: approved.cost_usd,
            },
          };
        } else if (
          env.payload.event_type === "WorkerCorrectionRequired" &&
          existing
        ) {
          const corr = env.payload;
          missions = {
            ...missions,
            [env.mission_id]: { ...existing, iteration: corr.iteration },
          };
        }

        return { lastSeq, eventsByMission, verdictsByMission, missions };
      }),

    setMissionDetail: (missionId, events, verdicts, workerSnapshots) =>
      set((state) => ({
        eventsByMission: { ...state.eventsByMission, [missionId]: events },
        verdictsByMission: { ...state.verdictsByMission, [missionId]: verdicts },
        workerSnapshotsByMission: workerSnapshots
          ? {
              ...state.workerSnapshotsByMission,
              [missionId]: workerSnapshots,
            }
          : state.workerSnapshotsByMission,
      })),

    selectMission: (id) => set({ selectedMissionId: id, selectedWorkerId: null }),
    selectWorker: (id) => set({ selectedWorkerId: id }),
    setConnected: (b) => set({ connected: b }),
    reset: () =>
      set({
        missions: {},
        eventsByMission: {},
        verdictsByMission: {},
        workerSnapshotsByMission: {},
        selectedMissionId: null,
        selectedWorkerId: null,
        lastSeq: 0,
      }),
  })),
);

function normalizeState(s: string): MissionState {
  const upper = s.toUpperCase();
  switch (upper) {
    case "PENDING":
    case "RUNNING":
    case "CRITIQUING":
    case "LOOPING":
    case "APPROVED":
    case "FAILED":
    case "CANCELLED":
    case "TIMED_OUT":
      return upper as MissionState;
    default:
      return "PENDING";
  }
}

export function selectMissionList(state: MissionsStore): MissionSummary[] {
  return Object.values(state.missions).sort((a, b) => b.created_ms - a.created_ms);
}

export function selectActiveCount(state: MissionsStore): number {
  let n = 0;
  for (const m of Object.values(state.missions)) {
    if (m.state !== "APPROVED" && m.state !== "FAILED" && m.state !== "CANCELLED" && m.state !== "TIMED_OUT") {
      n++;
    }
  }
  return n;
}
