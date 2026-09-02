/**
 * Pointer and keyboard navigation over the island: drag to pan, wheel to step
 * the zoom, arrow keys / WASD to glide. Plain DOM listeners on the stage
 * host — never through React state on the move path, and never through R3F's
 * pointer events (those belong to the figures).
 */
import { useEffect, type RefObject } from "react";

import { useCameraStore } from "./cameraStore";
import { ZOOM_WIDTHS_M, dragToPan } from "./worldCamera";

/** Pointer travel below this is a click, not a drag. */
const DRAG_THRESHOLD_PX = 4;
/** One zoom step per wheel gesture, not per tick. */
const WHEEL_COOLDOWN_MS = 160;

const PAN_KEYS = new Set(["ArrowUp", "ArrowDown", "ArrowLeft", "ArrowRight", "KeyW", "KeyA", "KeyS", "KeyD"]);

export function useWorldControls(hostRef: RefObject<HTMLElement | null>, enabled: boolean): void {
  useEffect(() => {
    const host = hostRef.current;
    if (!host || !enabled) return;
    const store = useCameraStore;

    let pointerId: number | null = null;
    let lastX = 0;
    let lastY = 0;
    let travelled = 0;
    let lastWheel = 0;

    /*
     * A held left button over the island pans it, so nothing may start a text
     * selection while that button is down. The signs over the houses and the
     * HUD are DOM drawn on top of the canvas: as the pointer travels the
     * browser paints them blue, and — worse — a selection that starts under a
     * captured pointer makes Chromium fire `pointercancel`, which ends the pan
     * halfway through the drag. `user-select: none` on the stage does not
     * settle it either, because the selection can begin over the HUD, which
     * sits outside the stage and stays readable by design.
     *
     * Blocking `selectstart` for exactly as long as the button is held costs
     * nothing (there is nothing to read on the map) and leaves every other
     * surface of the app selectable.
     */
    const blockSelectStart = (e: Event) => e.preventDefault();
    const allowSelectStart = () => document.removeEventListener("selectstart", blockSelectStart);

    const onPointerDown = (e: PointerEvent) => {
      if (e.button !== 0) return;
      pointerId = e.pointerId;
      lastX = e.clientX;
      lastY = e.clientY;
      travelled = 0;
      document.addEventListener("selectstart", blockSelectStart);
      host.focus({ preventScroll: true });
    };

    const onPointerMove = (e: PointerEvent) => {
      if (pointerId !== e.pointerId) return;
      const dx = e.clientX - lastX;
      const dy = e.clientY - lastY;
      lastX = e.clientX;
      lastY = e.clientY;
      travelled += Math.abs(dx) + Math.abs(dy);
      if (travelled < DRAG_THRESHOLD_PX) return;
      if (!store.getState().dragging) {
        store.getState().setDragging(true);
        try {
          host.setPointerCapture(e.pointerId);
        } catch {
          /* a synthetic pointer without capture support still pans */
        }
      }
      const width = ZOOM_WIDTHS_M[store.getState().zoom];
      const [px, pz] = dragToPan(dx, dy, width, host.clientWidth || 1);
      store.getState().panBy(px, pz);
    };

    const endDrag = (e: PointerEvent) => {
      if (pointerId !== e.pointerId) return;
      pointerId = null;
      allowSelectStart();
      if (store.getState().dragging) {
        // Let the click that ends a drag pass first, then re-enable figure clicks.
        window.setTimeout(() => store.getState().setDragging(false), 0);
      }
    };

    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      const now = performance.now();
      if (now - lastWheel < WHEEL_COOLDOWN_MS) return;
      lastWheel = now;
      store.getState().zoomStep(e.deltaY > 0 ? 1 : -1);
    };

    const onKeyDown = (e: KeyboardEvent) => {
      if (PAN_KEYS.has(e.code)) {
        e.preventDefault();
        store.getState().keyDown(e.code);
      } else if (e.code === "Equal" || e.code === "NumpadAdd") {
        store.getState().zoomStep(-1);
      } else if (e.code === "Minus" || e.code === "NumpadSubtract") {
        store.getState().zoomStep(1);
      }
    };
    const onKeyUp = (e: KeyboardEvent) => {
      if (PAN_KEYS.has(e.code)) store.getState().keyUp(e.code);
    };
    const onBlur = () => store.getState().clearKeys();

    host.addEventListener("pointerdown", onPointerDown);
    host.addEventListener("pointermove", onPointerMove);
    host.addEventListener("pointerup", endDrag);
    host.addEventListener("pointercancel", endDrag);
    host.addEventListener("wheel", onWheel, { passive: false });
    host.addEventListener("keydown", onKeyDown);
    host.addEventListener("keyup", onKeyUp);
    host.addEventListener("blur", onBlur);
    return () => {
      host.removeEventListener("pointerdown", onPointerDown);
      host.removeEventListener("pointermove", onPointerMove);
      host.removeEventListener("pointerup", endDrag);
      host.removeEventListener("pointercancel", endDrag);
      host.removeEventListener("wheel", onWheel);
      host.removeEventListener("keydown", onKeyDown);
      host.removeEventListener("keyup", onKeyUp);
      host.removeEventListener("blur", onBlur);
      allowSelectStart();
      store.getState().clearKeys();
      store.getState().setDragging(false);
    };
  }, [hostRef, enabled]);
}
