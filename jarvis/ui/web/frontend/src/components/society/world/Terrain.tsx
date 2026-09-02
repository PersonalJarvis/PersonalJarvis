/**
 * The ground and the sea. The terrain is one merged, vertex-coloured mesh
 * (`terrainGeometry.ts`) that receives the sun's shadows. The sea is a single
 * plane with a small shader: depth tint toward the coast, an animated foam
 * line along the shore, sparkle — world-masterplan-v2.md §3.6.
 */
import { useEffect, useMemo } from "react";
import { useFrame } from "@react-three/fiber";
import {
  ClampToEdgeWrapping,
  Color,
  DataTexture,
  DoubleSide,
  LinearFilter,
  MeshLambertMaterial,
  PlaneGeometry,
  RedFormat,
  ShaderMaterial,
} from "three";

import { ISLAND_HALF_M, TileKind, buildIsland, type IslandMap } from "./islandLayout";
import { buildTerrainGeometry } from "./terrainGeometry";
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
  return <mesh geometry={geometry} material={material} receiveShadow />;
}

/** The sea plane — far larger than the island so its edge is never in view. */
const SEA_SIZE_M = 1600;
/** How many tiles out from the coast the foam and the shallow tint reach. */
const SHORE_REACH_TILES = 4;

/**
 * Shore proximity per tile, 1 at the coast fading to 0 four tiles out: a
 * multi-source breadth-first walk from every land tile over the water.
 */
export function buildShoreTexture(map: IslandMap): DataTexture {
  const n = map.size * map.size;
  const dist = new Int16Array(n).fill(-1);
  const queue: number[] = [];
  for (let i = 0; i < n; i++) {
    if (map.kind[i] !== TileKind.water) {
      dist[i] = 0;
      queue.push(i);
    }
  }
  let head = 0;
  while (head < queue.length) {
    const cur = queue[head++];
    const d = dist[cur];
    if (d >= SHORE_REACH_TILES) continue;
    const cx = cur % map.size;
    const cz = Math.floor(cur / map.size);
    for (const [dx, dz] of [
      [1, 0],
      [-1, 0],
      [0, 1],
      [0, -1],
    ] as const) {
      const nx = cx + dx;
      const nz = cz + dz;
      if (nx < 0 || nz < 0 || nx >= map.size || nz >= map.size) continue;
      const ni = nz * map.size + nx;
      if (dist[ni] !== -1) continue;
      dist[ni] = d + 1;
      queue.push(ni);
    }
  }
  const data = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    if (map.kind[i] !== TileKind.water) {
      data[i] = 255;
      continue;
    }
    const d = dist[i];
    data[i] = d < 0 ? 0 : Math.round(255 * (1 - (d - 0.5) / SHORE_REACH_TILES));
  }
  const tex = new DataTexture(data, map.size, map.size, RedFormat);
  tex.minFilter = LinearFilter;
  tex.magFilter = LinearFilter;
  tex.wrapS = ClampToEdgeWrapping;
  tex.wrapT = ClampToEdgeWrapping;
  tex.needsUpdate = true;
  return tex;
}

const WATER_VERTEX = /* glsl */ `
  varying vec3 vWorld;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vWorld = world.xyz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const WATER_FRAGMENT = /* glsl */ `
  uniform sampler2D shoreTex;
  uniform float time;
  uniform float halfSize;
  uniform vec3 deep;
  uniform vec3 surface;
  uniform vec3 ripple;
  uniform vec3 foam;
  varying vec3 vWorld;

  void main() {
    vec2 uv = vWorld.xz / (2.0 * halfSize) + 0.5;
    float shore = texture2D(shoreTex, uv).r;
    // Two slow wave fields; their product gives moving glints.
    float w1 = sin(vWorld.x * 0.42 + vWorld.z * 0.18 + time * 0.9);
    float w2 = sin(vWorld.z * 0.37 - vWorld.x * 0.21 - time * 0.7);
    float glint = smoothstep(0.86, 1.0, w1 * w2);
    vec3 col = mix(deep, surface, clamp(shore * 1.3, 0.0, 1.0));
    col = mix(col, ripple, glint * 0.5);
    // Foam: a band hugging the coast that breathes with the waves.
    float breathe = 0.06 * sin(time * 1.6 + vWorld.x * 0.8 + vWorld.z * 0.6);
    float band = smoothstep(0.62 + breathe, 0.9 + breathe, shore);
    float lace = smoothstep(0.2, 0.8, sin(vWorld.x * 0.9 + time * 0.9) * sin(vWorld.z * 0.8 - time * 0.7) * 0.5 + 0.5);
    col = mix(col, foam, band * (0.7 + 0.3 * lace));
    gl_FragColor = vec4(col, 1.0);
    #include <colorspace_fragment>
  }
`;

export function Water({ paused }: { paused: boolean }) {
  const geometry = useMemo(() => new PlaneGeometry(SEA_SIZE_M, SEA_SIZE_M), []);
  const shore = useMemo(() => buildShoreTexture(buildIsland().map), []);
  const material = useMemo(
    () =>
      new ShaderMaterial({
        uniforms: {
          shoreTex: { value: shore },
          time: { value: 0 },
          halfSize: { value: ISLAND_HALF_M },
          deep: { value: new Color(WATER.deep) },
          surface: { value: new Color(WATER.surface) },
          ripple: { value: new Color(WATER.ripple) },
          foam: { value: new Color(WATER.foam) },
        },
        vertexShader: WATER_VERTEX,
        fragmentShader: WATER_FRAGMENT,
      }),
    [shore],
  );
  useEffect(
    () => () => {
      geometry.dispose();
      material.dispose();
      shore.dispose();
    },
    [geometry, material, shore],
  );

  useFrame(({ clock }) => {
    if (paused) return;
    material.uniforms.time.value = clock.getElapsedTime();
  });

  return <mesh geometry={geometry} material={material} rotation={[-Math.PI / 2, 0, 0]} />;
}
