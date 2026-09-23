import gigiCompanionMark from "@/assets/gigi-companion-mark.svg";
import { cn } from "@/lib/utils";

import { AgentSymbol, symbolAppearance } from "./AgentSymbol";
import type { SocietyAgent } from "./data";

type SwatchAgent = Pick<SocietyAgent, "figure" | "palette" | "name"> &
  Partial<Pick<SocietyAgent, "agentId" | "tier">>;

/** Lightweight vector identity shared by roster, profile and message surfaces. */
export function AgentSwatch({
  agent,
  size = 36,
  className,
}: {
  agent: SwatchAgent;
  size?: number;
  className?: string;
}) {
  // Older internal-message participants carry the figure but not the tier.
  const isJarvis = agent.tier === "lead" || (
    !agent.tier && agent.figure?.archetype === "spirit" && agent.figure.base === "gigi"
  );
  // Real roster identities survive renames. Name-only historical participants
  // still get a deterministic symbol without fetching a roster or a 3D model.
  const appearance = symbolAppearance(agent.agentId || agent.name);

  return (
    <span
      aria-hidden
      className={cn("relative inline-flex shrink-0 items-center justify-center select-none", className)}
      style={{ width: size, height: size }}
    >
      {isJarvis ? (
        <img
          data-agent-mascot="gigi"
          src={gigiCompanionMark}
          alt=""
          draggable={false}
          width={size}
          height={size}
          className="h-full w-full object-contain"
        />
      ) : (
        <AgentSymbol {...appearance} size={size} />
      )}
    </span>
  );
}
