/**
 * An agent's face wherever a 3D canvas would be wasteful — roster rows, card
 * headers, chips. A rendered headshot of the agent's own figure when the
 * look has a built base (faceCrop.ts); until it arrives, and for a row with
 * no figure, the flat stand-in below.
 *
 * Test revision (avatar readability): same size contract, distinct content.
 * The ground pairs the figure's primary with its secondary so two agents
 * never collapse to the same dark disc; the fallback draws a simple
 * two-eye face from the figure's own skin/hair/eyes cells; a loaded crop
 * is shown in full so the whole face stays visible inside the disc.
 */
import { useEffect, useState } from "react";

import { cn } from "@/lib/utils";

import type { SocietyAgent } from "./data";
import { faceCrop } from "./figures/faceCrop";
import { recipeKey, resolvePalette, type FigureRecipe } from "./figures/figureRecipe";

const crops = new Map<string, string>();

function useFaceCrop(figure: FigureRecipe | null): string | null {
  const key = figure ? recipeKey(figure) : "";
  const [url, setUrl] = useState<string | null>(() => (key ? (crops.get(key) ?? null) : null));
  useEffect(() => {
    if (!figure || !key) {
      setUrl(null);
      return;
    }
    const cached = crops.get(key);
    if (cached) {
      setUrl(cached);
      return;
    }
    let live = true;
    void faceCrop(figure).then((data) => {
      if (!live) return;
      if (data) crops.set(key, data);
      setUrl(data);
    });
    return () => {
      live = false;
    };
  }, [figure, key]);
  return url;
}

export function AgentSwatch({
  agent,
  size = 36,
  className,
}: {
  agent: Pick<SocietyAgent, "figure" | "palette" | "name">;
  size?: number;
  className?: string;
}) {
  const palette = resolvePalette(agent.figure);
  const primary = agent.figure ? palette.primary : agent.palette.primary;
  const secondary = agent.figure ? palette.secondary : agent.palette.secondary;
  const accent = agent.figure ? palette.accent : agent.palette.accent;
  const crop = useFaceCrop(agent.figure);
  const eyeGap = Math.max(3, size * 0.11);
  const eyeSize = Math.max(2.5, size * 0.085);
  return (
    <span
      aria-hidden
      className={cn(
        "relative inline-flex shrink-0 items-end justify-center overflow-hidden rounded-full border border-border",
        className,
      )}
      style={{
        width: size,
        height: size,
        background: `linear-gradient(170deg, ${primary} 0%, ${secondary} 135%)`,
        boxShadow: `inset 0 0 0 2px ${accent}66`,
      }}
    >
      {crop ? (
        <img
          src={crop}
          alt=""
          draggable={false}
          className="absolute inset-[4%] h-[92%] w-[92%] object-contain"
          style={{
            imageRendering: size <= 40 ? "auto" : "pixelated",
          }}
        />
      ) : (
        <span
          className="relative rounded-full"
          style={{
            width: size * 0.64,
            height: size * 0.64,
            marginBottom: size * 0.08,
            background: palette.skin,
            boxShadow: `0 ${-size * 0.16}px 0 0 ${palette.hair}, inset 0 0 0 ${Math.max(1, size * 0.03)}px rgba(0,0,0,0.08)`,
          }}
        >
          <span
            className="absolute left-1/2 top-1/2 flex -translate-x-1/2 -translate-y-1/2"
            style={{ gap: eyeGap, marginTop: size * 0.04 }}
          >
            <span className="rounded-full" style={{ width: eyeSize, height: eyeSize * 1.25, background: palette.eyes }} />
            <span className="rounded-full" style={{ width: eyeSize, height: eyeSize * 1.25, background: palette.eyes }} />
          </span>
        </span>
      )}
    </span>
  );
}
