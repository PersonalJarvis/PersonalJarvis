import { useEffect, useState } from "react";

/**
 * The backend's platform name (`darwin`, `win32`, `linux`), read once from
 * the permissions probe. `null` while loading or when the probe fails — the
 * callers then leave out anything platform-only.
 */
export function usePlatform(): string | null {
  const [platform, setPlatform] = useState<string | null>(null);
  useEffect(() => {
    let cancelled = false;
    void (async () => {
      try {
        const res = await fetch("/api/permissions/status");
        if (!res.ok) return;
        const data = (await res.json()) as { platform?: string };
        if (!cancelled && typeof data.platform === "string") setPlatform(data.platform);
      } catch {
        // Best-effort: without the probe there is simply no platform-only step.
      }
    })();
    return () => {
      cancelled = true;
    };
  }, []);
  return platform;
}
