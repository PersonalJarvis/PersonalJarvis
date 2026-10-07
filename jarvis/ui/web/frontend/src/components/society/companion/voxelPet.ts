/**
 * A pet the person drew themselves has no authored model, only its sprite
 * sheet. This turns the idle row into voxel frames: every opaque pixel
 * (alpha >= 128, the same rule the desktop overlay uses) becomes one cube.
 * Pure, so it is tested without a canvas.
 */

export interface Voxel {
  /** Column from the frame's left edge, row from its bottom edge. */
  x: number;
  y: number;
  /** 0xRRGGBB. */
  color: number;
}

export interface VoxelFrames {
  frames: Voxel[][];
  /** Shared bounds over all frames, so the figure never jumps between frames. */
  minX: number;
  maxX: number;
  minY: number;
  maxY: number;
}

/** More cubes than this in one frame and the sheet is treated as unusable. */
export const MAX_VOXELS_PER_FRAME = 4096;

export function voxelFrames(
  data: ArrayLike<number>, sheetWidth: number, sheetHeight: number,
  frameSize: number, row: number, frames: number,
): VoxelFrames | null {
  if (!(frameSize > 0) || row < 0 || frames < 1) return null;
  const top = row * frameSize;
  if (top + frameSize > sheetHeight) return null;
  const out: Voxel[][] = [];
  let minX = Infinity, maxX = -Infinity, minY = Infinity, maxY = -Infinity;
  for (let f = 0; f < frames; f++) {
    const left = f * frameSize;
    if (left + frameSize > sheetWidth) break;
    const voxels: Voxel[] = [];
    for (let py = 0; py < frameSize; py++) {
      for (let px = 0; px < frameSize; px++) {
        const i = ((top + py) * sheetWidth + left + px) * 4;
        if (data[i + 3] < 128) continue;
        const y = frameSize - 1 - py;
        voxels.push({ x: px, y, color: (data[i] << 16) | (data[i + 1] << 8) | data[i + 2] });
        if (px < minX) minX = px;
        if (px > maxX) maxX = px;
        if (y < minY) minY = y;
        if (y > maxY) maxY = y;
      }
    }
    if (voxels.length > MAX_VOXELS_PER_FRAME) return null;
    out.push(voxels);
  }
  if (out.length === 0 || !Number.isFinite(minX)) return null;
  return { frames: out, minX, maxX, minY, maxY };
}
