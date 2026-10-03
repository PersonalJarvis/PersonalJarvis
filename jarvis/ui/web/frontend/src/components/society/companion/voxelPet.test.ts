import { describe, expect, it } from "vitest";
import { MAX_VOXELS_PER_FRAME, voxelFrames } from "./voxelPet";

/** A sheet of `frames` cells of `size` px per row, with the given opaque pixels (frame, x, y from the top). */
function sheet(size: number, frames: number, rows: number, opaque: [number, number, number][]) {
  const width = size * frames, height = size * rows;
  const data = new Uint8ClampedArray(width * height * 4);
  for (const [f, x, y] of opaque) data.set([0x12, 0x34, 0x56, 255], (y * width + f * size + x) * 4);
  return { data, width, height };
}

describe("voxelFrames", () => {
  it("makes one cube per opaque pixel, counted from the bottom edge", () => {
    const { data, width, height } = sheet(4, 2, 1, [[0, 1, 3], [0, 2, 0], [1, 0, 3]]);
    const result = voxelFrames(data, width, height, 4, 0, 2)!;
    expect(result.frames).toHaveLength(2);
    expect(result.frames[0]).toEqual([{ x: 2, y: 3, color: 0x123456 }, { x: 1, y: 0, color: 0x123456 }]);
    expect(result.frames[1]).toEqual([{ x: 0, y: 0, color: 0x123456 }]);
    expect(result).toMatchObject({ minX: 0, maxX: 2, minY: 0, maxY: 3 });
  });

  it("ignores half-transparent pixels like the desktop overlay does", () => {
    const { data, width, height } = sheet(4, 1, 1, [[0, 1, 1]]);
    data[(1 * width + 1) * 4 + 3] = 127;
    expect(voxelFrames(data, width, height, 4, 0, 1)).toBeNull();
  });

  it("refuses rows outside the sheet and oversized frames", () => {
    const small = sheet(4, 1, 1, [[0, 0, 0]]);
    expect(voxelFrames(small.data, small.width, small.height, 4, 1, 1)).toBeNull();
    const size = 66;
    const every: [number, number, number][] = [];
    for (let i = 0; i < size * size; i++) every.push([0, i % size, Math.floor(i / size)]);
    const full = sheet(size, 1, 1, every);
    expect(size * size).toBeGreaterThan(MAX_VOXELS_PER_FRAME);
    expect(voxelFrames(full.data, full.width, full.height, size, 0, 1)).toBeNull();
  });
});
