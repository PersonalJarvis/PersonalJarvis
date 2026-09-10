import { useSyncExternalStore } from "react";

const QUERY = "(prefers-reduced-motion: reduce)";
function snapshot(): boolean {
  return typeof window !== "undefined" && typeof window.matchMedia === "function" && window.matchMedia(QUERY).matches;
}
function subscribe(listener: () => void): () => void {
  if (typeof window.matchMedia !== "function") return () => undefined;
  const media = window.matchMedia(QUERY);
  media.addEventListener("change", listener);
  return () => media.removeEventListener("change", listener);
}
/** The installed motion hook snapshots only at mount; city travel must react live. */
export function useCityReducedMotion(): boolean {
  return useSyncExternalStore(subscribe, snapshot, () => false);
}
