/**
 * The thin layer of DOM over the canvas: the population chip (top-left),
 * whatever the section hands in for the top-right corner (the World / Ledger
 * switch — app chrome, not world content), the controls hint, the zoom
 * buttons and the minimap (bottom-right).
 */
import type { ReactNode } from "react";
import { Minus, Plus } from "lucide-react";

import { fill, useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { useCameraStore } from "./cameraStore";
import { Minimap } from "./Minimap";
import { ZOOM_WIDTHS_M } from "./worldCamera";

interface Props {
  agents: SocietyAgent[];
  sample: boolean;
  awake: boolean;
  reducedMotion: boolean;
  topRight?: ReactNode;
}

export function WorldHud({ agents, sample, awake, reducedMotion, topRight }: Props) {
  const t = useT();
  const zoom = useCameraStore((s) => s.zoom);
  const zoomStep = useCameraStore((s) => s.zoomStep);
  const active = agents.filter((a) => a.state === "working").length;
  return (
    <div className="sw-hud" aria-live="off">
      <div className="sw-hud-top">
        <div className="sw-chip-row">
          <div className="sw-chip">
            <strong>{fill(t("society.world.hud_agents"), { count: agents.length })}</strong>
            <span className="sw-chip-sep" />
            <span>{fill(t("society.world.hud_active"), { count: active })}</span>
          </div>
          {sample && <div className="sw-chip sw-chip-note">{t("society.world.sample_badge")}</div>}
          {reducedMotion && <div className="sw-chip sw-chip-note">{t("society.world.reduced_motion_note")}</div>}
        </div>
        {topRight ? <div className="sw-hud-topright">{topRight}</div> : null}
      </div>
      <div className="sw-hud-bottom">
        <div className="sw-hint">{t("society.world.controls_hint")}</div>
        <div className="sw-corner">
          <div className="sw-zoom" role="group" aria-label={t("society.world.zoom_label")}>
            <button
              type="button"
              className="sw-zoom-btn"
              onClick={() => zoomStep(-1)}
              disabled={zoom === 0}
              aria-label={t("society.world.zoom_in")}
              title={t("society.world.zoom_in")}
            >
              <Plus size={14} />
            </button>
            <span className="sw-zoom-level">{ZOOM_WIDTHS_M[zoom]} m</span>
            <button
              type="button"
              className="sw-zoom-btn"
              onClick={() => zoomStep(1)}
              disabled={zoom === ZOOM_WIDTHS_M.length - 1}
              aria-label={t("society.world.zoom_out")}
              title={t("society.world.zoom_out")}
            >
              <Minus size={14} />
            </button>
          </div>
          <Minimap awake={awake} />
        </div>
      </div>
    </div>
  );
}
