/**
 * The compass beside the zoom: which way the island is being looked at, and
 * the three ways to change it without a mouse drag — a quarter turn each way
 * and a click on the dial for the designed bird's-eye view.
 *
 * The needle is written straight into the DOM from a store subscription: a
 * pointer turn changes the yaw on every move event, and that must never
 * become a React render (`useWorldControls` keeps the whole move path out of
 * React for the same reason). While the pointer turns the island the needle
 * tracks it exactly; a stepped quarter turn glides, matching the camera rig's
 * own ease instead of snapping ahead of the picture.
 */
import { useEffect, useRef } from "react";
import { RotateCcw, RotateCw } from "lucide-react";

import { useT } from "@/i18n";
import { useCameraStore } from "./cameraStore";
import { isDesignedView, northScreenAngle, shortestYawDelta } from "./worldCamera";

export function WorldCompass() {
  const t = useT();
  const dial = useRef<SVGGElement>(null);
  const root = useRef<HTMLDivElement>(null);
  const turnYaw = useCameraStore((s) => s.turnYaw);
  const resetView = useCameraStore((s) => s.resetView);

  useEffect(() => {
    // The angle actually written out: kept unwrapped, so a turn from 350° to
    // 10° eases 20° forward instead of 340° back — the same short way the
    // camera rig takes.
    let shown = northScreenAngle(useCameraStore.getState().yaw);
    const paint = (yaw: number, pitch: number, orbiting: boolean) => {
      shown += shortestYawDelta(shown, northScreenAngle(yaw));
      if (dial.current) {
        // Glide with the rig's ease, except while a drag is turning the island:
        // there the needle must sit exactly under the pointer, not 160 ms behind.
        dial.current.style.transition = orbiting ? "none" : "transform 160ms ease-out";
        dial.current.style.transform = `rotate(${shown.toFixed(1)}deg)`;
      }
      if (root.current) {
        // An empty string would still set the attribute, and the highlight
        // would never go away — the marker has to be deleted instead.
        if (isDesignedView(yaw, pitch)) delete root.current.dataset.turned;
        else root.current.dataset.turned = "yes";
      }
    };
    const s = useCameraStore.getState();
    paint(s.yaw, s.pitch, s.orbiting);
    return useCameraStore.subscribe((next) => paint(next.yaw, next.pitch, next.orbiting));
  }, []);

  return (
    <div ref={root} className="sw-compass" role="group" aria-label={t("society.world.compass_label")}>
      <button
        type="button"
        className="sw-zoom-btn"
        onClick={() => turnYaw(-1)}
        aria-label={t("society.world.turn_left")}
        title={t("society.world.turn_left")}
      >
        <RotateCcw size={14} />
      </button>
      <button
        type="button"
        className="sw-compass-dial"
        onClick={resetView}
        aria-label={t("society.world.view_reset")}
        title={t("society.world.view_reset")}
      >
        {/*
          The viewBox starts at 0,0 on purpose: with a negative origin Chromium
          turns `transform-box: view-box` about the box's corner rather than the
          user-space centre, and the needle orbits the rim instead of spinning.
        */}
        <svg viewBox="0 0 32 32" width="26" height="26" aria-hidden focusable="false">
          <circle cx="16" cy="16" r="14" className="sw-compass-face" />
          <g ref={dial}>
            {/* north half, then the south half in ink */}
            <polygon points="16,5 20,17 12,17" className="sw-compass-north" />
            <polygon points="16,27 20,17 12,17" className="sw-compass-south" />
          </g>
        </svg>
      </button>
      <button
        type="button"
        className="sw-zoom-btn"
        onClick={() => turnYaw(1)}
        aria-label={t("society.world.turn_right")}
        title={t("society.world.turn_right")}
      >
        <RotateCw size={14} />
      </button>
    </div>
  );
}
