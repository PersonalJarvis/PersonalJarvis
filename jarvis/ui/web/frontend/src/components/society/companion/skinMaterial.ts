/**
 * A companion design (skins.ts) on the 3D body: the standard vinyl material
 * with its diffuse colour replaced by the design's gradient over the body's
 * own bounding box, and the effect added as emissive light (stars, a moving
 * shine, flowing colours, a pulsing glow, glints). One shader for every
 * design; the colours, layout and clock are per-material uniforms.
 */
import { Box3, Color, MeshStandardMaterial, Vector2, Vector3, type BufferGeometry } from "three";
import { skinEffect, skinHighlight, SKIN_EFFECTS, type CompanionSkin } from "./skins";

interface SkinUniforms {
  [name: string]: { value: unknown };
  uSkinColors: { value: Color[] };
  uSkinCount: { value: number };
  uSkinRadial: { value: number };
  uSkinDir: { value: Vector2 };
  uSkinMin: { value: Vector3 };
  uSkinMax: { value: Vector3 };
  uSkinEffect: { value: number };
  uSkinTime: { value: number };
  uSkinHighlight: { value: Color };
}

const VERTEX_HEAD = "#include <common>\nvarying vec3 vSkinPos;";
const VERTEX_BODY = "#include <begin_vertex>\nvSkinPos = position;";

const FRAGMENT_HEAD = /* glsl */ `#include <common>
varying vec3 vSkinPos;
uniform vec3 uSkinColors[4];
uniform int uSkinCount;
uniform int uSkinRadial;
uniform vec2 uSkinDir;
uniform vec3 uSkinMin;
uniform vec3 uSkinMax;
uniform int uSkinEffect;
uniform float uSkinTime;
uniform vec3 uSkinHighlight;
vec3 skinSample(float t) {
  t = clamp(t, 0.0, 1.0) * float(uSkinCount - 1);
  vec3 c = uSkinColors[0];
  for (int i = 1; i < 4; i++) {
    if (i >= uSkinCount) break;
    c = mix(c, uSkinColors[i], clamp(t - float(i - 1), 0.0, 1.0));
  }
  return c;
}
vec3 skinRing(float u) {
  float t = fract(u) * float(uSkinCount);
  vec3 c = uSkinColors[0];
  for (int i = 1; i <= 4; i++) {
    if (i > uSkinCount) break;
    c = mix(c, uSkinColors[i % uSkinCount], clamp(t - float(i - 1), 0.0, 1.0));
  }
  return c;
}
float skinHash(vec3 p) { return fract(sin(dot(p, vec3(127.1, 311.7, 74.7))) * 43758.5453); }
float skinSpecks(vec3 p, float cells, float density, float speed) {
  vec3 q = p * cells;
  vec3 cell = floor(q);
  float h = skinHash(cell);
  vec3 offset = vec3(skinHash(cell + 1.7), skinHash(cell + 4.3), skinHash(cell + 8.9)) - 0.5;
  float d = length(fract(q) - 0.5 - offset * 0.5);
  float twinkle = 0.55 + 0.45 * sin(uSkinTime * speed + h * 40.0);
  return step(1.0 - density, h) * smoothstep(0.24, 0.02, d) * twinkle;
}`;

const FRAGMENT_DIFFUSE = /* glsl */ `vec3 skinP = (vSkinPos - uSkinMin) / max(uSkinMax - uSkinMin, vec3(1e-4)) - 0.5;
float skinT = uSkinRadial == 1 ? length(skinP - vec3(-0.12, 0.16, 0.3)) * 1.25 : dot(skinP.xy, uSkinDir) + 0.5;
vec3 skinColor = uSkinEffect == 3 ? skinRing(skinT * 0.9 - uSkinTime * 0.14) : skinSample(skinT);
vec4 diffuseColor = vec4( skinColor, opacity );`;

const FRAGMENT_EMISSIVE = /* glsl */ `#include <emissivemap_fragment>
if (uSkinEffect == 1) totalEmissiveRadiance += vec3(1.0) * skinSpecks(skinP, 7.0, 0.3, 2.4) * 2.6;
if (uSkinEffect == 2) {
  float sweep = fract(uSkinTime * 0.26) * 2.6 - 1.3;
  float band = exp(-pow((skinP.x + skinP.y * 0.6 - sweep) * 8.0, 2.0));
  totalEmissiveRadiance += vec3(band * 0.55);
}
if (uSkinEffect == 4) totalEmissiveRadiance += uSkinHighlight * (0.32 + 0.18 * sin(uSkinTime * 2.2));
if (uSkinEffect == 5) totalEmissiveRadiance += uSkinHighlight * skinSpecks(skinP, 5.0, 0.18, 1.6) * 2.4;`;

/** Effect ids as the shader numbers them (index in SKIN_EFFECTS). */
function effectCode(skin: CompanionSkin): number {
  return SKIN_EFFECTS.indexOf(skinEffect(skin));
}

/**
 * The body material for a design. `geometry` gives the box the gradient spans;
 * call `advanceSkin` every frame to move its effect.
 */
export function createSkinMaterial(skin: CompanionSkin, geometry: BufferGeometry | undefined): MeshStandardMaterial {
  const box = new Box3(new Vector3(-0.5, -0.5, -0.5), new Vector3(0.5, 0.5, 0.5));
  if (geometry) {
    if (!geometry.boundingBox) geometry.computeBoundingBox();
    if (geometry.boundingBox) box.copy(geometry.boundingBox);
  }
  const colors = [0, 1, 2, 3].map(i => new Color(skin.colors[Math.min(i, skin.colors.length - 1)]!));
  const rad = skin.angle * Math.PI / 180;
  const uniforms: SkinUniforms = {
    uSkinColors: { value: colors },
    uSkinCount: { value: skin.colors.length },
    uSkinRadial: { value: skin.pattern === "radial" ? 1 : 0 },
    uSkinDir: { value: new Vector2(Math.sin(rad), Math.cos(rad)) },
    uSkinMin: { value: box.min.clone() },
    uSkinMax: { value: box.max.clone() },
    uSkinEffect: { value: effectCode(skin) },
    uSkinTime: { value: 0 },
    uSkinHighlight: { value: new Color(skinHighlight(skin.colors)) },
  };
  const material = new MeshStandardMaterial({ color: "#ffffff", roughness: skinEffect(skin) === "shimmer" ? 0.38 : 0.62 });
  material.userData.skinUniforms = uniforms;
  material.onBeforeCompile = shader => {
    Object.assign(shader.uniforms, uniforms);
    shader.vertexShader = shader.vertexShader
      .replace("#include <common>", VERTEX_HEAD)
      .replace("#include <begin_vertex>", VERTEX_BODY);
    shader.fragmentShader = shader.fragmentShader
      .replace("#include <common>", FRAGMENT_HEAD)
      .replace("vec4 diffuseColor = vec4( diffuse, opacity );", FRAGMENT_DIFFUSE)
      .replace("#include <emissivemap_fragment>", FRAGMENT_EMISSIVE);
  };
  material.customProgramCacheKey = () => "jarvis-companion-skin";
  return material;
}

/** Moves a design's effect on; a material without a design is left alone. */
export function advanceSkin(material: MeshStandardMaterial, seconds: number): void {
  const uniforms = material.userData.skinUniforms as SkinUniforms | undefined;
  if (uniforms) uniforms.uSkinTime.value = (uniforms.uSkinTime.value + seconds) % 3600;
}
