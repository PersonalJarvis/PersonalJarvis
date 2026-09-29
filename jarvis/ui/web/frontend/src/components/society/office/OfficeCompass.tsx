/**
 * The compass bar, game style: a strip at the top centre of the stage that
 * shows where the camera faces (N/NE/E/… letters, a tick every 15°, a centre
 * marker and the heading in degrees), plus small pips for the selected agent
 * and the checkpoints at their bearing from the person's character.
 *
 * Drawn on a canvas at ≤ 20 Hz from the per-frame module state (`cameraView`,
 * `player`, `agentPositions`); colours come from theme tokens, so light and
 * dark mode both work. Pauses while the document is hidden.
 */
import { useCallback, useEffect, useRef } from "react";
import { useT } from "@/i18n";
import type { OfficeLayout } from "./officeLayout";
import { cameraView, player } from "./officeStore";
import { agentPositions } from "./walkerRegistry";
import { MAP_PAINT, bearingOf, compassPosition, compassTicks, relativeBearing, yawToBearing, type CompassPoint } from "./minimap";
import { agentColour, context2d, tokenColour, useElementSize, usePixelRatio, useThemeVersion, useThrottledFrames, type OfficeMapAgent } from "./mapHooks";

export interface OfficeCompassProps {
  layout: OfficeLayout;
  agents: ReadonlyMap<string, OfficeMapAgent>;
  selectedId: string | null;
}

/** Degrees visible across the bar. */
const SPAN_DEG = 150;
const FRAME_MS = 50;

interface CompassColours { ink: string; muted: string; tick: string; marker: string; rim: string }

export function OfficeCompass({ layout, agents, selectedId }: OfficeCompassProps) {
  const t = useT();
  const rootRef = useRef<HTMLDivElement>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);
  const readoutRef = useRef<HTMLSpanElement>(null);
  const coloursRef = useRef<CompassColours | null>(null);
  const size = useElementSize(canvasRef);
  const dpr = usePixelRatio();
  const themeVersion = useThemeVersion();

  const letters: Record<CompassPoint, string> = {
    n: t("society.office.compass_n"), ne: t("society.office.compass_ne"), e: t("society.office.compass_e"),
    se: t("society.office.compass_se"), s: t("society.office.compass_s"), sw: t("society.office.compass_sw"),
    w: t("society.office.compass_w"), nw: t("society.office.compass_nw"),
  };

  const live = useRef({ layout, agents, selectedId, size, letters });
  live.current = { layout, agents, selectedId, size, letters };

  useEffect(() => {
    const root = rootRef.current;
    const style = root ? getComputedStyle(root) : null;
    coloursRef.current = {
      ink: tokenColour(style, "--popover-foreground", "#111827"),
      muted: tokenColour(style, "--muted-foreground", "#6b7280"),
      tick: tokenColour(style, "--muted-foreground", "#6b7280", 0.7),
      marker: MAP_PAINT.checkpoint,
      rim: tokenColour(style, "--popover", "#ffffff"),
    };
  }, [themeVersion]);

  const draw = useCallback(() => {
    const canvas = canvasRef.current;
    const ctx = context2d(canvas);
    const colours = coloursRef.current;
    const { layout: lay, agents: roster, selectedId: sel, size: box, letters: names } = live.current;
    if (!canvas || !ctx || !colours || box.width <= 0 || box.height <= 0) return;
    const w = box.width;
    const h = box.height;
    const ratio = canvas.width / w;
    ctx.setTransform(ratio, 0, 0, ratio, 0, 0);
    ctx.clearRect(0, 0, w, h);
    const heading = cameraView.ready ? yawToBearing(cameraView.yaw) : yawToBearing(player.heading);
    if (readoutRef.current) readoutRef.current.textContent = `${Math.round(heading) % 360}°`;

    ctx.textAlign = "center";
    ctx.textBaseline = "middle";
    for (const tick of compassTicks(heading, SPAN_DEG, 15)) {
      const x = Math.round(tick.at * w) + 0.5;
      if (tick.point) {
        const main = tick.point.length === 1;
        ctx.fillStyle = tick.point === "n" ? MAP_PAINT.checkpointDeep : colours.ink;
        ctx.font = `${main ? 800 : 700} ${main ? 14 : 11}px system-ui, -apple-system, "Segoe UI", sans-serif`;
        ctx.fillText(names[tick.point], x, h * 0.42);
        ctx.fillStyle = colours.tick;
        ctx.fillRect(x - 0.5, h - 7, 1, 5);
      } else {
        ctx.fillStyle = colours.tick;
        ctx.fillRect(x - 0.5, h - 6, 1, 4);
        ctx.fillStyle = colours.muted;
        ctx.font = `600 9px system-ui, -apple-system, "Segoe UI", sans-serif`;
        ctx.fillText(String(Math.round(tick.deg)), x, h * 0.42);
      }
    }

    // Checkpoint pips: small gold diamonds on the bottom edge.
    for (const cp of lay.checkpoints) {
      const at = compassPosition(bearingOf(cp.x - player.x, cp.z - player.z), heading, SPAN_DEG);
      if (at === null) continue;
      const x = at * w;
      ctx.beginPath();
      ctx.moveTo(x, h - 9);
      ctx.lineTo(x + 3.2, h - 5.5);
      ctx.lineTo(x, h - 2);
      ctx.lineTo(x - 3.2, h - 5.5);
      ctx.closePath();
      ctx.fillStyle = MAP_PAINT.checkpoint;
      ctx.fill();
    }

    // The selected agent: a pip in its colour, pinned to the nearer end when out of view.
    const agent = sel ? roster.get(sel) : undefined;
    const pos = sel ? agentPositions.get(sel) : undefined;
    if (agent && pos) {
      const bearing = bearingOf(pos.x - player.x, pos.z - player.z);
      const inView = compassPosition(bearing, heading, SPAN_DEG);
      const at = inView ?? (relativeBearing(bearing, heading) < 0 ? 0.02 : 0.98);
      const x = at * w;
      ctx.globalAlpha = inView === null ? 0.6 : 1;
      ctx.beginPath();
      ctx.arc(x, h - 6, 4.2, 0, Math.PI * 2);
      ctx.fillStyle = agentColour(agent);
      ctx.fill();
      ctx.strokeStyle = colours.rim;
      ctx.lineWidth = 1.5;
      ctx.stroke();
      ctx.globalAlpha = 1;
    }

    // Centre marker: a gold notch at the top.
    ctx.beginPath();
    ctx.moveTo(w / 2 - 5, 0);
    ctx.lineTo(w / 2 + 5, 0);
    ctx.lineTo(w / 2, 6);
    ctx.closePath();
    ctx.fillStyle = colours.marker;
    ctx.fill();
  }, []);

  // Size the backing store to the measured CSS size and pixel ratio.
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || size.width <= 0 || size.height <= 0) return;
    canvas.width = Math.round(size.width * dpr);
    canvas.height = Math.round(size.height * dpr);
    draw();
  }, [size, dpr, themeVersion, draw]);

  useThrottledFrames(draw, FRAME_MS, size.width > 0);

  return (
    <div ref={rootRef} className="office-hud office-compass" data-office-ui role="img"
      aria-label={t("society.office.compass_label")}>
      <canvas ref={canvasRef} className="office-compass-canvas" aria-hidden />
      <span ref={readoutRef} className="office-compass-readout" aria-hidden />
    </div>
  );
}
