/**
 * The ground and the sea. The terrain is one merged, vertex-coloured mesh
 * (`terrainGeometry.ts`); the sea is a single plane under everything with a
 * scrolling pixel texture. Both are static apart from the water's drift.
 */
import { useEffect, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import { DoubleSide, MeshBasicMaterial, MeshLambertMaterial, PlaneGeometry } from "three";

import { buildIsland } from "./islandLayout";
import { buildTerrainGeometry } from "./terrainGeometry";
import { WATER_TILE_M, makeWaterTexture } from "./waterTexture";
import { WATER } from "./worldPalette";

export function Terrain() {
  const geometry = useMemo(() => buildTerrainGeometry(buildIsland().map), []);
  const material = useMemo(
    () => new MeshLambertMaterial({ vertexColors: true, side: DoubleSide }),
    [],
  );
  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
    },
    [geometry, material],
  );
  return <mesh geometry={geometry} material={material} receiveShadow={false} />;
}

/** The sea plane — far larger than the island so its edge is never in view. */
const SEA_SIZE_M = 1600;

export function Water({ paused }: { paused: boolean }) {
  const texture = useMemo(() => makeWaterTexture(), []);
  const geometry = useMemo(() => new PlaneGeometry(SEA_SIZE_M, SEA_SIZE_M), []);
  const material = useMemo(() => {
    if (!texture) return new MeshBasicMaterial({ color: WATER.surface });
    texture.repeat.set(SEA_SIZE_M / WATER_TILE_M, SEA_SIZE_M / WATER_TILE_M);
    return new MeshBasicMaterial({ map: texture });
  }, [texture]);
  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
      texture?.dispose();
    },
    [geometry, material, texture],
  );

  useFrame(({ clock }) => {
    if (paused || !texture) return;
    const t = clock.getElapsedTime();
    // A slow drift plus a gentle back-and-forth: water, not a conveyor belt.
    texture.offset.set(t * 0.012 + Math.sin(t * 0.35) * 0.01, t * 0.006);
  });

  return (
    <mesh geometry={geometry} material={material} rotation={[-Math.PI / 2, 0, 0]} position={[0, 0, 0]} />
  );
}
