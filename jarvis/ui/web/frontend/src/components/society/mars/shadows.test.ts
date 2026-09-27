import { expect, it } from "vitest";
import { OrthographicCamera, Vector3 } from "three";
import { boundsCorners } from "./camera";
import { outpostBounds } from "./world";
import { fitOutpostShadow, OUTPOST_SHADOW, SUN_POSITION, SUN_TARGET } from "./shadows";

it("keeps every authored bridge, cliff and tower corner inside the shadow camera", () => {
  const { left, right, top, bottom, near, far } = OUTPOST_SHADOW;
  const camera = new OrthographicCamera(left, right, top, bottom, near, far);
  camera.position.fromArray(SUN_POSITION); camera.lookAt(...SUN_TARGET); camera.updateMatrixWorld();
  for (const point of boundsCorners(outpostBounds())) {
    const projected = new Vector3(...point).project(camera);
    for (const coordinate of projected.toArray()) expect(Math.abs(coordinate)).toBeLessThan(1);
  }
});

it("retains a small physical depth offset when the reference bounds change", () => {
  const bounds = outpostBounds();
  const wider = fitOutpostShadow({ min: [bounds.min[0] - 20, bounds.min[1], bounds.min[2]], max: bounds.max });
  expect(wider.far - wider.near).not.toBe(OUTPOST_SHADOW.far - OUTPOST_SHADOW.near);
  for (const shadow of [OUTPOST_SHADOW, wider]) {
    const physicalOffset = -shadow.bias * (shadow.far - shadow.near);
    expect(physicalOffset).toBeGreaterThan(0);
    expect(physicalOffset).toBeLessThan(0.2);
    expect(physicalOffset).toBeCloseTo(0.18);
  }
});
