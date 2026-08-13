/**
 * How a device looks and reads on screen — icon, label, and the one line of
 * state worth putting under its name.
 *
 * Every lookup here is TOLERANT of a value it does not know, and that is the
 * point. The backend owns the device vocabulary and may add a kind or a
 * capability at any time; a frontend that switched exhaustively on those would
 * make a new device type *disappear* from the list, which is the enum-drift bug
 * class this repo has paid for repeatedly. An unknown kind gets a neutral icon
 * and its raw name — visibly generic, never missing.
 */
import {
  Blinds,
  Camera,
  CircleDot,
  Fan,
  Lightbulb,
  Lock,
  MousePointerClick,
  Plug,
  Power,
  Speaker,
  Sparkles,
  Thermometer,
  Waves,
  type LucideIcon,
} from "lucide-react";

import type { SmartDevice } from "@/hooks/useSmartHome";

const KIND_ICONS: Record<string, LucideIcon> = {
  light: Lightbulb,
  switch: Power,
  outlet: Plug,
  climate: Thermometer,
  cover: Blinds,
  lock: Lock,
  sensor: Waves,
  binary_sensor: CircleDot,
  media_player: Speaker,
  speaker: Speaker,
  fan: Fan,
  vacuum: Sparkles,
  camera: Camera,
  scene: Sparkles,
  script: Sparkles,
  button: MousePointerClick,
};

export function iconForKind(kind: string): LucideIcon {
  return KIND_ICONS[kind] ?? CircleDot;
}

/** Is this device "active" right now — the thing the card's glow reflects? */
export function isActive(device: SmartDevice): boolean {
  const state = device.state ?? {};
  if (typeof state.on_off === "boolean") return state.on_off;
  if (typeof state.open === "boolean") return state.open;
  // A locked door is SAFE, not active — highlighting it like a burning lamp
  // would train the eye to read "glowing = something is on" and then mislead on
  // the one device where that matters most.
  if (typeof state.locked === "boolean") return !state.locked;
  return false;
}

/**
 * The single line under the device name.
 *
 * Returns a translation KEY plus its values rather than a finished string, so
 * the caller does the interpolation with the live locale — the same rule the
 * rest of the app follows: no English assembled in a component.
 */
export interface StateLine {
  key: string;
  values?: Record<string, string | number>;
  /** Plain fallback when the key is missing from a locale. */
  fallback: string;
}

export function stateLine(device: SmartDevice): StateLine | null {
  const s = device.state ?? {};

  if (device.kind === "climate") {
    const target = typeof s.target_temperature === "number" ? s.target_temperature : null;
    const current =
      typeof s.current_temperature === "number" ? s.current_temperature : null;
    if (target !== null && current !== null) {
      return {
        key: "smarthome.state.climate",
        values: { current, target },
        fallback: `${current}° now, set to ${target}°`,
      };
    }
    if (target !== null) {
      return {
        key: "smarthome.state.climate_target",
        values: { target },
        fallback: `Set to ${target}°`,
      };
    }
  }

  if (device.kind === "cover" && typeof s.position === "number") {
    return {
      key: "smarthome.state.cover",
      values: { position: s.position },
      fallback: `${s.position}% open`,
    };
  }

  if (typeof s.locked === "boolean") {
    return s.locked
      ? { key: "smarthome.state.locked", fallback: "Locked" }
      : { key: "smarthome.state.unlocked", fallback: "Unlocked" };
  }

  if (device.capabilities.includes("read_only")) {
    const value = s.value;
    if (value !== undefined && value !== null && value !== "") {
      const unit = device.unit ? ` ${device.unit}` : "";
      return {
        key: "smarthome.state.reading",
        values: { value: `${String(value)}${unit}` },
        fallback: `${String(value)}${unit}`,
      };
    }
  }

  if (typeof s.on_off === "boolean") {
    if (s.on_off && typeof s.brightness === "number") {
      return {
        key: "smarthome.state.on_at",
        values: { brightness: s.brightness },
        fallback: `On, ${s.brightness}%`,
      };
    }
    return s.on_off
      ? { key: "smarthome.state.on", fallback: "On" }
      : { key: "smarthome.state.off", fallback: "Off" };
  }

  return null;
}

/**
 * Resolve a state line against the active locale.
 *
 * `useT()` takes a key and nothing else — it interpolates only the assistant
 * name — so the value substitution happens here. Falls back to the English
 * `fallback` when the key is missing from a locale, which is the same rule the
 * sidebar's `fallbackLabel` follows: a missing translation shows a real
 * sentence, never the raw key.
 */
export function renderStateLine(
  t: (key: string) => string,
  line: StateLine,
): string {
  const resolved = t(line.key);
  const template = resolved === line.key ? line.fallback : resolved;
  return template.replace(/\{(\w+)\}/g, (_, name: string) =>
    String(line.values?.[name] ?? ""),
  );
}

/** Group devices by room, with the room-less ones last under a null key. */
export function groupByRoom(devices: SmartDevice[]): [string | null, SmartDevice[]][] {
  const groups = new Map<string | null, SmartDevice[]>();
  for (const device of devices) {
    const key = device.room ?? null;
    const bucket = groups.get(key);
    if (bucket) bucket.push(device);
    else groups.set(key, [device]);
  }
  return [...groups.entries()].sort(([a], [b]) => {
    if (a === null) return 1;
    if (b === null) return -1;
    return a.localeCompare(b);
  });
}
