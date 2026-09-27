import { expect, it } from "vitest";
import { outpostBuildingAt } from "./buildingSelection";

it("selects the inspected building rather than sending every click to the mast", () => {
  expect(outpostBuildingAt([294, 61, 71])).toBe("operations");
  expect(outpostBuildingAt([351, 63, 69])).toBe("radome-south");
  expect(outpostBuildingAt([278, 65, 46])).toBe("dish-west");
  expect(outpostBuildingAt([320, 80, 46])).toBe("communications-mast");
});

it("does not move the camera when the user clicks a road, open terrace or cliff", () => {
  for (const point of [[294, 58, 75], [285.1, 58, 76.4], [310, 58, 87], [320, 15, 96]] as [number, number, number][]) {
    expect(outpostBuildingAt(point)).toBeNull();
  }
});
