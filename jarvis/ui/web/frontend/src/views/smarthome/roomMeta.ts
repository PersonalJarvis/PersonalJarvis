/**
 * How a room looks: its glyph, its colour, and the one line that sums it up.
 *
 * Two rules carry this file.
 *
 * **Tolerance.** Every lookup accepts a value it has never seen and returns
 * something sensible. The backend owns the icon and colour vocabulary and may
 * grow it at any time; a frontend that switched exhaustively would make a room
 * with a new icon disappear, which is the enum-drift bug class this repo has
 * paid for repeatedly. Same rule `deviceMeta.iconForKind` already follows.
 *
 * **Both themes, one palette.** Colours are written as EXPLICIT class strings,
 * never assembled (`bg-${color}-500` is a class Tailwind never compiles, so the
 * room would silently render colourless). Each is a translucent layer over the
 * card surface rather than a solid fill, which is what lets a single value read
 * correctly in light and dark without two separate tables.
 */
import {
  Armchair,
  Baby,
  Bath,
  BedDouble,
  Boxes,
  Briefcase,
  Car,
  CookingPot,
  DoorOpen,
  Flower2,
  Footprints,
  Package,
  Shirt,
  Sofa,
  Sun,
  UtensilsCrossed,
  Warehouse,
  type LucideIcon,
} from "lucide-react";

import type { SmartDevice } from "@/hooks/useSmartHome";

const ROOM_ICONS: Record<string, LucideIcon> = {
  room: Armchair,
  living_room: Sofa,
  kitchen: CookingPot,
  bedroom: BedDouble,
  bathroom: Bath,
  office: Briefcase,
  dining: UtensilsCrossed,
  hallway: DoorOpen,
  kids: Baby,
  garage: Car,
  garden: Flower2,
  basement: Warehouse,
  attic: Boxes,
  laundry: Shirt,
  balcony: Sun,
  stairs: Footprints,
  storage: Package,
};

export function iconForRoom(icon: string): LucideIcon {
  return ROOM_ICONS[icon] ?? Armchair;
}

/** Every icon the picker offers, in the order the backend declared them. */
export function roomIconOptions(icons: string[]): { id: string; Icon: LucideIcon }[] {
  return icons.map((id) => ({ id, Icon: iconForRoom(id) }));
}

export interface RoomPalette {
  /** The card's tinted surface. */
  surface: string;
  /** The card's border in its resting state. */
  border: string;
  /** Icon and accent text. */
  accent: string;
  /** The solid swatch shown in the colour picker. */
  swatch: string;
  /** A soft wash for the room header, brighter than `surface`. */
  wash: string;
}

/*
 * Written out rather than generated: Tailwind's compiler scans source text for
 * literal class names, so an interpolated `bg-${token}-500/10` produces a class
 * that never reaches the stylesheet and a room that renders grey.
 */
const ROOM_PALETTES: Record<string, RoomPalette> = {
  slate: {
    surface: "bg-slate-500/[0.07]",
    border: "border-slate-500/25",
    accent: "text-slate-600 dark:text-slate-300",
    swatch: "bg-slate-500",
    wash: "from-slate-500/20",
  },
  amber: {
    surface: "bg-amber-500/[0.09]",
    border: "border-amber-500/30",
    accent: "text-amber-700 dark:text-amber-300",
    swatch: "bg-amber-500",
    wash: "from-amber-500/25",
  },
  rose: {
    surface: "bg-rose-500/[0.08]",
    border: "border-rose-500/30",
    accent: "text-rose-700 dark:text-rose-300",
    swatch: "bg-rose-500",
    wash: "from-rose-500/25",
  },
  violet: {
    surface: "bg-violet-500/[0.08]",
    border: "border-violet-500/30",
    accent: "text-violet-700 dark:text-violet-300",
    swatch: "bg-violet-500",
    wash: "from-violet-500/25",
  },
  sky: {
    surface: "bg-sky-500/[0.08]",
    border: "border-sky-500/30",
    accent: "text-sky-700 dark:text-sky-300",
    swatch: "bg-sky-500",
    wash: "from-sky-500/25",
  },
  emerald: {
    surface: "bg-emerald-500/[0.08]",
    border: "border-emerald-500/30",
    accent: "text-emerald-700 dark:text-emerald-300",
    swatch: "bg-emerald-500",
    wash: "from-emerald-500/25",
  },
  orange: {
    surface: "bg-orange-500/[0.09]",
    border: "border-orange-500/30",
    accent: "text-orange-700 dark:text-orange-300",
    swatch: "bg-orange-500",
    wash: "from-orange-500/25",
  },
  teal: {
    surface: "bg-teal-500/[0.08]",
    border: "border-teal-500/30",
    accent: "text-teal-700 dark:text-teal-300",
    swatch: "bg-teal-500",
    wash: "from-teal-500/25",
  },
};

export function paletteFor(color: string): RoomPalette {
  return ROOM_PALETTES[color] ?? ROOM_PALETTES.slate;
}

/**
 * How much of a room is "on", as 0-1.
 *
 * Drives the room card's glow. Read-only sensors are excluded from the
 * denominator: a room with eight thermometers and one lamp would otherwise
 * look almost dark with its only light burning.
 */
export function roomActivity(devices: SmartDevice[]): number {
  const switchable = devices.filter((d) => typeof d.state.on_off === "boolean");
  if (switchable.length === 0) return 0;
  const on = switchable.filter((d) => d.state.on_off === true).length;
  return on / switchable.length;
}

export interface RoomSummary {
  temperature: number | null;
  humidity: number | null;
  activeCount: number;
  /** Devices that are on AND can be switched off — what "everything off" hits. */
  switchableOn: number;
  unreachable: number;
}

export function summarise(
  devices: SmartDevice[],
  temperature: number | null,
  humidity: number | null,
): RoomSummary {
  return {
    temperature,
    humidity,
    activeCount: devices.filter((d) => d.state.on_off === true).length,
    switchableOn: devices.filter(
      (d) => d.state.on_off === true && d.commands.includes("turn_off"),
    ).length,
    unreachable: devices.filter((d) => !d.reachable).length,
  };
}

/**
 * Group a room's devices for the detail view.
 *
 * Lights first, then the things people reach for next, sensors last — sensors
 * are read, not operated, so putting them on top would bury every control
 * under a wall of numbers.
 */
const KIND_ORDER: string[] = [
  "light",
  "switch",
  "outlet",
  "climate",
  "cover",
  "lock",
  "media_player",
  "speaker",
  "fan",
  "vacuum",
  "camera",
  "scene",
  "script",
  "button",
  "binary_sensor",
  "sensor",
];

export function groupByKind(devices: SmartDevice[]): [string, SmartDevice[]][] {
  const groups = new Map<string, SmartDevice[]>();
  for (const device of devices) {
    const bucket = groups.get(device.kind);
    if (bucket) bucket.push(device);
    else groups.set(device.kind, [device]);
  }
  return [...groups.entries()].sort(([a], [b]) => {
    // An unknown kind sorts to the end rather than to the front, so a device
    // type this build has never seen never displaces the lights.
    const ia = KIND_ORDER.indexOf(a);
    const ib = KIND_ORDER.indexOf(b);
    return (ia === -1 ? 999 : ia) - (ib === -1 ? 999 : ib);
  });
}

/**
 * The fill a tile draws to SHOW its state instead of spelling it out.
 *
 * Returns 0-1 plus the visual register to use. A dimmed lamp is a partly warm
 * tile, a half-open blind is a half-filled one — the eye reads the room at a
 * glance, which is the whole difference between a dashboard and a list.
 */
export interface TileFill {
  level: number;
  tone: "warm" | "cool" | "neutral";
}

export function tileFill(device: SmartDevice): TileFill {
  const state = device.state ?? {};
  if (device.kind === "cover") {
    const position = typeof state.position === "number" ? state.position : null;
    if (position !== null) return { level: position / 100, tone: "cool" };
    return { level: state.open === true ? 1 : 0, tone: "cool" };
  }
  if (device.kind === "climate") {
    const target =
      typeof state.target_temperature === "number" ? state.target_temperature : null;
    // Mapped across the range the stepper allows (5-30°C) so the fill means
    // the same thing on every thermostat rather than being relative to itself.
    if (target !== null) {
      return { level: Math.min(1, Math.max(0, (target - 5) / 25)), tone: "warm" };
    }
  }
  if (state.on_off === true) {
    const brightness = typeof state.brightness === "number" ? state.brightness : null;
    const level = brightness === null ? 1 : Math.max(0.15, brightness / 100);
    return { level, tone: device.kind === "light" ? "warm" : "neutral" };
  }
  return { level: 0, tone: "neutral" };
}
