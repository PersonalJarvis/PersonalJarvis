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

export interface LoadedGltf {
  scene: THREE.Object3D;
  animations: THREE.AnimationClip[];
  parser: { json: { asset?: { extras?: { jarvis_figure?: FigureExtras } } } };
}

export function readFigureExtras(gltf: LoadedGltf): FigureExtras | null {
  return gltf.parser.json.asset?.extras?.jarvis_figure ?? null;
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
  dispose(): void;
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
  body.traverse((node) => {
    if (node.name === "FWD") node.visible = false;
    if (!(node instanceof THREE.Mesh)) return;
    const original = (Array.isArray(node.material) ? node.material[0] : node.material) as
      | THREE.MeshStandardMaterial
      | THREE.MeshBasicMaterial;
    const map = original.map ?? null;
    if (map && !painted) painted = paintPalette(map, palette);
    const material = new THREE.MeshLambertMaterial({
      map: painted ?? map,
      color: 0xffffff,
    });
    node.material = material;
    // A skinned mesh's bounds ignore its bones; culling it by the rest pose
    // hides a figure whose arms leave the box. Two draw calls are cheaper.
    node.frustumCulled = false;
    node.castShadow = false;
    node.receiveShadow = false;
    owned.push(material);
  });
  if (painted) owned.push(painted);

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
