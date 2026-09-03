/**
 * The thin layer of DOM over the canvas: the population chip (top-left),
 * whatever the section hands in for the top-right corner (the World / Ledger
 * switch — app chrome, not world content), the controls hint, the zoom
 * buttons and the minimap (bottom-right).
 */
import type { ReactNode } from "react";
import { Minus, Plus, RotateCcw, ScrollText } from "lucide-react";

import { fill, useT } from "@/i18n";
import type { SocietyAgent } from "../data";
import { useBuildingPoses, useTurnedCount } from "./buildingPoses";
import { useCameraStore } from "./cameraStore";
import { Minimap } from "./Minimap";
import { WorldCompass } from "./WorldCompass";
import { ZOOM_WIDTHS_M } from "./worldCamera";

interface Props {
  agents: SocietyAgent[];
  sample: boolean;
  awake: boolean;
  reducedMotion: boolean;
  topRight?: ReactNode;
  /** The Quest Board button: how many quests are on the board and whether its drawer is open. */
  questsOnBoard?: number;
  questsOpen?: boolean;
  onOpenQuests?: () => void;
}

export function WorldHud({
  agents,
  sample,
  awake,
  reducedMotion,
  topRight,
  questsOnBoard = 0,
  questsOpen = false,
  onOpenQuests,
}: Props) {
  const t = useT();
  const zoom = useCameraStore((s) => s.zoom);
  const zoomStep = useCameraStore((s) => s.zoomStep);
  const turned = useTurnedCount();
  const resetAll = useBuildingPoses((s) => s.resetAll);
  const active = agents.filter((a) => a.state === "working").length;
  // The main locale file may override these keys with the `{0}` placeholder
  // convention the society rail uses; accept both spellings.
  const count = (key: string, n: number) => fill(t(key), { count: n }).replace("{0}", String(n));
  return (
    <div className="sw-hud" aria-live="off">
      <div className="sw-hud-top">
        <div className="sw-chip-row">
          <div className="sw-chip">
            <strong>{count("society.world.hud_agents", agents.length)}</strong>
            <span className="sw-chip-sep" />
            <span>{count("society.world.hud_active", active)}</span>
          </div>
          {onOpenQuests && (
            <button
              type="button"
              className="sw-chip sw-chip-btn"
              onClick={onOpenQuests}
              aria-pressed={questsOpen}
              title={t("society.world.drawer_quests_hint")}
            >
              <ScrollText size={14} aria-hidden />
              <strong>{t("society.world.hud_quests")}</strong>
              {questsOnBoard > 0 && (
                <>
                  <span className="sw-chip-sep" />
                  <span>{questsOnBoard}</span>
                </>
              )}
            </button>
          )}
          {sample && <div className="sw-chip sw-chip-note">{t("society.world.sample_badge")}</div>}
          {reducedMotion && <div className="sw-chip sw-chip-note">{t("society.world.reduced_motion_note")}</div>}
        </div>
        {topRight ? <div className="sw-hud-topright">{topRight}</div> : null}
      </div>
      <div className="sw-hud-bottom">
        <div className="sw-hint-row">
          <div className="sw-hint">{t("society.world.controls_hint")}</div>
          {turned > 0 && (
            <button
              type="button"
              className="sw-hint sw-hint-btn"
              onClick={resetAll}
              title={t("society.world.rotate_reset_all")}
            >
              <RotateCcw size={12} aria-hidden />
              {count("society.world.rotate_reset_all_count", turned)}
            </button>
          )}
        </div>
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
          <WorldCompass />
          <Minimap awake={awake} />
        </div>
      </div>
    </div>
  );
}
