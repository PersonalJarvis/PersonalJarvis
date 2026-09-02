/**
 * Every tree on the island in three draw calls: instanced trunks, instanced
 * lower canopies, instanced upper canopies. Size and shade come from the
 * layout's deterministic spots, so the forest is the same in every window.
 */
import { useMemo } from "react";
import { Color, InstancedMesh, Object3D } from "three";

import { buildIsland } from "./islandLayout";
import { NATURE } from "./worldPalette";
import { useKit } from "./WorldKit";

export function Trees() {
  const { g, m } = useKit();
  const { trees } = buildIsland().content;
  const dummy = useMemo(() => new Object3D(), []);
  const colors = useMemo(
    () => ({ a: new Color(NATURE.canopyA), b: new Color(NATURE.canopyB), light: new Color(NATURE.canopyLight) }),
    [],
  );

  const trunks = (mesh: InstancedMesh | null) => {
    if (!mesh) return;
    trees.forEach((t, i) => {
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
    trees.forEach((t, i) => {
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

  return (
    <group>
      <instancedMesh ref={trunks} args={[g.cylinder, m.lit(NATURE.trunk), trees.length]} />
      <instancedMesh ref={(mesh) => canopies(mesh, false)} args={[g.blob, m.lit("#ffffff"), trees.length]} />
      <instancedMesh ref={(mesh) => canopies(mesh, true)} args={[g.blob, m.lit("#ffffff"), trees.length]} />
    </group>
  );
}
