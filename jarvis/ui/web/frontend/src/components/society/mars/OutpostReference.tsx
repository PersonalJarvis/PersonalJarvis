import { useEffect, useState } from "react";
import { useThree, type ThreeEvent } from "@react-three/fiber";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import { Material, Mesh, Texture, type Group } from "three";
import outpostUrl from "../../../../../../../../art/studies/mars-outpost-reference/exports/communications-outpost.glb";

function dispose(root: Group): void {
  const materials = new Set<Material>(), textures = new Set<Texture>();
  root.traverse((object) => {
    if (!(object instanceof Mesh)) return;
    object.geometry.dispose();
    for (const material of Array.isArray(object.material) ? object.material : [object.material]) {
      materials.add(material);
      for (const value of Object.values(material)) if (value instanceof Texture) textures.add(value);
    }
  });
  for (const texture of textures) texture.dispose();
  for (const material of materials) material.dispose();
}

/** Review asset loaded in the actual renderer, outside the production asset catalog. */
export function OutpostReference({ onSelect, onReady }: {
  onSelect: (id: string) => void; onReady: (ready: boolean) => void;
}) {
  const [scene, setScene] = useState<Group | null>(null);
  const [failed, setFailed] = useState(false);
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => {
    const controller = new AbortController();
    let owned: Group | null = null;
    let retired = false;
    setFailed(false);
    void fetch(outpostUrl, { signal: controller.signal })
      .then((response) => {
        if (!response.ok) throw new Error("outpost_asset_unavailable");
        return response.arrayBuffer();
      })
      .then((buffer) => new GLTFLoader().parseAsync(buffer, ""))
      .then((asset) => {
        if (retired) { dispose(asset.scene); return; }
        owned = asset.scene;
        owned.traverse((object) => {
          if (object instanceof Mesh) { object.castShadow = true; object.receiveShadow = true; }
        });
        setScene(owned); onReady(true); invalidate();
      })
      .catch((error: unknown) => {
        if (retired) return;
        console.warn("Outpost reference could not load", error instanceof Error ? error.name : "Error");
        setFailed(true); onReady(false); invalidate();
      });
    return () => {
      retired = true; controller.abort();
      if (owned) dispose(owned);
    };
  }, [invalidate, onReady]);
  if (!scene || failed) return null;
  const select = (event: ThreeEvent<MouseEvent>) => {
    if (event.delta > 4) return;
    event.stopPropagation();
    const point = event.point;
    onSelect(point.x >= 283 && point.x <= 305 && point.z >= 56 && point.z <= 72
      ? "operations" : "communications-mast");
  };
  return <primitive object={scene} position={[320, 58, 50]} onClick={select} dispose={null} />;
}
