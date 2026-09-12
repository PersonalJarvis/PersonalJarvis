import { inDesktopShell } from "./nativeDrop";

let pending = Promise.resolve();
let requested = false;

/** Serialize desktop transitions so a quick return cannot leave the window fullscreen. */
export function setMapFullscreen(enabled: boolean): Promise<void> {
  requested = enabled;
  if (inDesktopShell()) {
    // A previous caller already reports its failure; allow the next transition.
    const next = pending.catch(() => undefined).then(async () => {
      const response = await fetch("/api/window/fullscreen", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ enabled }),
      });
      if (!response.ok || !(await response.json()).ok) throw new Error("Desktop fullscreen unavailable");
    });
    pending = next;
    return next;
  }
  // Browser entry must stay synchronous with the user's click for activation.
  if (enabled && !document.fullscreenElement) {
    const enter = document.documentElement.requestFullscreen?.();
    if (!enter) return Promise.reject(new Error("Fullscreen unavailable"));
    return enter.then(async () => {
      if (!requested && document.fullscreenElement) await document.exitFullscreen();
    });
  }
  if (!enabled && document.fullscreenElement) return document.exitFullscreen();
  return Promise.resolve();
}
