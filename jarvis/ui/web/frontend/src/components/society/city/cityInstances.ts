import { InstancedMesh } from "three";

/** R3F's dispose={null} prop can shadow the instance's native dispose method. */
export function releaseCityInstances(mesh: InstancedMesh): void {
  InstancedMesh.prototype.dispose.call(mesh);
}
