/**
 * A few soft clouds drifting high over the island. They are never seen from
 * this camera — the point is their SHADOWS, which the sun's shadow map casts
 * onto the ground as they pass: the slow-moving patches of shade that make a
 * Clash-of-Clans village feel outdoors (world-masterplan-v2.md §3.7).
 */
import { useEffect, useMemo, useRef } from "react";
import { useFrame } from "@react-three/fiber";
import { CanvasTexture, DoubleSide, Group, MeshBasicMaterial, PlaneGeometry } from "three";

import { hash2 } from "./islandLayout";

const CLOUD_Y = 70;
const CLOUD_COUNT = 4;
/** Metres per second; the whole sky crosses the island in ~5 minutes. */
const DRIFT_MPS = 1.1;
const FIELD = 420;

function makeCloudTexture(): CanvasTexture | null {
  if (typeof document === "undefined") return null;
  const c = document.createElement("canvas");
  c.width = 128;
  c.height = 128;
  const ctx = c.getContext("2d");
  if (!ctx) return null;
  ctx.clearRect(0, 0, 128, 128);
  // Three overlapping soft blobs: a cloud, not a disc.
  for (const [x, y, r] of [
    [50, 64, 34],
    [78, 58, 30],
    [64, 74, 26],
  ]) {
    const g = ctx.createRadialGradient(x, y, r * 0.3, x, y, r);
    g.addColorStop(0, "rgba(255,255,255,1)");
    g.addColorStop(1, "rgba(255,255,255,0)");
    ctx.fillStyle = g;
    ctx.fillRect(0, 0, 128, 128);
  }
  return new CanvasTexture(c);
}

export function Clouds({ paused }: { paused: boolean }) {
  const group = useRef<Group>(null);
  const texture = useMemo(makeCloudTexture, []);
  const geometry = useMemo(() => new PlaneGeometry(1, 1), []);
  const material = useMemo(
    () =>
      new MeshBasicMaterial({
        map: texture ?? undefined,
        transparent: true,
        opacity: 0.0, // invisible from below/above — only its shadow matters
        alphaTest: 0.45, // the depth material honours this: soft-edged shadow, hard cutoff
        side: DoubleSide,
        depthWrite: false,
      }),
    [texture],
  );
  const seeds = useMemo(
    () =>
      Array.from({ length: CLOUD_COUNT }, (_, i) => ({
        x: (hash2(i, 1, 99) - 0.5) * FIELD,
        z: (hash2(i, 2, 99) - 0.5) * FIELD,
        s: 26 + hash2(i, 3, 99) * 22,
        speed: 0.7 + hash2(i, 4, 99) * 0.6,
      })),
    [],
  );

  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
      texture?.dispose();
    },
    [geometry, material, texture],
  );

  useFrame((_, dt) => {
    const g = group.current;
    if (!g || paused) return;
    const step = Math.min(dt, 0.1) * DRIFT_MPS;
    g.children.forEach((cloud, i) => {
      cloud.position.x += step * seeds[i].speed;
      cloud.position.z += step * seeds[i].speed * 0.35;
      if (cloud.position.x > FIELD / 2) cloud.position.x -= FIELD;
      if (cloud.position.z > FIELD / 2) cloud.position.z -= FIELD;
    });
  });

  return (
    <group ref={group}>
      {seeds.map((s, i) => (
        <mesh
          key={i}
          geometry={geometry}
          material={material}
          position={[s.x, CLOUD_Y, s.z]}
          rotation={[-Math.PI / 2, 0, 0]}
          scale={[s.s, s.s * 0.7, 1]}
          castShadow
        />
      ))}
    </group>
  );
}
