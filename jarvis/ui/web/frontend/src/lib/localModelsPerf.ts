/**
 * User Timing marks for the Local models section, so "instant paint" is a
 * number in DevTools (Performance → Timings) rather than an impression:
 * `local-models:mount` when the view mounts, `local-models:first-data` the
 * first time the overview has data on screen (snapshot or live).
 */
export const LOCAL_MODELS_MARK_MOUNT = "local-models:mount";
export const LOCAL_MODELS_MARK_FIRST_DATA = "local-models:first-data";

/** `performance.mark` guarded for environments without User Timing. */
export function markLocalModels(name: string): void {
  try {
    if (typeof performance !== "undefined" && typeof performance.mark === "function")
      performance.mark(name);
  } catch {
    // A mark is a measurement aid; never let it break the paint it measures.
  }
}
