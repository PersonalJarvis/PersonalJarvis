/**
 * The ground and the sea. The terrain is one merged, vertex-coloured mesh
 * (`terrainGeometry.ts`) that receives the sun's shadows. The sea is a single
 * plane with a shader driven by a shore-distance texture baked from the tile
 * map: turquoise shallows deepening to the open sea, long swells, caustic
 * shimmer in the shallows, wave crests rolling toward every coast and
 * breaking into a foam line, sparkle further out — world-masterplan-v2.md §3.6.
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

import { ISLAND_HALF_M, TILE_M, TileKind, buildIsland, type IslandMap } from "./islandLayout";
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
const SEA_SIZE_M = 2400;
/** How many tiles out from the coast the shore texture measures distance. */
export const SHORE_REACH_TILES = 16;
/** The same reach in metres — the shader's distance scale. */
const SHORE_REACH_M = SHORE_REACH_TILES * TILE_M;

/**
 * Shore distance per tile: 1 on land and at the coast, fading to 0 sixteen
 * tiles out — a multi-source breadth-first walk from every land tile over the
 * water. Linear filtering turns the per-tile values into a smooth field.
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
    data[i] = d < 0 ? 0 : Math.max(0, Math.round(255 * (1 - (d - 0.5) / SHORE_REACH_TILES)));
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
  uniform float reachM;
  uniform vec3 abyss;
  uniform vec3 deep;
  uniform vec3 surface;
  uniform vec3 shallow;
  uniform vec3 ripple;
  uniform vec3 foam;
  varying vec3 vWorld;

  float hashn(vec2 p) {
    return fract(sin(dot(p, vec2(127.1, 311.7))) * 43758.5453);
  }
  float noise(vec2 p) {
    vec2 i = floor(p);
    vec2 f = fract(p);
    f = f * f * (3.0 - 2.0 * f);
    float a = hashn(i);
    float b = hashn(i + vec2(1.0, 0.0));
    float c = hashn(i + vec2(0.0, 1.0));
    float d = hashn(i + vec2(1.0, 1.0));
    return mix(mix(a, b, f.x), mix(c, d, f.x), f.y);
  }

  void main() {
    vec2 uv = vWorld.xz / (2.0 * halfSize) + 0.5;
    float shore = texture2D(shoreTex, uv).r;
    // Metres from the nearest coast, 0 at the shore, reachM on the open sea.
    float dist = (1.0 - shore) * reachM;

    // Depth: turquoise over the sand, the surface blue, then deep, then the abyss.
    vec3 col = mix(shallow, surface, smoothstep(0.0, 9.0, dist));
    col = mix(col, deep, smoothstep(8.0, 22.0, dist));
    col = mix(col, abyss, smoothstep(24.0, reachM, dist));

    // Long swells: two slow bands crossing the whole sea.
    float swell = sin(dot(vWorld.xz, vec2(0.045, 0.028)) - time * 0.55) * 0.5 + 0.5;
    float swell2 = sin(dot(vWorld.xz, vec2(-0.03, 0.05)) + time * 0.4) * 0.5 + 0.5;
    col *= 0.94 + 0.05 * swell + 0.04 * swell2;

    // Caustic shimmer where the bottom is close.
    float n1 = noise(vWorld.xz * 0.8 + vec2(time * 0.35, -time * 0.22));
    float n2 = noise(vWorld.xz * 1.15 - vec2(time * 0.28, time * 0.31));
    float caustic = smoothstep(0.66, 0.92, n1 * 0.5 + n2 * 0.5) * (1.0 - smoothstep(2.0, 12.0, dist));
    col = mix(col, ripple, caustic * 0.32);

    // Breakers: crests rolling in toward the coast, wavelength ~9 m, bent by noise.
    float wobble = noise(vWorld.xz * 0.12 + vec2(3.7, 1.3)) * 2.5;
    float crest = sin(dist * 0.7 - time * 1.35 + wobble);
    float crestMask = (1.0 - smoothstep(3.0, 16.0, dist)) * smoothstep(0.6, 3.0, dist);
    float lace = noise(vWorld.xz * 0.9 + vec2(-time * 0.6, time * 0.45));
    float waves = smoothstep(0.84, 0.97, crest) * crestMask * smoothstep(0.25, 0.75, lace);

    // The foam line hugging the coast, breathing with the waves.
    float breathe = 0.35 * sin(time * 1.35 + wobble);
    float band = 1.0 - smoothstep(1.0 + breathe, 2.8 + breathe, dist);
    float foamAmt = max(band * (0.7 + 0.3 * lace), waves * 0.9);
    col = mix(col, foam, foamAmt);

    // Sparkle on the open water.
    float g1 = sin(vWorld.x * 0.42 + vWorld.z * 0.18 + time * 0.9);
    float g2 = sin(vWorld.z * 0.37 - vWorld.x * 0.21 - time * 0.7);
    float glintPatch = smoothstep(0.42, 0.72, noise(vWorld.xz * 0.06 + vec2(time * 0.05, -time * 0.03)));
    float glint = smoothstep(0.9, 1.0, g1 * g2) * smoothstep(4.0, 12.0, dist) * glintPatch;
    col = mix(col, ripple, glint * 0.45);

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
          reachM: { value: SHORE_REACH_M },
          abyss: { value: new Color(WATER.abyss) },
          deep: { value: new Color(WATER.deep) },
          surface: { value: new Color(WATER.surface) },
          shallow: { value: new Color(WATER.shallow) },
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
