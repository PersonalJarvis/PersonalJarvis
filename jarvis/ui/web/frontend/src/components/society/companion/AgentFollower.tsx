import { Component, Suspense, useEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import { useGLTF } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { Group, Mesh, MeshBasicMaterial, MeshStandardMaterial, SphereGeometry, Vector3, type Material } from "three";
import { useReducedMotion } from "framer-motion";
import companionModels from "@/assets/society/companions/companions.glb";
import gigiModel from "@/assets/society/companions/gigi.glb";
import accessoryModels from "@/assets/society/companions/accessories.glb";
import { ACCESSORY_CATALOG, resolveFill, slotDepthM, wornAccessories } from "./accessories";
import { companionEyeColors, type CompanionAppearance } from "./appearance";
import { advancePetTrail, createPetTrail, petDisplayPosition, recordOwner, type PetTrail, type TrailPoint } from "./trail";
import { useWornPet } from "./companionPetStore";
import { companionFlies, type CompanionPet } from "./petCompanions";
import { PetModel, VoxelPet, type PetDrive } from "./PetModel";
import { advanceSkin, createSkinMaterial } from "./skinMaterial";

class PetBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.warn("Companion asset unavailable", error); }
  render() { return this.state.failed ? null : this.props.children; }
}

/** A flying pet worn by an agent hovers this high beside its owner, metres. */
export const WORN_PET_LIFT_M = 0.32;

/**
 * An agent's companion in 3D: its shape, or the pet it wears instead
 * (`companion.pet`) with that pet's own model and gait. While the pets
 * answer is still on its way a worn pet draws nothing, so no shape flashes
 * first; a pet this machine does not have falls back to the shape.
 */
export function CompanionModel({ appearance, lead = false, drive, paused = false }: {
  appearance: CompanionAppearance; lead?: boolean;
  /** How fast the owner moves, for a worn pet's gait; a still pet without it. */
  drive?: { current: PetDrive }; paused?: boolean;
}) {
  const pet = useWornPet(lead ? undefined : appearance.pet);
  if (pet === undefined) return null;
  if (pet) return <WornPet pet={pet} drive={drive} paused={paused} />;
  return <ShapeModel appearance={appearance} lead={lead} />;
}

function WornPet({ pet, drive, paused }: { pet: CompanionPet; drive?: { current: PetDrive }; paused: boolean }) {
  const reduced = useReducedMotion() ?? false;
  const still = useRef<PetDrive>({ speed: 0, mood: "idle" });
  const lift = companionFlies(pet) ? WORN_PET_LIFT_M : 0;
  return <group position={[0, lift, 0]}>
    {pet.kind === "model" ? <PetModel pet={pet} drive={drive ?? still} reduced={reduced} paused={paused} />
      : pet.kind === "voxel" ? <VoxelPet pet={pet} reduced={reduced} paused={paused} />
      // Gigi worn by an agent: the lead's own hover model at companion size.
      : <ShapeModel appearance={GIGI_LOOK} lead />}
  </group>;
}

const GIGI_LOOK: CompanionAppearance = {
  shape: "circle", color: "#ffcd61", eyes: "dots", enabled: true, accessories: {}, sizeM: 0.5, followDistanceM: 1,
};

/** A cached authored mesh, with instance-owned materials and no extra canvas. */
function ShapeModel({ appearance, lead = false }: { appearance: CompanionAppearance; lead?: boolean }) {
  const { scene } = useGLTF(lead ? gigiModel : companionModels);
  const reduced = useReducedMotion() ?? false;
  // The design by value: a freshly parsed but equal skin must not rebuild the material.
  const skinKey = appearance.skin ? JSON.stringify(appearance.skin) : "";
  const instance = useMemo(() => {
    const original = lead ? scene : scene.getObjectByName(appearance.shape);
    if (!original) throw new Error("Companion silhouette missing from asset");
    const model = original.clone(true);
    const materials: MeshStandardMaterial[] = [];
    if (!lead) {
      const ink = companionEyeColors(appearance.color);
      // A soft vinyl sheen lets the light show the volume of each body.
      let bodyMesh: Mesh | undefined;
      model.traverse(object => { if ((object as Mesh).isMesh && object.name.includes("Body")) bodyMesh ??= object as Mesh; });
      const body = appearance.skin ? createSkinMaterial(appearance.skin, bodyMesh?.geometry)
        : new MeshStandardMaterial({ color: appearance.color, roughness: 0.62 });
      const eyes = new MeshStandardMaterial({ color: ink.eye, roughness: 1 });
      const shine = new MeshStandardMaterial({ color: ink.highlight, roughness: 1 });
      materials.push(body, eyes, shine);
      model.traverse(object => {
        const mesh = object as Mesh;
        if (!mesh.isMesh) return;
        const name = mesh.name;
        mesh.castShadow = true;
        mesh.visible = name.includes("Dots") ? appearance.eyes === "dots" : name.includes("Lines") ? appearance.eyes === "lines" : !name.includes("Highlight");
        mesh.material = name.includes("Body") ? body : name.includes("Highlight") ? shine : eyes;
      });
    }
    return { model, materials };
    // eslint-disable-next-line react-hooks/exhaustive-deps -- appearance.skin is read through skinKey
  }, [scene, lead, appearance.shape, appearance.color, appearance.eyes, skinKey]);
  useEffect(() => () => { instance.materials.forEach(material => material.dispose()); }, [instance]);
  useFrame((_, delta) => { if (!reduced && skinKey) advanceSkin(instance.materials[0]!, delta); });
  const wearing = !lead && wornAccessories(appearance.accessories).length > 0;
  // Gigi's authored body is 0.4 m; the symbol master is exactly 1 m.
  return <group scale={appearance.sizeM / (lead ? 0.4 : 1)}>
    <primitive object={instance.model} dispose={null} />
    {/* Accessories load on their own, so a missing asset never hides the companion. */}
    {wearing && <PetBoundary><Suspense fallback={null}><CompanionAccessories appearance={appearance} /></Suspense></PetBoundary>}
  </group>;
}

const PUFFS = 7;
const PUFF_GEOMETRY = new SphereGeometry(1, 12, 8);

/**
 * Cigar smoke: a few soft puffs that leave the ember, swell, drift and fade
 * on a loop. Plain meshes on the shared clock; the Verse renders every frame
 * while it is awake, so the plume adds no extra frames. Reduced motion shows
 * no plume (the cigar keeps its glowing ember).
 */
function SmokePlume({ position, scale, color }: { position: [number, number, number]; scale: number; color: string }) {
  const puffs = useRef<(Mesh | null)[]>([]);
  const reduced = useReducedMotion() ?? false;
  const materials = useMemo(() => Array.from({ length: PUFFS }, () =>
    new MeshBasicMaterial({ color, transparent: true, opacity: 0, depthWrite: false })), [color]);
  useEffect(() => () => { materials.forEach(material => material.dispose()); }, [materials]);
  useFrame(({ clock }) => {
    const t = clock.getElapsedTime();
    puffs.current.forEach((puff, i) => {
      if (!puff) return;
      const phase = (t / 3.4 + i / PUFFS) % 1;
      const wobble = Math.sin((t * 1.3 + i * 1.7)) * 0.05;
      puff.position.set(phase * 0.12 + wobble * phase, phase * 0.62, phase * 0.04);
      puff.scale.setScalar(0.035 + phase * 0.13);
      materials[i]!.opacity = Math.sin(Math.min(1, phase * 1.25) * Math.PI) * 0.42;
    });
  });
  if (reduced) return null;
  return <group position={position} scale={scale}>
    {materials.map((material, i) => <mesh key={i} ref={node => { puffs.current[i] = node; }} geometry={PUFF_GEOMETRY} material={material} />)}
  </group>;
}

/** Worn items from the shared catalog, placed on the 1 m symbol master. */
function CompanionAccessories({ appearance }: { appearance: CompanionAppearance }) {
  const { scene } = useGLTF(accessoryModels);
  // Polished metal reads black without something to reflect: soften it then.
  const reflective = useThree(state => !!state.scene.environment);
  const instance = useMemo(() => {
    const group = new Group();
    const materials: Material[] = [];
    const meta = ACCESSORY_CATALOG.shapes[appearance.shape];
    const unit = 1 / (meta.bottom - meta.top);
    for (const item of wornAccessories(appearance.accessories)) {
      // Items worn on the body come pre-fitted to each silhouette's curved surface.
      const fitted = scene.getObjectByName(`acc_${item.id}__${appearance.shape}__fit`);
      const free = fitted ? undefined : scene.getObjectByName(`acc_${item.id}`);
      if (fitted) group.add(fitted.clone(true));
      if (free) {
        const [x, y, k] = meta.anchors[item.slot];
        const node = free.clone(true);
        node.position.set((x - 20) * unit, (meta.bottom - y) * unit, slotDepthM(appearance.shape, item.slot));
        node.rotation.set(0, 0, 0);
        node.scale.setScalar(k * (item.scale ?? 1) * unit);
        group.add(node);
      }
      // Clothing is authored per silhouette, already in the master's frame.
      const patch = item.regions ? scene.getObjectByName(`acc_${item.id}__${appearance.shape}`) : undefined;
      if (patch) group.add(patch.clone(true));
    }
    group.traverse(object => {
      const mesh = object as Mesh;
      if (!mesh.isMesh) return;
      mesh.castShadow = true;
      const source = mesh.material as MeshStandardMaterial;
      const own = source.clone();
      const token = source.name.split(":")[1] ?? "";
      if (token && !token.startsWith("#")) own.color.set(resolveFill(token, appearance.color));
      if (!reflective && own.metalness > 0.5) { own.metalness = 0.35; own.roughness = 0.42; }
      mesh.material = own;
      materials.push(own);
    });
    // Smoke sources, placed exactly like the free parts they belong to.
    const plumes: { key: string; position: [number, number, number]; scale: number; color: string }[] = [];
    for (const item of wornAccessories(appearance.accessories)) {
      const [x, y, k] = meta.anchors[item.slot];
      const size = k * (item.scale ?? 1) * unit;
      const depth = slotDepthM(appearance.shape, item.slot);
      item.parts.forEach((part, index) => {
        if (part.t !== "smoke") return;
        plumes.push({
          key: `${item.id}-${index}`, color: part.fill, scale: part.size,
          position: [(x - 20) * unit + part.c[0] * size, (meta.bottom - y) * unit - part.c[1] * size, depth + part.c[2] * size],
        });
      });
    }
    return { group, materials, plumes };
  }, [scene, reflective, appearance.shape, appearance.color, appearance.accessories]);
  useEffect(() => () => { instance.materials.forEach(material => material.dispose()); }, [instance]);
  return <>
    <primitive object={instance.group} dispose={null} />
    {instance.plumes.map(plume => <SmokePlume key={plume.key} position={plume.position} scale={plume.scale} color={plume.color} />)}
  </>;
}

/** Sibling of the character: world-space footsteps never inherit its rotation. */
export function AgentFollower({ owner, appearance, paused, lead = false, waypoint, initialPosition, clear }: {
  owner: RefObject<Group>; appearance: CompanionAppearance; paused: boolean; lead?: boolean;
  waypoint?: { current: TrailPoint | null };
  /** A nearby point on the owner's verified navigation segment, when known. */
  initialPosition?: TrailPoint;
  clear?: (point: TrailPoint, radius: number) => boolean;
}) {
  const root = useRef<Group>(null);
  const motion = useRef<PetTrail | null>(null);
  const scratch = useMemo(() => new Vector3(), []);
  const phase = useRef(0);
  const reduced = useReducedMotion() ?? false;
  const { invalidate } = useThree();
  // A worn pet walks, waddles or flies on its own rig: it gets the owner's pace, not the shape's bob.
  const worn = useWornPet(lead ? undefined : appearance.pet);
  const drive = useRef<PetDrive>({ speed: 0, mood: "idle" });
  useEffect(() => { motion.current = null; invalidate(); }, [appearance.enabled, invalidate]);
  useFrame((_, delta) => {
    if (!root.current || !owner.current) return;
    root.current.visible = appearance.enabled && owner.current.visible;
    if (!root.current.visible || paused) return;
    owner.current.getWorldPosition(scratch);
    const point: TrailPoint = [scratch.x, scratch.y, scratch.z];
    let trail = motion.current ?? (motion.current = createPetTrail(initialPosition ?? point));
    if (!recordOwner(trail, point, waypoint?.current ?? undefined) && initialPosition) {
      // A delayed snapshot must not put the pet inside the character. The map
      // supplies an already verified nearby segment point for re-placement.
      trail = motion.current = createPetTrail(initialPosition);
      recordOwner(trail, point);
    }
    if (waypoint) waypoint.current = null;
    advancePetTrail(trail, appearance.followDistanceM, delta);
    if (!reduced) phase.current += Math.min(delta, 0.1) * trail.speed * 9;
    drive.current.speed = trail.speed;
    const bob = worn || reduced || trail.speed < 0.02 ? 0 : Math.abs(Math.sin(phase.current)) * 0.035;
    const display = petDisplayPosition(trail.position, trail.yaw, appearance.sizeM, clear);
    root.current.position.set(display[0], display[1] + 0.04 + bob, display[2]);
    root.current.rotation.y = trail.points.length ? trail.yaw : owner.current.rotation.y;
    if (trail.speed > 0.005) invalidate();
  });
  return <group ref={root} visible={false}>
    {appearance.enabled && <PetBoundary key={lead ? "gigi" : appearance.pet ?? appearance.shape}><Suspense fallback={null}><CompanionModel appearance={appearance} lead={lead} drive={drive} paused={paused} /></Suspense></PetBoundary>}
  </group>;
}
