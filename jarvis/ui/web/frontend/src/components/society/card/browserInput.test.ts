import { describe, expect, test, vi } from "vitest";
import { browserPoint } from "./browserInput";

describe("full browser window input", () => {
  test("scales the displayed frame and excludes the side gutters", () => {
    const canvas = document.createElement("canvas");
    canvas.width = 1600; canvas.height = 1000;
    canvas.dataset.browserGeometryId = "with-profile-popup";
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 20, top: 40, width: 2000, height: 1000 } as DOMRect);
    expect(browserPoint(canvas, 20, 50)).toBeNull();
    expect(browserPoint(canvas, 1700, 110)).toEqual({ x: 1480, y: 70, geometry_id: "with-profile-popup" });
    expect(browserPoint(canvas, 1820, 1040)).toBeNull();
  });
  test("uses the captured pixel size under display scaling", () => {
    const canvas = document.createElement("canvas");
    canvas.width = 2400; canvas.height = 1500;
    vi.spyOn(canvas, "getBoundingClientRect").mockReturnValue({ left: 0, top: 0, width: 1200, height: 750 } as DOMRect);
    expect(browserPoint(canvas, 1150, 60)).toEqual({ x: 2300, y: 120 });
  });
});
