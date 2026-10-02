/**
 * The 3D body of the person's chosen pet in the Jarvis Verse. Built-in pets
 * load their authored GLB and move by `petRig.ts`; a pet the person drew
 * becomes a voxel figure cut from its own idle frames, turned to the camera.
 * Both stand on their origin (bottom centre); the host places and turns them.
 */
import { useEffect, useMemo, useRef, useState } from "react";
import { useGLTF } from "@react-three/drei";
import { useFrame } from "@react-three/fiber";
import {
  BoxGeometry, Color, InstancedMesh, Matrix4, MeshStandardMaterial, Object3D, Quaternion, Vector3, type Group,
} from "three";
import type { CompanionPet } from "./petCompanions";
import { createRigState, rigPose, stepRig, type PartMotion, type PetMood } from "./petRig";
import { voxelFrames, type VoxelFrames } from "./voxelPet";

/** What the host knows each frame: how fast the pet moves over the ground and what Jarvis is doing. */
export interface PetDrive {
  speed: number;
  mood: PetMood;
}

const PET_FILES = import.meta.glob("../../../assets/society/companions/pets/*.glb", {
  query: "?url",
  import: "default",
  eager: true,
}) as Record<string, string>;

export function petModelUrl(id: string): string | null {
  const suffix = `/pets/${id}.glb`;
  for (const [key, url] of Object.entries(PET_FILES)) if (key.endsWith(suffix)) return url;
  return null;
}

interface RiggedPart {
  object: Object3D;
  position: Vector3;
  rotation: [number, number, number];
  scale: Vector3;
}

function applyMotion(part: RiggedPart, motion: PartMotion): void {
  const { object, position, rotation, scale } = part;
  object.position.set(position.x, position.y + motion.dy, position.z);
  object.rotation.set(rotation[0] + motion.rx, rotation[1] + motion.ry, rotation[2] + motion.rz);
  object.scale.set(scale.x * motion.sx, scale.y * motion.sy, scale.z * motion.sz);
}

/** A built-in pet: its authored model, animated part by part. */
export function PetModel({ pet, drive, reduced, paused }: {
  pet: CompanionPet; drive: { current: PetDrive }; reduced: boolean; paused: boolean;
}) {
  const url = petModelUrl(pet.id);
  if (!url) throw new Error(`No 3D model for pet ${pet.id}`);
  const { scene } = useGLTF(url);
  const instance = useMemo(() => {
    const model = scene.clone(true);
    const parts = new Map<string, RiggedPart>();
    model.traverse((object) => {
      if ((object as { isMesh?: boolean }).isMesh) object.castShadow = true;
      const name = typeof object.userData.name === "string" ? object.userData.name : object.name;
      if (name.startsWith(`${pet.id}_`)) {
        parts.set(name, {
          object,
          position: object.position.clone(),
          rotation: [object.rotation.x, object.rotation.y, object.rotation.z],
          scale: object.scale.clone(),
        });
      }
    });
    return { model, parts };
  }, [scene, pet.id]);
  const rig = useRef(createRigState());
  const pose = useMemo(() => new Map<string, PartMotion>(), []);
  useEffect(() => { pose.clear(); }, [pet.id, pose]);
  useFrame((_, dt) => {
    if (paused) return;
    const { speed, mood } = drive.current;
    stepRig(rig.current, pet, { dt, speed, mood, reduced });
    rigPose(pet, rig.current, mood, pose);
    for (const [name, motion] of pose) {
      const part = instance.parts.get(name);
      if (part) applyMotion(part, motion);
    }
  });
  return <primitive object={instance.model} scale={pet.heightM / pet.modelHeightM} dispose={null} />;
}

/** Reads a same-origin sprite sheet into voxel frames; null while loading or when unusable. */
function useVoxelFrames(pet: CompanionPet): VoxelFrames | null {
  const [frames, setFrames] = useState<VoxelFrames | null>(null);
  useEffect(() => {
    setFrames(null);
    if (!pet.sheetUrl || !pet.frameSize || !pet.idle) return;
    let cancelled = false;
    const image = new Image();
    image.onload = () => {
      if (cancelled) return;
      const canvas = document.createElement("canvas");
      canvas.width = image.naturalWidth;
      canvas.height = image.naturalHeight;
      const context = canvas.getContext("2d", { willReadFrequently: true });
      if (!context) return;
      context.drawImage(image, 0, 0);
      const { data } = context.getImageData(0, 0, canvas.width, canvas.height);
      const idle = pet.idle!;
      const result = voxelFrames(data, canvas.width, canvas.height, pet.frameSize!, idle.row, idle.frames);
      if (!result) console.warn("Pet sheet has no usable idle frames for a 3D figure", pet.id);
      setFrames(result);
    };
    image.onerror = () => { if (!cancelled) console.warn("Pet sheet could not load", pet.id); };
    image.src = pet.sheetUrl;
    return () => { cancelled = true; image.onload = null; image.onerror = null; };
  }, [pet.id, pet.sheetUrl, pet.frameSize, pet.idle]);
  return frames;
}

/** Voxel figure of a drawn pet: one instanced cube per opaque pixel, flipping through its idle frames. */
export function VoxelPet({ pet, reduced, paused }: { pet: CompanionPet; reduced: boolean; paused: boolean }) {
  const frames = useVoxelFrames(pet);
  const holder = useRef<Group>(null);
  const shown = useRef(-1);
  const clock = useRef(0);
  const capacity = frames ? Math.max(...frames.frames.map((f) => f.length)) : 0;
  const resources = useMemo(() => {
    if (!frames || capacity === 0) return null;
    const geometry = new BoxGeometry(1, 1, 1);
    const material = new MeshStandardMaterial({ roughness: 0.85 });
    const mesh = new InstancedMesh(geometry, material, capacity);
    mesh.castShadow = true;
    mesh.frustumCulled = false;
    return { mesh, geometry, material };
  }, [frames, capacity]);
  useEffect(() => () => {
    if (!resources) return;
    resources.mesh.dispose(); resources.geometry.dispose(); resources.material.dispose();
  }, [resources]);
  const scratch = useMemo(() => ({ matrix: new Matrix4(), colour: new Color(), parent: new Quaternion(), facing: new Quaternion(), up: new Vector3(0, 1, 0), at: new Vector3() }), []);
  useEffect(() => { shown.current = -1; }, [resources]);

  useFrame(({ camera }, dt) => {
    if (!frames || !resources || !holder.current) return;
    if (!paused && !reduced) clock.current += Math.min(dt, 0.1);
    const index = Math.floor(clock.current * (pet.idle?.fps ?? 6)) % frames.frames.length;
    if (index !== shown.current) {
      shown.current = index;
      const voxel = pet.heightM / (frames.maxY - frames.minY + 1);
      const centreX = (frames.minX + frames.maxX + 1) / 2;
      const { mesh } = resources;
      const list = frames.frames[index];
      for (let i = 0; i < list.length; i++) {
        const v = list[i];
        scratch.matrix.makeScale(voxel, voxel, voxel * 2);
        scratch.matrix.setPosition((v.x + 0.5 - centreX) * voxel, (v.y - frames.minY + 0.5) * voxel, 0);
        mesh.setMatrixAt(i, scratch.matrix);
        mesh.setColorAt(i, scratch.colour.setHex(v.color));
      }
      mesh.count = list.length;
      mesh.instanceMatrix.needsUpdate = true;
      if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
    }
    // A cut-out reads best face on: keep turning it towards the camera.
    const group = holder.current;
    const parent = group.parent;
    group.getWorldPosition(scratch.at);
    const yaw = Math.atan2(camera.position.x - scratch.at.x, camera.position.z - scratch.at.z);
    scratch.facing.setFromAxisAngle(scratch.up, yaw);
    if (parent) {
      parent.getWorldQuaternion(scratch.parent);
      group.quaternion.copy(scratch.parent.invert().multiply(scratch.facing));
    } else group.quaternion.copy(scratch.facing);
  });

  return <group ref={holder}>{resources && <primitive object={resources.mesh} />}</group>;
}
