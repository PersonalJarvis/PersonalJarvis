/**
 * Per-viewer look settings for the island. Cosmetic, local, never synced.
 *
 * `grain` — screen pixels per rendered pixel. 0 = smooth (the default since the
 * world-masterplan-v2 decision: the references are Roblox / Clash of Clans,
 * both smooth); 2 = the V1 pixel look; 3 = coarse. Any grain > 0 also serves
 * as the cheap-GPU mode, because the pixel pass renders grain² fewer fragments.
 * `scale` — internal render scale when grain is 0 (0.5–1). Below 1 the frame
 * is rendered smaller and upscaled with linear filtering: the same GPU saving
 * without stair-steps.
 */
import { create } from "zustand";

export type Grain = 0 | 2 | 3;

export interface WorldSettings {
  grain: Grain;
  scale: number;
  bloom: boolean;
  shadows: boolean;
  setGrain: (grain: Grain) => void;
  setScale: (scale: number) => void;
  setBloom: (on: boolean) => void;
  setShadows: (on: boolean) => void;
}

const KEY = "jarvis.world.look.v1";

interface Stored {
  grain?: number;
  scale?: number;
  bloom?: boolean;
  shadows?: boolean;
}

function read(): Stored {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return {};
    const parsed = JSON.parse(raw) as Stored;
    return typeof parsed === "object" && parsed !== null ? parsed : {};
  } catch {
    return {};
  }
}

function write(s: Stored): void {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* not persisted, still applied */
  }
}

export function normalizeGrain(v: unknown): Grain {
  return v === 2 || v === 3 ? v : 0;
}

export function normalizeScale(v: unknown): number {
  return typeof v === "number" && Number.isFinite(v) ? Math.min(1, Math.max(0.5, v)) : 0.75;
}

const initial = typeof localStorage === "undefined" ? {} : read();

export const useWorldSettings = create<WorldSettings>((set, get) => ({
  grain: normalizeGrain(initial.grain),
  scale: normalizeScale(initial.scale),
  bloom: initial.bloom ?? false,
  shadows: initial.shadows ?? true,
  setGrain: (grain) => {
    set({ grain });
    persist(get());
  },
  setScale: (scale) => {
    set({ scale: normalizeScale(scale) });
    persist(get());
  },
  setBloom: (bloom) => {
    set({ bloom });
    persist(get());
  },
  setShadows: (shadows) => {
    set({ shadows });
    persist(get());
  },
}));

function persist(s: WorldSettings): void {
  write({ grain: s.grain, scale: s.scale, bloom: s.bloom, shadows: s.shadows });
}
