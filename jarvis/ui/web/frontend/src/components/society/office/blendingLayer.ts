/**
 * drei's <Html occlude="blending"> draws its page BEHIND the canvas: it lifts
 * the canvas above the HTML layer (z-index, absolute) and punches a clear hole
 * where the screen is, so anything in front of the screen still covers it.
 *
 * Every other <Html> (a name label, a hint) resets those canvas styles when it
 * mounts. A label that appears after a live screen therefore drops the canvas
 * back under the screen, and the screen shows through every wall in front of
 * it. While a blending screen is mounted, re-assert the lift each frame.
 */
import { useFrame, useThree } from "@react-three/fiber";

/** The canvas z-index drei gives a blending <Html> with this `zIndexRange`. Pure. */
export function blendingCanvasZ(zIndexRange: readonly [number, number]): string {
  return String(Math.floor(zIndexRange[0] / 2));
}

/** Keep the canvas above the blending screens' HTML layer while the caller is mounted. */
export function useBlendingCanvasLayer(zIndexRange: readonly [number, number]): void {
  const gl = useThree((s) => s.gl);
  const z = blendingCanvasZ(zIndexRange);
  useFrame(() => {
    const style = gl.domElement.style;
    if (style.zIndex === z) return;
    style.zIndex = z;
    style.position = "absolute";
    style.pointerEvents = "none";
  });
}
