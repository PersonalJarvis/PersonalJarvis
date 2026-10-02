// @vitest-environment node
/**
 * The shipped pet GLBs and the rig must agree: every pivot the rig moves has
 * to exist in the model, or that limb silently stands still.
 */
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";
import { Box3, type Object3D } from "three";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { BUILTIN_COMPANIONS, type CompanionPet } from "./petCompanions";
import { createRigState, rigPose, stepRig } from "./petRig";

const PETS_DIR = resolve(__dirname, "../../../assets/society/companions/pets");

async function load(id: string): Promise<Object3D> {
  const bytes = readFileSync(resolve(PETS_DIR, `${id}.glb`));
  const buffer = bytes.buffer.slice(bytes.byteOffset, bytes.byteOffset + bytes.byteLength);
  const gltf = await new GLTFLoader().parseAsync(buffer, "");
  return gltf.scene;
}

describe("pet models", () => {
  for (const id of Object.keys(BUILTIN_COMPANIONS)) {
    it(`${id}: has every pivot the rig animates, stands on the floor and matches its listed height`, async () => {
      const scene = await load(id);
      const names = new Set<string>();
      scene.traverse((object) => names.add(String(object.userData.name ?? object.name)));
      const pet: CompanionPet = { id, ...BUILTIN_COMPANIONS[id] };
      const state = createRigState();
      for (const mood of ["idle", "work", "talk", "sleep"] as const) {
        stepRig(state, pet, { dt: 1 / 60, speed: 1, mood, reduced: false });
        for (const part of rigPose(pet, state, mood).keys()) expect(names, `${part} missing`).toContain(part);
      }
      const box = new Box3().setFromObject(scene);
      expect(box.min.y).toBeGreaterThan(-0.01);
      expect(box.max.y - box.min.y).toBeCloseTo(pet.modelHeightM, 2);
      // Faces forward (+z): the eyes sit in front of the centre.
      const eye = scene.getObjectByName(`${id}_Eye_L`)!;
      const at = eye.getWorldPosition(eye.position.clone());
      expect(at.z).toBeGreaterThan(0);
    });
  }
});
