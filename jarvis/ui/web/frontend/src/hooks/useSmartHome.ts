/**
 * The Smart Home section's data: devices, rooms and connection health.
 *
 * Reads `/api/smarthome/overview`, which answers all three in one round trip on
 * purpose — each of them fans out to a hub on the user's own home network,
 * often a Raspberry Pi, and three separate fetches would make it answer the
 * same question three times.
 *
 * Polling is bound to `useDocumentVisible`: a minimised window must not keep
 * asking someone's house what it is doing. Coming back rebuilds the interval,
 * and building it again IS the catch-up read.
 *
 * Optimistic updates are deliberate. A light switched from the UI has to look
 * switched immediately — a hub takes a few hundred milliseconds to confirm, and
 * a toggle that visibly lags is the single thing that makes a smart-home app
 * feel broken. The server's own answer (`changed`) then overwrites the guess,
 * so a command the hardware refused snaps back rather than lying.
 */
import { useCallback, useEffect, useRef, useState } from "react";

import { useDocumentVisible } from "@/hooks/useDocumentVisible";

/** Kept as plain strings, matching the backend's enum policy: an unknown value
 *  renders generically instead of dropping the device off the screen. */
export type DeviceKind = string;
export type Capability = string;
export type CommandName = string;

export interface SmartDevice {
  id: string;
  provider: string;
  native_id: string;
  name: string;
  kind: DeviceKind;
  capabilities: Capability[];
  commands: CommandName[];
  state: Record<string, unknown>;
  room: string | null;
  room_id: string | null;
  reachable: boolean;
  manufacturer: string | null;
  model: string | null;
  unit: string | null;
}

/**
 * A room as the USER arranged it — not as a hub reports it.
 *
 * `device_ids` rather than embedded devices: the overview already ships every
 * device once, and a second copy inside each room would let the two disagree
 * the moment a lamp is switched. The view joins them by id instead.
 */
export interface SmartRoom {
  id: string;
  name: string;
  icon: string;
  color: string;
  floor: string | null;
  order: number;
  provider_rooms: string[];
  devices: string[];
  excluded_devices: string[];
  favorites: string[];
  temperature_device: string | null;
  humidity_device: string | null;
  device_ids: string[];
  device_count: number;
  active_count: number;
  temperature: number | null;
  humidity: number | null;
}

/** A hub area no room has adopted yet. Offered on screen, never taken silently. */
export interface RoomSuggestion {
  id: string;
  name: string;
  provider: string;
  device_count: number;
}

export interface RoomLayoutPayload {
  rooms: SmartRoom[];
  /** Device ids that belong to no room — offered for assignment, never dropped. */
  unassigned: string[];
  suggestions: RoomSuggestion[];
  /** The editor's vocabulary, supplied by the backend so it cannot drift. */
  icons: string[];
  colors: string[];
}

export type ConnectionState =
  | "connected"
  | "not_configured"
  | "needs_reauth"
  | "unreachable";

export interface ProviderStatus {
  provider: string;
  display_name: string;
  state: ConnectionState;
  detail: string | null;
  device_count: number | null;
  longevity: string;
  extra?: Record<string, string>;
}

export interface Ecosystem {
  id: string;
  display_name: string;
  logo_slug: string;
  reachability: "direct" | "via_hub" | "planned" | "unavailable";
  connection: string;
  longevity: string;
  note: string;
  covers: string;
  /** Curation rank: "hub" is the one universal route, then popular, then technical. */
  tier: string;
  /** Brand colour as hex WITHOUT "#", for the logo tile. */
  logo_color: string;
  /** What the user actually does, in order. Empty when nothing can be done. */
  setup_steps: string[];
  docs_url: string | null;
}

/**
 * Hand a Home Assistant token to the marketplace, from inside this section.
 *
 * Deliberately posts to the marketplace endpoint rather than growing a second
 * credential store: Home Assistant is ONE connection, and a token pasted here
 * has to be the same token the plugin surface sees. What this section owns is
 * the moment of asking — sending someone to a different screen to type it was
 * the "you cannot connect anything" complaint.
 */
export async function connectHomeAssistant(
  instanceUrl: string,
  token: string,
): Promise<void> {
  const res = await fetch("/api/marketplace/plugins/home_assistant/connect/pat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ token, instance_url: instanceUrl }),
  });
  if (!res.ok) {
    const body = await res.json().catch(() => ({}));
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
}

interface Overview extends RoomLayoutPayload {
  providers: ProviderStatus[];
  devices: SmartDevice[];
  connected: boolean;
  commands: CommandName[];
}

export interface CommandOutcome {
  ok: boolean;
  device_id: string;
  command: CommandName;
  changed: SmartDevice[];
  error: string | null;
  /**
   * The server refused because the command physically opens something that
   * cannot be undone remotely (unlocking a door, opening a garage) and wants an
   * explicit acknowledgement.
   *
   * Which commands those are is decided ON THE SERVER and never mirrored here:
   * a copy of that list in the frontend is a copy that can drift, and the half
   * that drifts is always the one guarding the front door. The UI simply reacts
   * to the flag by asking, then resends with `confirm`.
   */
  requires_confirmation?: boolean;
}

const POLL_MS = 8_000;

async function unwrap<T>(res: Response): Promise<T> {
  const body = await res.json().catch(() => ({}));
  if (!res.ok) {
    throw new Error((body as { detail?: string }).detail ?? `HTTP ${res.status}`);
  }
  return body as T;
}

const EMPTY_LAYOUT: RoomLayoutPayload = {
  rooms: [],
  unassigned: [],
  suggestions: [],
  icons: [],
  colors: [],
};

export function useSmartHome() {
  const [devices, setDevices] = useState<SmartDevice[]>([]);
  const [layout, setLayout] = useState<RoomLayoutPayload>(EMPTY_LAYOUT);
  const [providers, setProviders] = useState<ProviderStatus[]>([]);
  const [connected, setConnected] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const visible = useDocumentVisible();
  // A poll that lands while a command is still in flight would overwrite the
  // optimistic state with the hub's pre-command answer, making the switch flick
  // back and then forward again. Counting in-flight commands suppresses exactly
  // those ticks and nothing else.
  const inFlight = useRef(0);

  const refetch = useCallback(async (force = false) => {
    try {
      const res = await fetch(`/api/smarthome/overview${force ? "?refresh=true" : ""}`);
      const data = await unwrap<Overview>(res);
      setDevices(data.devices ?? []);
      setLayout(readLayout(data));
      setProviders(data.providers ?? []);
      setConnected(Boolean(data.connected));
      setError(null);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setLoading(false);
    }
  }, []);

  useEffect(() => {
    void refetch();
  }, [refetch]);

  useEffect(() => {
    if (!visible) return;
    const handle = window.setInterval(() => {
      if (inFlight.current === 0) void refetch();
    }, POLL_MS);
    return () => window.clearInterval(handle);
  }, [visible, refetch]);

  const execute = useCallback(
    async (
      deviceId: string,
      command: CommandName,
      args: Record<string, unknown> = {},
      optimistic?: Record<string, unknown>,
      confirm = false,
    ): Promise<CommandOutcome> => {
      inFlight.current += 1;
      if (optimistic) {
        setDevices((prev) =>
          prev.map((d) =>
            d.id === deviceId ? { ...d, state: { ...d.state, ...optimistic } } : d,
          ),
        );
      }
      try {
        const res = await fetch(
          `/api/smarthome/devices/${encodeURI(deviceId)}/command`,
          {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ command, args, confirm }),
          },
        );
        const outcome = await unwrap<CommandOutcome>(res);
        if (outcome.changed?.length) {
          // The platform's own read-back wins over the guess — including when
          // it disagrees, which is how a refused command visibly snaps back.
          const byId = new Map(outcome.changed.map((d) => [d.id, d]));
          setDevices((prev) => prev.map((d) => byId.get(d.id) ?? d));
        } else if (!outcome.ok && optimistic) {
          void refetch(true);
        }
        return outcome;
      } catch (e) {
        if (optimistic) void refetch(true);
        return {
          ok: false,
          device_id: deviceId,
          command,
          changed: [],
          error: (e as Error).message,
        };
      } finally {
        inFlight.current -= 1;
      }
    },
    [refetch],
  );

  /**
   * Run a room mutation and adopt the layout it returns.
   *
   * Every room endpoint answers with the WHOLE layout rather than the one room
   * it touched, and that is deliberate: moving a device out of room A and into
   * room B changes both, so a response carrying only B would leave A showing a
   * lamp that has left it until the next poll.
   */
  const mutateRooms = useCallback(
    async (path: string, init?: RequestInit): Promise<string | null> => {
      inFlight.current += 1;
      try {
        const res = await fetch(`/api/smarthome/rooms${path}`, {
          headers: { "Content-Type": "application/json" },
          ...init,
        });
        const data = await unwrap<RoomLayoutPayload>(res);
        setLayout(readLayout(data));
        return null;
      } catch (e) {
        // Returned, not thrown: the caller is a dialog that has to show this
        // next to the field the user typed into, and an unhandled rejection
        // would close it with no explanation at all.
        return (e as Error).message;
      } finally {
        inFlight.current -= 1;
      }
    },
    [],
  );

  const rooms = useCallback(
    () => ({
      create: (body: RoomDraft) =>
        mutateRooms("", { method: "POST", body: JSON.stringify(body) }),
      update: (roomId: string, body: Partial<RoomDraft>) =>
        mutateRooms(`/${roomId}`, { method: "PATCH", body: JSON.stringify(body) }),
      remove: (roomId: string) => mutateRooms(`/${roomId}`, { method: "DELETE" }),
      reorder: (roomIds: string[]) =>
        mutateRooms("/reorder", {
          method: "POST",
          body: JSON.stringify({ room_ids: roomIds }),
        }),
      importAreas: () => mutateRooms("/import", { method: "POST" }),
      assign: (roomId: string, deviceIds: string[]) =>
        mutateRooms(`/${roomId}/devices`, {
          method: "POST",
          body: JSON.stringify({ device_ids: deviceIds }),
        }),
      unassign: (roomId: string, deviceId: string) =>
        mutateRooms(`/${roomId}/devices/${encodeURI(deviceId)}`, { method: "DELETE" }),
      toggleFavorite: (roomId: string, deviceId: string) =>
        mutateRooms(`/${roomId}/favorites`, {
          method: "POST",
          body: JSON.stringify({ device_id: deviceId }),
        }),
    }),
    [mutateRooms],
  );

  /**
   * One verb across a whole room, run by the SERVER.
   *
   * Done server-side rather than by looping here, because the loop belongs
   * wherever the assistant can reach it too: "everything off in the kitchen"
   * has to mean the same thing spoken as it does tapped.
   */
  const executeRoom = useCallback(
    async (roomId: string, command: CommandName): Promise<boolean> => {
      inFlight.current += 1;
      // Optimistic across the room, for the same reason a single switch is: a
      // dozen lamps that visibly lag make the whole section feel broken.
      // Membership comes from the LAYOUT, not from `room_id` — that field is
      // the hub's area, which is exactly what the room layer may override.
      const members = new Set(layout.rooms.find((r) => r.id === roomId)?.device_ids ?? []);
      const wanted = command === "turn_on";
      if (command === "turn_on" || command === "turn_off") {
        setDevices((prev) =>
          prev.map((d) =>
            members.has(d.id) &&
            typeof d.state.on_off === "boolean" &&
            d.commands.includes(command)
              ? { ...d, state: { ...d.state, on_off: wanted } }
              : d,
          ),
        );
      }
      try {
        const res = await fetch(`/api/smarthome/rooms/${roomId}/command`, {
          method: "POST",
          headers: { "Content-Type": "application/json" },
          body: JSON.stringify({ command }),
        });
        const outcome = await unwrap<{ changed: SmartDevice[]; ok: boolean }>(res);
        if (outcome.changed?.length) {
          const byId = new Map(outcome.changed.map((d) => [d.id, d]));
          setDevices((prev) => prev.map((d) => byId.get(d.id) ?? d));
        }
        return outcome.ok;
      } catch {
        // The guess is now unverifiable — ask the house what actually happened
        // rather than leaving a room drawn as off that never switched.
        void refetch(true);
        return false;
      } finally {
        inFlight.current -= 1;
      }
    },
    [refetch, layout.rooms],
  );

  return {
    devices,
    layout,
    rooms: layout.rooms,
    providers,
    connected,
    loading,
    error,
    refetch,
    execute,
    executeRoom,
    roomActions: rooms(),
  };
}

/** The fields a room editor writes. Mirrors the backend's create/patch body. */
export interface RoomDraft {
  name?: string;
  icon?: string;
  color?: string;
  floor?: string | null;
  provider_rooms?: string[];
  devices?: string[];
  favorites?: string[];
  temperature_device?: string | null;
  humidity_device?: string | null;
}

/** Normalise a layout response so a missing key never crashes a render. */
function readLayout(data: Partial<RoomLayoutPayload>): RoomLayoutPayload {
  return {
    rooms: data.rooms ?? [],
    unassigned: data.unassigned ?? [],
    suggestions: data.suggestions ?? [],
    icons: data.icons ?? [],
    colors: data.colors ?? [],
  };
}

/** The static reachability map. Fetched once — it never changes at runtime. */
export function useEcosystems() {
  const [ecosystems, setEcosystems] = useState<Ecosystem[]>([]);
  const [loading, setLoading] = useState(true);

  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/smarthome/ecosystems");
        const data = await unwrap<{ ecosystems: Ecosystem[] }>(res);
        if (!cancelled) setEcosystems(data.ecosystems ?? []);
      } catch {
        if (!cancelled) setEcosystems([]);
      } finally {
        if (!cancelled) setLoading(false);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);

  return { ecosystems, loading };
}

export async function setDemoMode(enabled: boolean): Promise<boolean> {
  const res = await fetch("/api/smarthome/demo", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ enabled }),
  });
  const data = await unwrap<{ demo_mode: boolean }>(res);
  return data.demo_mode;
}
