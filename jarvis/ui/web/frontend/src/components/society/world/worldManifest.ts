import inventory from "../../../assets/society/world/asset-inventory.json";
import manifest from "../../../assets/society/world/world-manifest.json";
import type { Polygon } from "./spatial";

export interface BuildingAsset {
  asset: string;
  sizeM: [number, number];
  collision: Polygon;
  door: [number, number];
  stand: [number, number];
  sign: [number, number, number];
  heightM: number;
  ramps: Array<{ from: [number, number, number]; to: [number, number, number]; widthM: number }>;
}
export const WORLD_MANIFEST = manifest;
export const BUILDING_ASSETS = manifest.buildings as unknown as Record<keyof typeof manifest.buildings, BuildingAsset>;

/** Measured exports drive labels; the design envelope is only a fallback. */
export function buildingHeight(place: keyof typeof BUILDING_ASSETS): number {
  const entry = inventory.assets.find(a => a.file === `world/kit/${BUILDING_ASSETS[place].asset}.glb`);
  return entry?.bounds?.[1]?.[1] ?? BUILDING_ASSETS[place].heightM;
}
