/**
 * The ground and the sea. The terrain is one merged, vertex-coloured mesh
 * (`terrainGeometry.ts`) that receives the sun's shadows.
 *
 * The sea is a subdivided plane whose vertex shader raises real waves — three
 * open-sea swells plus breakers that build up as the bottom rises (a shore-
 * distance field baked from the tile map says where the coast is) — and hands
 * the fragment shader the wave normal and the crest phase. The fragment shader
 * lights the water with the sun (dark troughs, bright faces, a glint on the
 * steep side), tints it by depth from the turquoise shallows to the abyss,
 * shimmers the shallows, breaks the crests into whitecaps and lays a
 * breathing foam line along the coast. A second, transparent plane just above
 * the beach is the wash: foam that runs up the sand and drains back.
 * world-masterplan-v2.md §3.6.
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
  Vector3,
} from "three";

import { ISLAND_HALF_M, LEVEL_Y, TILE_M, TileKind, buildIsland, type IslandMap } from "./islandLayout";
import { buildTerrainGeometry } from "./terrainGeometry";
import { viewAngles } from "./viewAngles";
import { SKY, WATER } from "./worldPalette";
import { cameraOffset } from "./worldCamera";

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
const SEA_SIZE_M = 1400;
/** The plane's rest height: below the water line, so a raised wave stays under the beach. */
const SEA_REST_Y = -0.1;
/** Vertices per side: 5 m cells, fine enough for 9 m breakers. */
const SEA_SEGMENTS = 280;
/** How many tiles out from the coast the shore texture measures distance. */
export const SHORE_REACH_TILES = 16;
/** The same reach in metres — the shader's distance scale. */
const SHORE_REACH_M = SHORE_REACH_TILES * TILE_M;
/** How many tiles inland the wash texture reaches over the beach. */
const WASH_REACH_TILES = 2;

/**
 * Shore distance per tile: 1 on land and at the coast, fading to 0 sixteen
 * tiles out — a multi-source breadth-first walk from every land tile over the
 * water. Linear filtering turns the per-tile values into a smooth field.
 */
export function buildShoreTexture(map: IslandMap): DataTexture {
  const dist = distanceField(map, (k) => k !== TileKind.water, (k) => k === TileKind.water, SHORE_REACH_TILES);
  const n = map.size * map.size;
  const data = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    if (map.kind[i] !== TileKind.water) {
      data[i] = 255;
      continue;
    }
    const d = dist[i];
    data[i] = d < 0 ? 0 : Math.max(0, Math.round(255 * (1 - (d - 0.5) / SHORE_REACH_TILES)));
  }
  return makeTexture(data, map.size);
}

/**
 * The beach as seen from the water: 1 on sand right at the waterline, 0.5 one
 * tile further up, 0 elsewhere — the field the wash climbs.
 */
export function buildWashTexture(map: IslandMap): DataTexture {
  const dist = distanceField(map, (k) => k === TileKind.water, (k) => k === TileKind.sand, WASH_REACH_TILES);
  const n = map.size * map.size;
  const data = new Uint8Array(n);
  for (let i = 0; i < n; i++) {
    const d = dist[i];
    // Only the low beach: sand one step above the water.
    if (map.kind[i] !== TileKind.sand || map.level[i] !== 1 || d <= 0) continue;
    data[i] = Math.round(255 * (1 - (d - 1) / WASH_REACH_TILES));
  }
  return makeTexture(data, map.size);
}

/** Breadth-first distance from every `source` tile over `through` tiles, up to `reach`. */
function distanceField(
  map: IslandMap,
  source: (kind: number) => boolean,
  through: (kind: number) => boolean,
  reach: number,
): Int16Array {
  const n = map.size * map.size;
  const dist = new Int16Array(n).fill(-1);
  const queue: number[] = [];
  for (let i = 0; i < n; i++) {
    if (source(map.kind[i])) {
      dist[i] = 0;
      queue.push(i);
    }
  }
  let head = 0;
  while (head < queue.length) {
    const cur = queue[head++];
    const d = dist[cur];
    if (d >= reach) continue;
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
      if (dist[ni] !== -1 || !through(map.kind[ni])) continue;
      dist[ni] = d + 1;
      queue.push(ni);
    }
  }
  return dist;
}

function makeTexture(data: Uint8Array, size: number): DataTexture {
  const tex = new DataTexture(data, size, size, RedFormat);
  tex.minFilter = LinearFilter;
  tex.magFilter = LinearFilter;
  tex.wrapS = ClampToEdgeWrapping;
  tex.wrapT = ClampToEdgeWrapping;
  tex.needsUpdate = true;
  return tex;
}

const NOISE_GLSL = /* glsl */ `
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
`;

const WATER_VERTEX = /* glsl */ `
  uniform sampler2D shoreTex;
  uniform float time;
  uniform float halfSize;
  uniform float reachM;
  varying vec3 vWorld;
  varying vec3 vNormal;
  varying float vDist;
  varying float vCrest;
  ${NOISE_GLSL}

  float shoreDist(vec2 xz) {
    vec2 uv = xz / (2.0 * halfSize) + 0.5;
    return (1.0 - texture2D(shoreTex, uv).r) * reachM;
  }

  void main() {
    vec3 world = (modelMatrix * vec4(position, 1.0)).xyz;
    vec2 p = world.xz;
    float dist = shoreDist(p);
    // Where the coast lies, from the field's gradient: the breakers run that way.
    float e = 2.0;
    vec2 grad = vec2(shoreDist(p + vec2(e, 0.0)) - shoreDist(p - vec2(e, 0.0)),
                     shoreDist(p + vec2(0.0, e)) - shoreDist(p - vec2(0.0, e))) / (2.0 * e);

    float h = 0.0;
    vec2 slope = vec2(0.0);
    // Three open-sea swells: long, medium, short.
    vec2 d1 = normalize(vec2(0.8, 0.6));
    vec2 d2 = normalize(vec2(-0.5, 0.87));
    vec2 d3 = normalize(vec2(0.3, -0.95));
    float k1 = 6.2832 / 34.0, k2 = 6.2832 / 19.0, k3 = 6.2832 / 9.5;
    float a1 = 0.1, a2 = 0.06, a3 = 0.035;
    float p1 = dot(p, d1) * k1 - time * 0.9;
    float p2 = dot(p, d2) * k2 - time * 1.25;
    float p3 = dot(p, d3) * k3 - time * 1.9;
    h += a1 * sin(p1) + a2 * sin(p2) + a3 * sin(p3);
    slope += a1 * k1 * cos(p1) * d1 + a2 * k2 * cos(p2) * d2 + a3 * k3 * cos(p3) * d3;

    // Breakers: as the bottom rises the wave steepens, peaks a few metres off
    // the beach and collapses into foam on the sand.
    float shoal = smoothstep(1.5, 6.0, dist) * (1.0 - smoothstep(9.0, 30.0, dist));
    float wobble = noise(p * 0.12 + vec2(3.7, 1.3)) * 2.5;
    float phase = dist * 0.72 - time * 1.35 + wobble;
    float crest = sin(phase);
    // Sharpen the front face: a wave that is about to break leans forward.
    float steep = crest + 0.35 * sin(2.0 * phase + 1.2);
    float ab = 0.13 * shoal;
    h += ab * steep;
    // Its slope points along the shore gradient (toward the coast).
    slope += ab * 0.72 * cos(phase) * grad;

    // The surface itself barely rises (it must never break through the sand, which
    // sits 0.35 m up); the LIGHT does the work: normals are steepened for shading.
    vec3 displaced = world + vec3(0.0, h, 0.0);
    vWorld = displaced;
    vNormal = normalize(vec3(-slope.x * 2.6, 1.0, -slope.y * 2.6));
    vDist = dist;
    vCrest = steep * shoal;
    gl_Position = projectionMatrix * viewMatrix * vec4(displaced, 1.0);
  }
`;

const WATER_FRAGMENT = /* glsl */ `
  uniform float time;
  uniform float reachM;
  uniform vec3 sunDir;
  uniform vec3 viewDir;
  uniform vec3 abyss;
  uniform vec3 deep;
  uniform vec3 surface;
  uniform vec3 shallow;
  uniform vec3 ripple;
  uniform vec3 foam;
  uniform vec3 glintColor;
  varying vec3 vWorld;
  varying vec3 vNormal;
  varying float vDist;
  varying float vCrest;
  ${NOISE_GLSL}

  void main() {
    float dist = vDist;
    vec3 n = normalize(vNormal);

    // Depth: turquoise over the sand, the surface blue, then deep, then the abyss.
    vec3 col = mix(shallow, surface, smoothstep(0.0, 9.0, dist));
    col = mix(col, deep, smoothstep(8.0, 22.0, dist));
    col = mix(col, abyss, smoothstep(24.0, reachM, dist));

    // The sun on the waves: faces toward it brighten, troughs and backs darken.
    float ndl = max(dot(n, sunDir), 0.0);
    col *= 0.72 + 0.4 * ndl;
    // The glint: the sun's reflection toward the camera on the steep faces.
    vec3 r = reflect(-sunDir, n);
    float spec = pow(max(dot(r, viewDir), 0.0), 90.0);
    col += glintColor * spec * 0.55;

    // Caustic shimmer where the bottom is close.
    float n1 = noise(vWorld.xz * 0.8 + vec2(time * 0.35, -time * 0.22));
    float n2 = noise(vWorld.xz * 1.15 - vec2(time * 0.28, time * 0.31));
    float caustic = smoothstep(0.66, 0.92, n1 * 0.5 + n2 * 0.5) * (1.0 - smoothstep(2.0, 12.0, dist));
    col = mix(col, ripple, caustic * 0.3);

    // Whitecaps: the crest of a breaker, torn by noise, streaming behind it.
    float lace = noise(vWorld.xz * 0.9 + vec2(-time * 0.6, time * 0.45));
    float lace2 = noise(vWorld.xz * 2.2 + vec2(time * 0.9, -time * 0.7));
    float cap = smoothstep(0.55, 0.95, vCrest) * smoothstep(0.2, 0.7, lace * 0.6 + lace2 * 0.4);
    float streak = smoothstep(0.25, 0.6, vCrest) * smoothstep(0.55, 0.85, lace2) * 0.5;

    // The foam line hugging the coast, breathing with the waves.
    float wobble = noise(vWorld.xz * 0.12 + vec2(3.7, 1.3)) * 2.5;
    float breathe = 0.45 * sin(time * 1.35 + wobble);
    float band = 1.0 - smoothstep(0.9 + breathe, 3.0 + breathe, dist);
    float foamAmt = max(band * (0.65 + 0.35 * lace), max(cap * 0.95, streak));
    col = mix(col, foam, foamAmt);

    // Sparkle on the open water, in drifting patches.
    float g1 = sin(vWorld.x * 0.42 + vWorld.z * 0.18 + time * 0.9);
    float g2 = sin(vWorld.z * 0.37 - vWorld.x * 0.21 - time * 0.7);
    float glintPatch = smoothstep(0.42, 0.72, noise(vWorld.xz * 0.06 + vec2(time * 0.05, -time * 0.03)));
    float glint = smoothstep(0.9, 1.0, g1 * g2) * smoothstep(4.0, 12.0, dist) * glintPatch;
    col = mix(col, ripple, glint * 0.4);

    gl_FragColor = vec4(col, 1.0);
    #include <colorspace_fragment>
  }
`;

const WASH_VERTEX = /* glsl */ `
  varying vec2 vXz;
  void main() {
    vec4 world = modelMatrix * vec4(position, 1.0);
    vXz = world.xz;
    gl_Position = projectionMatrix * viewMatrix * world;
  }
`;

const WASH_FRAGMENT = /* glsl */ `
  uniform sampler2D washTex;
  uniform float time;
  uniform float halfSize;
  uniform vec3 foam;
  varying vec2 vXz;
  ${NOISE_GLSL}

  void main() {
    vec2 uv = vXz / (2.0 * halfSize) + 0.5;
    // 1 at the waterline, 0.5 a tile up the beach, 0 beyond: how far a wash can climb.
    float beach = texture2D(washTex, uv).r;
    if (beach <= 0.001) discard;
    float wobble = noise(vXz * 0.12 + vec2(3.7, 1.3)) * 2.5;
    // The same rhythm as the breakers: the wash follows each one up the sand.
    float surge = 0.5 + 0.5 * sin(time * 1.35 + wobble + 1.3);
    float reach = 0.35 + 0.65 * surge;
    float edge = smoothstep(reach - 0.35, reach, beach);
    float lace = noise(vXz * 1.4 + vec2(-time * 0.5, time * 0.4));
    // The sheet is thickest at its leading edge and lacy behind it.
    float alpha = edge * (0.35 + 0.45 * smoothstep(0.3, 0.8, lace)) * (0.6 + 0.4 * surge);
    gl_FragColor = vec4(foam, alpha);
    #include <colorspace_fragment>
  }
`;

const SUN_DIR = new Vector3(SKY.sunFrom[0], SKY.sunFrom[1], SKY.sunFrom[2]).normalize();
const VIEW_DIR = (() => {
  const [x, y, z] = cameraOffset();
  return new Vector3(x, y, z).normalize();
})();

export function Water({ paused }: { paused: boolean }) {
  const geometry = useMemo(() => new PlaneGeometry(SEA_SIZE_M, SEA_SIZE_M, SEA_SEGMENTS, SEA_SEGMENTS), []);
  const washGeometry = useMemo(() => new PlaneGeometry(SEA_SIZE_M, SEA_SIZE_M), []);
  const { shore, wash } = useMemo(() => {
    const { map } = buildIsland();
    return { shore: buildShoreTexture(map), wash: buildWashTexture(map) };
  }, []);
  const material = useMemo(
    () =>
      new ShaderMaterial({
        uniforms: {
          shoreTex: { value: shore },
          time: { value: 0 },
          halfSize: { value: ISLAND_HALF_M },
          reachM: { value: SHORE_REACH_M },
          sunDir: { value: SUN_DIR },
          // Cloned: the frame loop turns this one with the orbit.
          viewDir: { value: VIEW_DIR.clone() },
          abyss: { value: new Color(WATER.abyss) },
          deep: { value: new Color(WATER.deep) },
          surface: { value: new Color(WATER.surface) },
          shallow: { value: new Color(WATER.shallow) },
          ripple: { value: new Color(WATER.ripple) },
          foam: { value: new Color(WATER.foam) },
          glintColor: { value: new Color(SKY.seaGlint) },
        },
        vertexShader: WATER_VERTEX,
        fragmentShader: WATER_FRAGMENT,
      }),
    [shore],
  );
  const washMaterial = useMemo(
    () =>
      new ShaderMaterial({
        uniforms: {
          washTex: { value: wash },
          time: { value: 0 },
          halfSize: { value: ISLAND_HALF_M },
          foam: { value: new Color(WATER.foam) },
        },
        vertexShader: WASH_VERTEX,
        fragmentShader: WASH_FRAGMENT,
        transparent: true,
        depthWrite: false,
      }),
    [wash],
  );
  useEffect(
    () => () => {
      geometry.dispose();
      washGeometry.dispose();
      material.dispose();
      washMaterial.dispose();
      shore.dispose();
      wash.dispose();
    },
    [geometry, washGeometry, material, washMaterial, shore, wash],
  );

  useFrame(({ clock }) => {
    // The sun's glint is a mirror of the view direction, so it has to follow
    // the orbit even when the water itself is frozen (reduced motion).
    const { yaw, pitch } = viewAngles();
    const [vx, vy, vz] = cameraOffset(pitch, yaw);
    material.uniforms.viewDir.value.set(vx, vy, vz).normalize();
    if (paused) return;
    const t = clock.getElapsedTime();
    material.uniforms.time.value = t;
    washMaterial.uniforms.time.value = t;
  });

  return (
    <group>
      <mesh geometry={geometry} material={material} rotation={[-Math.PI / 2, 0, 0]} position={[0, SEA_REST_Y, 0]} />
      {/* the wash: a sheet just above the beach's sand */}
      <mesh
        geometry={washGeometry}
        material={washMaterial}
        rotation={[-Math.PI / 2, 0, 0]}
        position={[0, LEVEL_Y[1] + 0.03, 0]}
        renderOrder={2}
      />
    </group>
  );
}
