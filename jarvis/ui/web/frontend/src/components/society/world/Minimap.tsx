/**
 * The island at a glance: a 2D canvas painted once from the tile map — every
 * kind in its colour, the high ground lighter and the low ground deeper so the
 * relief reads — with the viewport rectangle and the walkers' pins redrawn a
 * few times a second. Clicking it moves the camera there.
 */
import { useEffect, useMemo, useRef } from "react";

import { useT } from "@/i18n";
import { useCameraStore } from "./cameraStore";
import { ISLAND_HALF_M, PLATEAU_LEVEL, TILE_M, TileKind, buildIsland } from "./islandLayout";
import { walkerPins } from "./walkerRegistry";
import { ZOOM_WIDTHS_M, visibleGroundCorners } from "./worldCamera";
import { LABEL, WATER, minimapColor, shadeHex } from "./worldPalette";

const SIZE_PX = 168;
const REDRAW_MS = 120;

function paintBase(): HTMLCanvasElement | null {
  if (typeof document === "undefined") return null;
  const { map } = buildIsland();
  const canvas = document.createElement("canvas");
  canvas.width = map.size;
  canvas.height = map.size;
  const ctx = canvas.getContext("2d");
  if (!ctx) return null;
  for (let tz = 0; tz < map.size; tz++) {
    for (let tx = 0; tx < map.size; tx++) {
      const i = tz * map.size + tx;
      const kind = map.kind[i] as TileKind;
      if (kind === TileKind.water) {
        ctx.fillStyle = WATER.deep;
      } else {
        ctx.fillStyle = shadeHex(minimapColor(kind), 1 + (map.level[i] - PLATEAU_LEVEL) * 0.06);
      }
      ctx.fillRect(tx, tz, 1, 1);
    }
  }
  return canvas;
}

function worldToMap(x: number, z: number): [number, number] {
  const s = SIZE_PX / (ISLAND_HALF_M * 2);
  return [(x + ISLAND_HALF_M) * s, (z + ISLAND_HALF_M) * s];
}

export function Minimap({ awake }: { awake: boolean }) {
  const t = useT();
  const ref = useRef<HTMLCanvasElement>(null);
  const base = useMemo(paintBase, []);

  useEffect(() => {
    const canvas = ref.current;
    if (!canvas || !base || !awake) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    let frame = 0;
    let last = 0;
    const draw = (now: number) => {
      frame = requestAnimationFrame(draw);
      if (now - last < REDRAW_MS) return;
      last = now;
      ctx.imageSmoothingEnabled = false;
      ctx.clearRect(0, 0, SIZE_PX, SIZE_PX);
      ctx.drawImage(base, 0, 0, SIZE_PX, SIZE_PX);
      // Walker pins.
      for (const pin of walkerPins().values()) {
        const [px, pz] = worldToMap(pin.x, pin.z);
        ctx.fillStyle = LABEL.ink;
        ctx.fillRect(Math.round(px) - 2, Math.round(pz) - 2, 5, 5);
        ctx.fillStyle = pin.color;
        ctx.fillRect(Math.round(px) - 1, Math.round(pz) - 1, 3, 3);
      }
      // The viewport as a rotated rectangle.
      const { target, zoom, aspect } = useCameraStore.getState();
      const corners = visibleGroundCorners(target, ZOOM_WIDTHS_M[zoom], aspect);
      ctx.beginPath();
      corners.forEach(([x, z], i) => {
        const [px, pz] = worldToMap(x, z);
        if (i === 0) ctx.moveTo(px, pz);
        else ctx.lineTo(px, pz);
      });
      ctx.closePath();
      ctx.lineWidth = 2;
      ctx.strokeStyle = LABEL.ink;
      ctx.stroke();
      ctx.lineWidth = 1;
      ctx.strokeStyle = "#ffffff";
      ctx.stroke();
    };
    frame = requestAnimationFrame(draw);
    return () => cancelAnimationFrame(frame);
  }, [base, awake]);

  const onClick = (e: React.MouseEvent<HTMLCanvasElement>) => {
    const rect = e.currentTarget.getBoundingClientRect();
    const fx = (e.clientX - rect.left) / rect.width;
    const fz = (e.clientY - rect.top) / rect.height;
    const x = (fx * 2 - 1) * ISLAND_HALF_M;
    const z = (fz * 2 - 1) * ISLAND_HALF_M;
    // Snap to a tile centre so the ease lands on a clean pixel.
    useCameraStore.getState().jumpTo(Math.round(x / TILE_M) * TILE_M, Math.round(z / TILE_M) * TILE_M);
  };

  return (
    <canvas
      ref={ref}
      width={SIZE_PX}
      height={SIZE_PX}
      className="sw-minimap"
      role="img"
      aria-label={t("society.world.minimap_label")}
      onClick={onClick}
    />
  );
}
