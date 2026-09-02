/**
 * From one loaded base GLB to one agent's figure: a real skinned clone, the
 * recipe's palette painted into the sheet's strip, a Lambert material over
 * it, the mixer and its actions, and the scale that renders the source at
 * the recipe's height (docs/agent-society/character-pipeline.md §9.2).
 *
 * Pure with respect to React: the viewer and, later, the island's walkers
 * call this and own the result's lifetime (`dispose()`).
 */
import * as THREE from "three";
import { clone as cloneSkeleton } from "three/examples/jsm/utils/SkeletonUtils.js";

import { PALETTE_CELLS, type Palette } from "./figureRecipe";

/** `asset.extras.jarvis_figure`, as the build writes it. */
export interface FigureExtras {
  contract: number;
  archetype: string;
  variant: string;
  forward: string;
  /** Native height of the source mesh, metres. */
  height_m: number;
  target_height_m: number;
  clips: Record<string, { duration: number; loop: boolean; stride_m?: number }>;
}

/** The sheet layout the build wrote — mirrors scripts/figures/contract.json. */
export const SHEET = { size: 128, cellWidth: 8, cellHeight: 16 } as const;

/** `asset.extras.jarvis_part`, as the build writes it. */
export interface PartExtras {
  contract: number;
  archetype: string;
  slot: string;
  attach: string;
  hides: string[];
  label: string;
  /** Flat cloth with no back face of its own; see `two_sided` in the catalog. */
  two_sided?: boolean;
}

/**
 * The material slot a body primitive fills, read off its name — the build
 * names every slot `<figure id>-<slot>` (`biped-mage-hair`, `spirit-gigi-marks`).
 * A part's `hides` list names slots, so this is how the two meet.
 */
export function materialSlotOf(name: string): string {
  const cut = name.lastIndexOf("-");
  return cut < 0 ? "" : name.slice(cut + 1);
}

/** The slot every base paints its body with; nothing may hide it. */
const BODY_SLOT = "sheet";

export interface LoadedGltf {
  scene: THREE.Object3D;
  animations: THREE.AnimationClip[];
  parser: { json: { asset?: { extras?: { jarvis_figure?: FigureExtras; jarvis_part?: PartExtras } } } };
}

export function readFigureExtras(gltf: LoadedGltf): FigureExtras | null {
  return gltf.parser.json.asset?.extras?.jarvis_figure ?? null;
}

export function readPartExtras(gltf: LoadedGltf): PartExtras | null {
  return gltf.parser.json.asset?.extras?.jarvis_part ?? null;
}

/** Pixel sheets sample nearest, carry no mipmaps and live in sRGB — re-asserted on every load. */
export function prepareSheetTexture(texture: THREE.Texture): void {
  texture.magFilter = THREE.NearestFilter;
  texture.minFilter = THREE.NearestFilter;
  texture.generateMipmaps = false;
  texture.colorSpace = THREE.SRGBColorSpace;
  texture.needsUpdate = true;
}

/**
 * The recipe's sixteen colours painted over the strip of the base sheet;
 * face and detail area stay untouched. One 128×128 upload per look.
 */
export function paintPalette(source: THREE.Texture, palette: Palette): THREE.CanvasTexture {
  const image = source.image as CanvasImageSource & { width: number; height: number };
  const canvas = document.createElement("canvas");
  canvas.width = image.width || SHEET.size;
  canvas.height = image.height || SHEET.size;
  const ctx = canvas.getContext("2d");
  if (ctx) {
    ctx.imageSmoothingEnabled = false;
    ctx.drawImage(image, 0, 0);
    PALETTE_CELLS.forEach((cell, index) => {
      ctx.fillStyle = palette[cell];
      ctx.fillRect(index * SHEET.cellWidth, 0, SHEET.cellWidth, SHEET.cellHeight);
    });
  }
  const painted = new THREE.CanvasTexture(canvas);
  painted.flipY = false; // glTF textures are top-down; the source was too
  prepareSheetTexture(painted);
  return painted;
}

export interface AssembledFigure {
  root: THREE.Group;
  mixer: THREE.AnimationMixer;
  actions: Record<string, THREE.AnimationAction>;
  extras: FigureExtras;
  /** Source units → rendered metres. */
  scale: number;
  /**
   * Rendered metres from the ground to the top of everything worn — the body
   * plus a wizard hat's point. The body alone is `heightM`; a camera that
   * frames by that alone cuts the hat off, so the viewer frames by this.
   */
  renderedHeightM: number;
  /**
   * The largest of the three rendered extents. A person is taller than they
   * are wide, so for a biped this is the height; a fox is half again as long
   * as it is tall, and a camera pulled back by its height alone crops its
   * nose and tail.
   */
  renderedSpanM: number;
  dispose(): void;
}

/**
 * Take the source material's face sides, cutout and depth behaviour with us.
 * The pixel look is Lambert (or unlit for the glowing marks), but which faces
 * exist is geometry, not shading: a cape is one open sheet of cloth, and
 * rendering it front-side-only makes the half of it that faces the wearer —
 * and every edge that reaches past the body — disappear.
 */
function copySurface(from: THREE.Material, to: THREE.Material, forceDoubleSide: boolean): void {
  to.side = forceDoubleSide ? THREE.DoubleSide : from.side;
  to.transparent = from.transparent;
  to.opacity = from.opacity;
  to.alphaTest = from.alphaTest;
  to.depthWrite = from.depthWrite;
}

function firstMaterialOf(node: THREE.Mesh): THREE.Material {
  return (Array.isArray(node.material) ? node.material[0] : node.material) as THREE.Material;
}

/**
 * Clone the base for one agent. `SkeletonUtils.clone` is the only clone that
 * keeps a skinned mesh bound to ITS OWN skeleton copy; `Object3D.clone`
 * would leave every agent sharing one set of bones — and one pose.
 */
export function assembleFigure(
  gltf: LoadedGltf,
  palette: Palette,
  heightM: number,
  parts: LoadedGltf[] = [],
): AssembledFigure | null {
  const extras = readFigureExtras(gltf);
  if (!extras) return null;
  const root = new THREE.Group();
  root.name = "figure";
  const body = cloneSkeleton(gltf.scene);
  const scale = extras.height_m > 0 ? heightM / extras.height_m : 1;
  body.scale.setScalar(scale);
  root.add(body);

  const owned: Array<{ dispose(): void }> = [];
  let painted: THREE.CanvasTexture | null = null;
  const skinned: THREE.SkinnedMesh[] = [];
  const bodyMeshes: THREE.Mesh[] = [];
  body.traverse((node) => {
    if (node.name === "FWD") node.visible = false;
    if (!(node instanceof THREE.Mesh)) return;
    const original = firstMaterialOf(node) as THREE.MeshStandardMaterial | THREE.MeshBasicMaterial;
    const map = original.map ?? null;
    if (map && !painted) painted = paintPalette(map, palette);
    // A "-marks" slot (Gigi's eyes, mouth, scanlines) glows: unlit, so it
    // reads white on the dark body under any light, like the mascot in 2D.
    const material = materialSlotOf(original.name) === "marks"
      ? new THREE.MeshBasicMaterial({ map: painted ?? map, color: 0xffffff })
      : new THREE.MeshLambertMaterial({ map: painted ?? map, color: 0xffffff });
    material.name = original.name;
    copySurface(original, material, false);
    node.material = material;
    // A skinned mesh's bounds ignore its bones; culling it by the rest pose
    // hides a figure whose arms leave the box. Two draw calls are cheaper.
    node.frustumCulled = false;
    node.castShadow = false;
    node.receiveShadow = false;
    owned.push(material);
    bodyMeshes.push(node);
    if (node instanceof THREE.SkinnedMesh) skinned.push(node);
  });
  if (painted) owned.push(painted);

  // Parts: skinned to the same 23 bones in the same order, so a part binds to
  // the body's skeleton with the body's bind matrix and follows every clip.
  const anchor = skinned[0] ?? null;
  const hidden = new Set<string>();
  const worn: THREE.SkinnedMesh[] = [];
  for (const partGltf of parts) {
    const partExtras = readPartExtras(partGltf);
    // A part built for another archetype binds to bones this skeleton does
    // not have; it would hang in the air. The figure renders without it.
    if (!anchor || !partExtras || partExtras.archetype !== extras.archetype) continue;
    partGltf.scene.traverse((node) => {
      if (!(node instanceof THREE.SkinnedMesh)) return;
      const source = firstMaterialOf(node);
      // The part's own sheet is the fallback, not `undefined`: an imported
      // body carries no palette strip to paint, and a part without a map is
      // a flat white silhouette.
      const map = painted ?? (source as THREE.MeshStandardMaterial).map ?? undefined;
      const material = new THREE.MeshLambertMaterial({ map, color: 0xffffff });
      material.name = `part-${partExtras.slot}`;
      copySurface(source, material, partExtras.two_sided === true);
      const mesh = new THREE.SkinnedMesh(node.geometry, material);
      mesh.name = `part:${partExtras.slot}`;
      mesh.frustumCulled = false;
      mesh.bind(anchor.skeleton, anchor.bindMatrix);
      anchor.parent?.add(mesh);
      owned.push(material);
      worn.push(mesh);
    });
    for (const hide of partExtras.hides ?? []) hidden.add(hide);
  }
  // `hides` names material slots, not just hair: a full helmet hides the
  // hair, a diving suit could hide the hands. The body slot itself never goes.
  for (const mesh of bodyMeshes) {
    const slot = materialSlotOf(firstMaterialOf(mesh).name ?? "");
    if (slot && slot !== BODY_SLOT && hidden.has(slot)) mesh.visible = false;
  }

  // What the camera has to fit: the rest-pose bounds of everything visible,
  // in rendered metres. A wizard hat reaches a third of a metre above a
  // 1.75 m figure; framing by the body's height alone decapitates it.
  const bounds = new THREE.Box3();
  for (const mesh of [...bodyMeshes, ...worn]) {
    if (!mesh.visible) continue;
    mesh.geometry.computeBoundingBox();
    const box = mesh.geometry.boundingBox;
    if (box) bounds.union(box);
  }
  const renderedHeightM = bounds.isEmpty() ? heightM : Math.max(heightM, bounds.max.y * scale);
  const size = bounds.isEmpty() ? null : bounds.getSize(new THREE.Vector3());
  const renderedSpanM = size
    ? Math.max(renderedHeightM, size.x * scale, size.z * scale)
    : renderedHeightM;

  const mixer = new THREE.AnimationMixer(body);
  const actions: Record<string, THREE.AnimationAction> = {};
  for (const clip of gltf.animations) {
    const action = mixer.clipAction(clip, body);
    const facts = extras.clips[clip.name];
    action.loop = facts && !facts.loop ? THREE.LoopOnce : THREE.LoopRepeat;
    action.clampWhenFinished = true;
    actions[clip.name] = action;
  }

  return {
    root,
    mixer,
    actions,
    extras,
    scale,
    renderedHeightM,
    renderedSpanM,
    dispose() {
      mixer.stopAllAction();
      mixer.uncacheRoot(body);
      for (const item of owned) item.dispose();
      root.clear();
    },
  };
}

/** Cross-fade to a clip; a one-shot clip returns to `fallback` when it ends. */
export function playClip(
  figure: AssembledFigure,
  name: string,
  fallback = "idle",
  fadeSeconds = 0.2,
): THREE.AnimationAction | null {
  const next = figure.actions[name] ?? figure.actions[fallback];
  if (!next) return null;
  for (const [clipName, action] of Object.entries(figure.actions)) {
    if (action === next) continue;
    if (action.isRunning()) action.fadeOut(fadeSeconds);
    void clipName;
  }
  next.reset().fadeIn(fadeSeconds).play();
  if (next.loop === THREE.LoopOnce) {
    const mixer = figure.mixer;
    const onFinished = (event: { action: THREE.AnimationAction }) => {
      if (event.action !== next) return;
      mixer.removeEventListener("finished", onFinished);
      playClip(figure, fallback, fallback, fadeSeconds);
    };
    mixer.addEventListener("finished", onFinished);
  }
  return next;
}
