import { Component, Suspense, useEffect, useMemo, useRef, type ReactNode, type RefObject } from "react";
import { useGLTF } from "@react-three/drei";
import { useFrame, useThree } from "@react-three/fiber";
import { Group, Mesh, MeshStandardMaterial, Vector3, type Material } from "three";
import { useReducedMotion } from "framer-motion";
import companionModels from "@/assets/society/companions/companions.glb";
import gigiModel from "@/assets/society/companions/gigi.glb";
import accessoryModels from "@/assets/society/companions/accessories.glb";
import { ACCESSORY_CATALOG, resolveFill, wornAccessories } from "./accessories";
import { companionEyeColors, type CompanionAppearance } from "./appearance";
import { advancePetTrail, createPetTrail, petDisplayPosition, recordOwner, type PetTrail, type TrailPoint } from "./trail";

class PetBoundary extends Component<{ children: ReactNode }, { failed: boolean }> {
  state = { failed: false };
  static getDerivedStateFromError() { return { failed: true }; }
  componentDidCatch(error: Error) { console.warn("Companion asset unavailable", error); }
  render() { return this.state.failed ? null : this.props.children; }
}

/** A cached authored mesh, with instance-owned materials and no extra canvas. */
export function CompanionModel({ appearance, lead = false }: { appearance: CompanionAppearance; lead?: boolean }) {
  const { scene } = useGLTF(lead ? gigiModel : companionModels);
  const instance = useMemo(() => {
    const original = lead ? scene : scene.getObjectByName(appearance.shape);
    if (!original) throw new Error("Companion silhouette missing from asset");
    const model = original.clone(true);
    const materials: MeshStandardMaterial[] = [];
    if (!lead) {
      const ink = companionEyeColors(appearance.color);
      const body = new MeshStandardMaterial({ color: appearance.color, roughness: 0.82 });
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
  }, [scene, lead, appearance.shape, appearance.color, appearance.eyes]);
  useEffect(() => () => { instance.materials.forEach(material => material.dispose()); }, [instance]);
  const wearing = !lead && wornAccessories(appearance.accessories).length > 0;
  // Gigi's authored body is 0.4 m; the symbol master is exactly 1 m.
  return <group scale={appearance.sizeM / (lead ? 0.4 : 1)}>
    <primitive object={instance.model} dispose={null} />
    {/* Accessories load on their own, so a missing asset never hides the companion. */}
    {wearing && <PetBoundary><Suspense fallback={null}><CompanionAccessories appearance={appearance} /></Suspense></PetBoundary>}
  </group>;
}

/** Worn items from the shared catalog, placed on the 1 m symbol master. */
function CompanionAccessories({ appearance }: { appearance: CompanionAppearance }) {
  const { scene } = useGLTF(accessoryModels);
  const instance = useMemo(() => {
    const group = new Group();
    const materials: Material[] = [];
    const meta = ACCESSORY_CATALOG.shapes[appearance.shape];
    const unit = 1 / (meta.bottom - meta.top);
    for (const item of wornAccessories(appearance.accessories)) {
      const free = scene.getObjectByName(`acc_${item.id}`);
      if (free) {
        const [x, y, k] = meta.anchors[item.slot];
        const node = free.clone(true);
        node.position.set((x - 20) * unit, (meta.bottom - y) * unit, ACCESSORY_CATALOG.slotDepth[item.slot] * ACCESSORY_CATALOG.frontDepthM);
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
      mesh.material = own;
      materials.push(own);
    });
    return { group, materials };
  }, [scene, appearance.shape, appearance.color, appearance.accessories]);
  useEffect(() => () => { instance.materials.forEach(material => material.dispose()); }, [instance]);
  return <primitive object={instance.group} dispose={null} />;
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
    const bob = reduced || trail.speed < 0.02 ? 0 : Math.abs(Math.sin(phase.current)) * 0.035;
    const display = petDisplayPosition(trail.position, trail.yaw, appearance.sizeM, clear);
    root.current.position.set(display[0], display[1] + 0.04 + bob, display[2]);
    root.current.rotation.y = trail.points.length ? trail.yaw : owner.current.rotation.y;
    if (trail.speed > 0.005) invalidate();
  });
  return <group ref={root} visible={false}>
    {appearance.enabled && <PetBoundary key={lead ? "gigi" : appearance.shape}><Suspense fallback={null}><CompanionModel appearance={appearance} lead={lead} /></Suspense></PetBoundary>}
  </group>;
}
