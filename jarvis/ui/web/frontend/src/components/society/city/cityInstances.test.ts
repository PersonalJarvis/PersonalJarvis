import { applyProps } from "@react-three/fiber";
import { BoxGeometry, InstancedMesh, MeshBasicMaterial } from "three";
import { expect, it } from "vitest";
import { releaseCityInstances } from "./cityInstances";

it("releases instance buffers even when R3F shadows the native disposer", () => {
  const geometry = new BoxGeometry(), material = new MeshBasicMaterial();
  const mesh = new InstancedMesh(geometry, material, 1);
  let releases = 0; mesh.addEventListener("dispose", () => releases++);
  applyProps(mesh, { dispose: null });
  expect(() => releaseCityInstances(mesh)).not.toThrow();
  expect(releases).toBe(1); geometry.dispose(); material.dispose();
});
