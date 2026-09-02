/**
 * Everything that grows or lies on the island, in a handful of draw calls:
 * round trees (instanced trunks + two canopy layers), pines (trunk + a merged
 * two-cone crown), palms (a leaning trunk + a merged frond crown) and the
 * boulders. Kind, size and shade come from the layout's deterministic spots,
 * so the landscape is the same in every window.
 */
import { useMemo } from "react";
import { Color, InstancedMesh, Object3D } from "three";

import { buildIsland, type Boulder, type Post, type TreeSpot } from "./islandLayout";
import { NATURE } from "./worldPalette";
import { useKit } from "./WorldKit";

type Setup = (mesh: InstancedMesh | null) => void;

function RoundTrees({ spots }: { spots: TreeSpot[] }) {
  const { g, m } = useKit();
  const dummy = useMemo(() => new Object3D(), []);
  const colors = useMemo(
    () => ({ a: new Color(NATURE.canopyA), b: new Color(NATURE.canopyB), light: new Color(NATURE.canopyLight) }),
    [],
  );

  const trunks: Setup = (mesh) => {
    if (!mesh) return;
    spots.forEach((t, i) => {
      const h = 1.1 + t.size * 0.9;
      dummy.position.set(t.x, t.y + h / 2, t.z);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.set(0.42, h, 0.42);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  };

  const canopies = (mesh: InstancedMesh | null, upper: boolean) => {
    if (!mesh) return;
    spots.forEach((t, i) => {
      const h = 1.1 + t.size * 0.9;
      const r = upper ? 1.6 + t.size * 1.2 : 2.4 + t.size * 1.8;
      const dy = upper ? h + r * 0.55 + 0.9 : h + r * 0.35;
      const jitter = upper ? (t.shade - 0.5) * 0.9 : 0;
      dummy.position.set(t.x + jitter, t.y + dy, t.z - jitter * 0.6);
      dummy.rotation.set(0, t.shade * Math.PI, 0);
      dummy.scale.set(r, r * 0.85, r);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, upper ? (t.shade > 0.5 ? colors.light : colors.b) : t.shade > 0.5 ? colors.b : colors.a);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  };

  if (spots.length === 0) return null;
  return (
    <group>
      <instancedMesh ref={trunks} args={[g.cylinder, m.lit(NATURE.trunk), spots.length]} />
      <instancedMesh ref={(mesh) => canopies(mesh, false)} args={[g.blob, m.lit("#ffffff"), spots.length]} />
      <instancedMesh ref={(mesh) => canopies(mesh, true)} args={[g.blob, m.lit("#ffffff"), spots.length]} />
    </group>
  );
}

function Pines({ spots }: { spots: TreeSpot[] }) {
  const { g, m } = useKit();
  const dummy = useMemo(() => new Object3D(), []);
  const colors = useMemo(
    () => ({ a: new Color(NATURE.pineA), b: new Color(NATURE.pineB), light: new Color(NATURE.pineLight) }),
    [],
  );

  const trunks: Setup = (mesh) => {
    if (!mesh) return;
    spots.forEach((t, i) => {
      const h = 1.0 + t.size * 0.8;
      dummy.position.set(t.x, t.y + h / 2, t.z);
      dummy.rotation.set(0, 0, 0);
      dummy.scale.set(0.34, h, 0.34);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  };

  const crowns: Setup = (mesh) => {
    if (!mesh) return;
    spots.forEach((t, i) => {
      const h = 1.0 + t.size * 0.8;
      const s = 4.6 + t.size * 3.2; // crown height in metres
      const r = 2.0 + t.size * 1.4;
      dummy.position.set(t.x, t.y + h - 0.3, t.z);
      dummy.rotation.set(0, t.shade * Math.PI * 2, 0);
      dummy.scale.set(r * 2, s, r * 2);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, t.shade < 0.33 ? colors.a : t.shade < 0.66 ? colors.b : colors.light);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  };

  if (spots.length === 0) return null;
  return (
    <group>
      <instancedMesh ref={trunks} args={[g.cylinder, m.lit(NATURE.trunk), spots.length]} />
      <instancedMesh ref={crowns} args={[g.pine, m.lit("#ffffff"), spots.length]} />
    </group>
  );
}

function Palms({ spots }: { spots: TreeSpot[] }) {
  const { g, m } = useKit();
  const dummy = useMemo(() => new Object3D(), []);
  const colors = useMemo(() => ({ a: new Color(NATURE.palmLeaf), b: new Color(NATURE.palmLeafLight) }), []);

  /** Where the trunk top ends up: the trunk leans by up to ~12°. */
  const top = (t: TreeSpot): { x: number; y: number; z: number; lean: number; dir: number } => {
    const h = 4.2 + t.size * 2.4;
    const lean = 0.08 + t.shade * 0.14;
    const dir = t.shade * Math.PI * 2;
    return {
      x: t.x + Math.sin(lean) * h * Math.cos(dir),
      y: t.y + Math.cos(lean) * h,
      z: t.z + Math.sin(lean) * h * Math.sin(dir),
      lean,
      dir,
    };
  };

  const trunks: Setup = (mesh) => {
    if (!mesh) return;
    spots.forEach((t, i) => {
      const h = 4.2 + t.size * 2.4;
      const { lean, dir } = top(t);
      dummy.position.set(t.x, t.y, t.z);
      // Tilt about the axis perpendicular to the lean direction, then stand it up.
      dummy.rotation.set(0, 0, 0);
      dummy.rotateY(-dir);
      dummy.rotateZ(-lean);
      dummy.scale.set(0.34, h, 0.34);
      dummy.translateY(h / 2); // the unit cylinder is centred: lift it onto its foot
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  };

  const crowns: Setup = (mesh) => {
    if (!mesh) return;
    spots.forEach((t, i) => {
      const { x, y, z } = top(t);
      const r = 2.6 + t.size * 1.4;
      dummy.position.set(x, y, z);
      dummy.rotation.set(0, t.shade * 7, 0);
      dummy.scale.set(r, r * 0.9, r);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, t.shade > 0.5 ? colors.b : colors.a);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  };

  if (spots.length === 0) return null;
  return (
    <group>
      <instancedMesh ref={trunks} args={[g.cylinder, m.lit(NATURE.palmTrunk), spots.length]} />
      <instancedMesh ref={crowns} args={[g.palmCrown, m.lit("#ffffff"), spots.length]} />
    </group>
  );
}

function Boulders({ rocks }: { rocks: Boulder[] }) {
  const { g, m } = useKit();
  const dummy = useMemo(() => new Object3D(), []);
  const colors = useMemo(() => ({ a: new Color(NATURE.boulderA), b: new Color(NATURE.boulderB) }), []);

  const setup: Setup = (mesh) => {
    if (!mesh) return;
    rocks.forEach((r, i) => {
      const s = 0.9 + r.size * 2.6;
      dummy.position.set(r.x, r.y + s * 0.28, r.z);
      dummy.rotation.set(r.seed * 0.6, r.seed * Math.PI * 2, r.size * 0.5);
      dummy.scale.set(s, s * 0.7, s * (0.8 + r.seed * 0.4));
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
      mesh.setColorAt(i, r.seed > 0.5 ? colors.a : colors.b);
    });
    mesh.instanceMatrix.needsUpdate = true;
    if (mesh.instanceColor) mesh.instanceColor.needsUpdate = true;
  };

  if (rocks.length === 0) return null;
  return <instancedMesh ref={setup} args={[g.blob, m.lit("#ffffff"), rocks.length]} />;
}

/** Reeds in the marsh: leaning stalks with a brown head, two instanced meshes. */
function Reeds({ reeds }: { reeds: Post[] }) {
  const { g, m } = useKit();
  const dummy = useMemo(() => new Object3D(), []);
  const stalks: Setup = (mesh) => {
    if (!mesh) return;
    reeds.forEach((r, i) => {
      const h = 1.5 + (r.rotation % 1) * 0.9;
      dummy.position.set(r.x, r.y, r.z);
      dummy.rotation.set(0, r.rotation, 0);
      dummy.rotateX(0.12);
      dummy.scale.set(0.12, h, 0.12);
      dummy.translateY(h / 2);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  };
  const heads: Setup = (mesh) => {
    if (!mesh) return;
    reeds.forEach((r, i) => {
      const h = 1.5 + (r.rotation % 1) * 0.9;
      dummy.position.set(r.x, r.y, r.z);
      dummy.rotation.set(0, r.rotation, 0);
      dummy.rotateX(0.12);
      dummy.scale.set(0.2, 0.45, 0.2);
      dummy.translateY(h / 0.45 - 0.3);
      dummy.updateMatrix();
      mesh.setMatrixAt(i, dummy.matrix);
    });
    mesh.instanceMatrix.needsUpdate = true;
  };
  if (reeds.length === 0) return null;
  return (
    <group>
      <instancedMesh ref={stalks} args={[g.slab, m.lit(NATURE.reed), reeds.length]} />
      <instancedMesh ref={heads} args={[g.cylinder, m.lit(NATURE.reedHead), reeds.length]} />
    </group>
  );
}

export function Trees() {
  const { trees, boulders, reeds } = buildIsland().content;
  const byKind = useMemo(
    () => ({
      round: trees.filter((t) => t.kind === "round"),
      pine: trees.filter((t) => t.kind === "pine"),
      palm: trees.filter((t) => t.kind === "palm"),
    }),
    [trees],
  );
  return (
    <group>
      <RoundTrees spots={byKind.round} />
      <Pines spots={byKind.pine} />
      <Palms spots={byKind.palm} />
      <Boulders rocks={boulders} />
      <Reeds reeds={reeds} />
    </group>
  );
}
