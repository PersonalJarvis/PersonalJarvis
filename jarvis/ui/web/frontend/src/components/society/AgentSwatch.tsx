/**
 * The flat stand-in for an agent's face wherever a 3D canvas would be
 * wasteful — roster rows, card headers, chips. Skin over the primary
 * garment, the accent as a ring: the same colours the figure wears, so a
 * row and its character read as one agent.
 */
import { cn } from "@/lib/utils";

import type { SocietyAgent } from "./data";
import { resolvePalette } from "./figures/figureRecipe";

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
    </span>
  );
}
