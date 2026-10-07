import { useEffect } from "react";
import { useThree } from "@react-three/fiber";
import { startOfficeFrameBudget } from "./officeFrameBudget";

/** The narrow IDE companion animates at 30 fps; the full view stays unrestricted. */
export function OfficeFrameDriver({ enabled }: { enabled: boolean }) {
  const invalidate = useThree((state) => state.invalidate);
  // Dev-only handle for runtime checks of the draw-call and memory budget (renderer.info).
  const gl = useThree((state) => state.gl);
  useEffect(() => {
    if (import.meta.env.DEV && typeof window !== "undefined") Object.assign(window, { __officeGl: gl });
  }, [gl]);
  useEffect(() => {
    if (!enabled) return;
    return startOfficeFrameBudget(invalidate, 30);
  }, [enabled, invalidate]);
  return null;
}
