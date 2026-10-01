import { useEffect } from "react";
import { useThree } from "@react-three/fiber";
import { startOfficeFrameBudget } from "./officeFrameBudget";

/** The narrow IDE companion animates at 30 fps; the full view stays unrestricted. */
export function OfficeFrameDriver({ enabled }: { enabled: boolean }) {
  const invalidate = useThree((state) => state.invalidate);
  useEffect(() => {
    if (!enabled) return;
    return startOfficeFrameBudget(invalidate, 30);
  }, [enabled, invalidate]);
  return null;
}
