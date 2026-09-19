import { ownsTextOrUi } from "./input";

/** Escape belongs to follow only while its own viewport has keyboard focus. */
export function bindFollowEscape(host: HTMLElement, stop: () => void): () => void {
  const ownsFocus = () => host.contains(document.activeElement) && !ownsTextOrUi(document.activeElement);
  const escape = (event: KeyboardEvent) => {
    if ((event.key !== "Escape" && event.code !== "Escape") || event.defaultPrevented
      || event.altKey || event.ctrlKey || event.metaKey || !ownsFocus()
      || ownsTextOrUi(event.target)) return;
    event.preventDefault();
    stop();
  };
  // Browser Escape may leave fullscreen without dispatching a page keydown.
  // The parent preserves this map while follow relinquishes its own camera.
  const fullscreen = () => {
    if (!document.fullscreenElement && ownsFocus()) stop();
  };
  host.addEventListener("keydown", escape);
  document.addEventListener("fullscreenchange", fullscreen);
  return () => {
    host.removeEventListener("keydown", escape);
    document.removeEventListener("fullscreenchange", fullscreen);
  };
}
