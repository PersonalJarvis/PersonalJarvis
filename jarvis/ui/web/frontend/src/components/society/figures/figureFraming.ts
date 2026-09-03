/**
 * How far the card's camera stands back from a figure.
 *
 * A fixed multiple of the figure's height only works while the column it
 * renders into is tall. The look editor grew from four rows to nine, the
 * column became wide and short, and the same distance started cutting the
 * legs off. Solving the frustum for the figure's span in BOTH axes does not
 * care what shape the box is — and it is the same maths for a 1.75 m person
 * and a fox that is longer than it is tall.
 */

/** Air around the figure, so the crown and the feet never touch the edge. */
export const FRAME_MARGIN = 1.2;

export interface FrameInput {
  /** The figure's largest rendered extent, in metres. */
  spanM: number;
  /** Vertical field of view, degrees. */
  fovDeg: number;
  /** Viewport width / height. */
  aspect: number;
  /** The person's wheel zoom; larger means closer. */
  zoom: number;
}

/**
 * Distance in metres. Never returns zero or a negative: a canvas measured
 * before layout reports an aspect of 0, and a camera inside the figure is a
 * black frame nobody can recover from by dragging.
 */
export function frameDistance({ spanM, fovDeg, aspect, zoom }: FrameInput): number {
  const half = Math.max(spanM, 0.01) * 0.5 * FRAME_MARGIN;
  const tan = Math.tan(((Math.min(Math.max(fovDeg, 1), 179) * Math.PI) / 180) / 2);
  const safeAspect = aspect > 0 ? aspect : 1;
  const fitVertically = half / tan;
  const fitHorizontally = half / (tan * safeAspect);
  const distance = Math.max(fitVertically, fitHorizontally);
  return distance / (zoom > 0 ? zoom : 1);
}
