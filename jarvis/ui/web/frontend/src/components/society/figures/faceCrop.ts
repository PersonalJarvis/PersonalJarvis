/**
 * A rendered headshot per look — the face the roster rail, chat avatars and
 * nameplates show instead of a colour circle (character-pipeline.md §9.1).
 *
 * One hidden renderer serves every crop in turn: a 96×96 frame of the
 * assembled figure's head, read back as a PNG data URL and cached by the
 * recipe's key. The renderer is created on the first request and its
 * context is handed back (`forceContextLoss` + `dispose`) after a few idle
 * seconds — the page has sixteen contexts in total (AP-32) and a headshot
 * queue must never hold one open. Loads go through one GLTFLoader cache of
 * its own; drei's cache belongs to React trees.
 */
import * as THREE from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";

import { assembleFigure, type LoadedGltf } from "./assembleFigure";
import { recipeKey, resolvePalette, type FigureRecipe } from "./figureRecipe";
import { figureAssetFor, partAssetsFor } from "./figureRegistry";

const SIZE = 128;
const IDLE_RELEASE_MS = 4000;

const crops = new Map<string, Promise<string | null>>();
const gltfs = new Map<string, Promise<LoadedGltf>>();
let loader: GLTFLoader | null = null;
let stage: { renderer: THREE.WebGLRenderer; scene: THREE.Scene; camera: THREE.PerspectiveCamera } | null = null;
let releaseTimer = 0;

function loadGltf(url: string): Promise<LoadedGltf> {
  let pending = gltfs.get(url);
  if (!pending) {
    loader ??= new GLTFLoader();
    pending = loader.loadAsync(url) as unknown as Promise<LoadedGltf>;
    gltfs.set(url, pending);
  }
  return pending;
}

function acquireStage() {
  window.clearTimeout(releaseTimer);
  if (stage) return stage;
  const canvas = document.createElement("canvas");
  canvas.width = SIZE;
  canvas.height = SIZE;
  const renderer = new THREE.WebGLRenderer({
    canvas,
    alpha: true,
    antialias: false,
    preserveDrawingBuffer: true,
    powerPreference: "low-power",
  });
  renderer.setPixelRatio(1);
  renderer.setSize(SIZE, SIZE, false);
  renderer.setClearColor(0x000000, 0);
  const scene = new THREE.Scene();
  scene.add(new THREE.HemisphereLight(0xffffff, 0x8fa0b3, 1.15));
  const key = new THREE.DirectionalLight(0xffffff, 1.9);
  key.position.set(2.2, 4.5, 3.2);
  scene.add(key);
  const fill = new THREE.DirectionalLight(0xffffff, 0.45);
  fill.position.set(-3, 2, -2);
  scene.add(fill);
  const camera = new THREE.PerspectiveCamera(24, 1, 0.05, 20);
  stage = { renderer, scene, camera };
  return stage;
}

function scheduleRelease() {
  window.clearTimeout(releaseTimer);
  releaseTimer = window.setTimeout(() => {
    if (!stage) return;
    // Hand the context back: dispose() alone keeps it (useWebglSurface's lesson).
    stage.renderer.forceContextLoss();
    stage.renderer.dispose();
    stage = null;
  }, IDLE_RELEASE_MS);
}

async function renderCrop(recipe: FigureRecipe): Promise<string | null> {
  const asset = figureAssetFor(recipe);
  if (!asset) return null;
  const parts = partAssetsFor(recipe);
  const [base, ...partGltfs] = await Promise.all([loadGltf(asset.url), ...parts.map((p) => loadGltf(p.url))]);
  const height = recipe.heightM ?? asset.defaultHeightM;
  const figure = assembleFigure(base, resolvePalette(recipe), height, partGltfs);
  if (!figure) return null;
  const { renderer, scene, camera } = acquireStage();
  try {
    // Turn the figure a little toward the light and frame the whole head
    // with air around it: the swatch shows the full face, never a cropped
    // close-up. A spirit carries its face lower on the body than a biped
    // carries its head.
    figure.root.rotation.y = -0.25;
    scene.add(figure.root);
    figure.actions.idle?.play();
    figure.mixer.update(0.4);
    const archetype = figure.extras.archetype;
    const spirit = archetype === "spirit";
    const quadruped = archetype === "quadruped";
    const headY = height * (spirit ? 0.66 : 0.78);
    // Per-archetype distance: a human head is small relative to its body,
    // so a biped needs a closer camera than the mascot or an animal whose
    // face already fills more of the frame at the same factor.
    const distance = height * (spirit ? 1.6 : quadruped ? 1.35 : 0.95);
    camera.position.set(0.06, headY + height * 0.04, distance);
    camera.lookAt(0, headY, 0);
    camera.updateProjectionMatrix();
    renderer.render(scene, camera);
    return renderer.domElement.toDataURL("image/png");
  } finally {
    scene.remove(figure.root);
    figure.dispose();
    scheduleRelease();
  }
}

/** The headshot for a look, cached; null when the recipe has no built base or WebGL is absent. */
export function faceCrop(recipe: FigureRecipe | null): Promise<string | null> {
  if (!recipe || typeof document === "undefined") return Promise.resolve(null);
  const key = recipeKey(recipe);
  let pending = crops.get(key);
  if (!pending) {
    pending = renderCrop(recipe).catch((error: unknown) => {
      // A crop that cannot be drawn falls back to the colour swatch, out loud.
      console.warn("[society] face crop failed:", error);
      crops.delete(key);
      return null;
    });
    crops.set(key, pending);
  }
  return pending;
}
