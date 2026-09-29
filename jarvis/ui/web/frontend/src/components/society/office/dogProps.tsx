/**
 * Small props of the office dog: the treat bone (carried by the person and by
 * the dog) and the treat jar in the break room that hands the bones out.
 * Built like every office prop: centred on the origin, front towards +z,
 * inside its FURNITURE_SIZE footprint.
 */
import { CylinderGeometry, MeshStandardMaterial, SphereGeometry } from "three";
import { Box } from "./OfficeFurniture";

const M = {
  bone: new MeshStandardMaterial({ color: "#f3ead7", roughness: 0.7 }),
  wood: new MeshStandardMaterial({ color: "#6d4a33", roughness: 0.7 }),
  woodDark: new MeshStandardMaterial({ color: "#4a3122", roughness: 0.7 }),
  brass: new MeshStandardMaterial({ color: "#d8ae52", roughness: 0.3, metalness: 0.75 }),
  glass: new MeshStandardMaterial({ color: "#dff1ff", roughness: 0.05, transparent: true, opacity: 0.28, depthWrite: false }),
  label: new MeshStandardMaterial({ color: "#8f2f3a", roughness: 0.8 }),
};
const G = { cyl: new CylinderGeometry(1, 1, 1, 14), sphere: new SphereGeometry(1, 14, 10) };

/** A cartoon bone along local x, about 0.2 m long at scale 1. */
export function TreatBone({ scale = 1 }: { scale?: number }) {
  return (
    <group scale={scale}>
      <mesh geometry={G.cyl} material={M.bone} rotation={[0, 0, Math.PI / 2]} scale={[0.022, 0.16, 0.022]} castShadow />
      {[-0.08, 0.08].flatMap((x) => [-0.022, 0.022].map((z) => (
        <mesh key={`${x}${z}`} geometry={G.sphere} material={M.bone} position={[x, 0, z]} scale={0.032} castShadow />
      )))}
    </group>
  );
}

/** A wooden stand with a glass jar of bones on top and a bone plaque on the front. */
export function TreatJar() {
  return (
    <group>
      <Box size={[0.46, 0.78, 0.46]} position={[0, 0.39, 0]} material={M.wood} />
      <Box size={[0.5, 0.04, 0.5]} position={[0, 0.8, 0]} material={M.woodDark} />
      <Box size={[0.3, 0.12, 0.012]} position={[0, 0.55, 0.236]} material={M.label} cast={false} />
      <group position={[0, 0.55, 0.246]} rotation={[Math.PI / 2, 0, 0]}><TreatBone scale={0.7} /></group>
      {/* The jar: bones inside, a brass lid with a knob. */}
      {[[-0.05, 0.88, 0.02, 0.5], [0.05, 0.9, -0.03, -0.6], [0, 0.97, 0.03, 1.2]].map(([x, y, z, r], i) => (
        <group key={i} position={[x, y, z]} rotation={[0, r, 0.4]}><TreatBone scale={0.6} /></group>
      ))}
      <mesh geometry={G.cyl} material={M.glass} position={[0, 0.96, 0]} scale={[0.16, 0.28, 0.16]} />
      <mesh geometry={G.cyl} material={M.brass} position={[0, 1.115, 0]} scale={[0.165, 0.03, 0.165]} castShadow />
      <mesh geometry={G.sphere} material={M.brass} position={[0, 1.15, 0]} scale={0.03} />
    </group>
  );
}
