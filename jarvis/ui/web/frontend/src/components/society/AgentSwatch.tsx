/**
 * An agent's face wherever a 3D canvas would be wasteful — roster rows, card
 * headers, chips. A rendered headshot of the agent's own figure when the
 * look has a built base (faceCrop.ts); until it arrives, and for a row with
 * no figure, the flat stand-in: skin over the primary garment with the
 * accent as a ring, the same colours the figure wears.
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
  const accent = agent.figure ? palette.accent : agent.palette.accent;
  const crop = useFaceCrop(agent.figure);
  return (
    <span
      aria-hidden
      className={cn("relative inline-flex shrink-0 items-end justify-center overflow-hidden rounded-full", className)}
      style={{
        width: size,
        height: size,
        background: `linear-gradient(170deg, ${primary}, ${palette.primary_shade})`,
        boxShadow: `inset 0 0 0 2px ${accent}55`,
      }}
    >
      {crop ? (
        <img
          src={crop}
          alt=""
          draggable={false}
          className="absolute inset-0 h-full w-full object-cover"
          style={{ imageRendering: size <= 40 ? "auto" : "pixelated" }}
        />
      ) : (
        <span
          className="rounded-full"
          style={{
            width: size * 0.42,
            height: size * 0.42,
            marginBottom: size * 0.12,
            background: palette.skin,
            boxShadow: `0 ${-size * 0.12}px 0 0 ${palette.hair}`,
          }}
        />
      )}
    </span>
  );
}
