/**
 * Pointer and keyboard navigation over the island: drag to pan, drag with the
 * RIGHT (or middle) button to orbit, wheel to step the zoom, arrow keys / WASD
 * to glide, Q / E for a quarter turn and R for the designed view. Plain DOM
 * listeners on the stage host — never through React state on the move path,
 * and never through R3F's pointer events (those belong to the figures).
 *
 * Why the right button for the turn: the left one is spoken for three times
 * over (pan, pick a figure, drag a building's rotate handle), so a turn on it
 * would need a modifier no one discovers. The right button is free — the stage
 * has no context menu of its own — and it is what every city builder uses.
 */
import { useEffect, type RefObject } from "react";

import { useCameraStore } from "./cameraStore";
import { viewAngles } from "./viewAngles";
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
    /** What the held button does: pan the ground, or orbit around it. */
    let mode: "pan" | "orbit" = "pan";
    let lastX = 0;
    let lastY = 0;
    let travelled = 0;
    let orbitTravel = 0;
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
      // 0 = left (pan), 2 = right and 1 = middle (orbit). Anything else is
      // someone's fourth mouse button and belongs to the browser.
      if (e.button !== 0 && e.button !== 1 && e.button !== 2) return;
      mode = e.button === 0 ? "pan" : "orbit";
      // The middle button would otherwise start Chromium's autoscroll.
      if (e.button === 1) e.preventDefault();
      pointerId = e.pointerId;
      lastX = e.clientX;
      lastY = e.clientY;
      travelled = 0;
      orbitTravel = 0;
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
        // `dragging` stands every click guard down, whichever button it is:
        // a turn must not select a figure either.
        store.getState().setDragging(true);
        if (mode === "orbit") {
          store.getState().setOrbiting(true);
          host.dataset.orbiting = "yes"; // the stage swaps its grab cursor
        }
        try {
          host.setPointerCapture(e.pointerId);
        } catch {
          /* a synthetic pointer without capture support still pans */
        }
      }
      if (mode === "orbit") {
        orbitTravel += dx;
        return;
      }
      // Pan along the ground axes of the picture on screen, not the designed ones.
      const width = ZOOM_WIDTHS_M[store.getState().zoom];
      const { yaw, pitch } = viewAngles();
      const [px, pz] = dragToPan(dx, dy, width, host.clientWidth || 1, pitch, yaw);
      store.getState().panBy(px, pz);
    };

    const endDrag = (e: PointerEvent) => {
      if (pointerId !== e.pointerId) return;
      pointerId = null;
      if (mode === "orbit" && Math.abs(orbitTravel) >= 24) store.getState().turnYaw(orbitTravel > 0 ? 1 : -1);
      allowSelectStart();
      delete host.dataset.orbiting;
      if (store.getState().orbiting) store.getState().setOrbiting(false);
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
      } else if (e.code === "KeyQ") {
        store.getState().turnYaw(-1);
      } else if (e.code === "KeyE") {
        store.getState().turnYaw(1);
      } else if (e.code === "KeyR") {
        store.getState().resetView();
      }
    };

    // The stage has no context menu of its own, and the right button turns it.
    const onContextMenu = (e: MouseEvent) => e.preventDefault();
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
    host.addEventListener("contextmenu", onContextMenu);
    return () => {
      host.removeEventListener("pointerdown", onPointerDown);
      host.removeEventListener("pointermove", onPointerMove);
      host.removeEventListener("pointerup", endDrag);
      host.removeEventListener("pointercancel", endDrag);
      host.removeEventListener("wheel", onWheel);
      host.removeEventListener("keydown", onKeyDown);
      host.removeEventListener("keyup", onKeyUp);
      host.removeEventListener("blur", onBlur);
      host.removeEventListener("contextmenu", onContextMenu);
      allowSelectStart();
      delete host.dataset.orbiting;
      store.getState().clearKeys();
      store.getState().setDragging(false);
      store.getState().setOrbiting(false);
    };
  }, [hostRef, enabled]);
}
