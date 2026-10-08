import { GigiAvatar } from "./GigiAvatar";
import { cn } from "@/lib/utils";

import { AgentSymbol, SymbolThinkingDots } from "./AgentSymbol";
import { resolveCompanion } from "./companion/appearance";
import type { SocietyAgent } from "./data";

type SwatchAgent = Pick<SocietyAgent, "figure" | "palette" | "name"> &
  Partial<Pick<SocietyAgent, "agentId" | "tier" | "state">>;

/** The gold of the lead's mark (GigiAvatar's lens rim). */
export const LEAD_COLOR = "#ffcd61";

/**
 * The one colour a person knows an agent by: the lead's gold, or the
 * companion colour its symbol wears everywhere else.
 */
export function agentColor(agent: SwatchAgent): string {
  if (isLead(agent)) return LEAD_COLOR;
  return resolveCompanion(agent.agentId || agent.name, agent.figure?.companion).color;
}

function isLead(agent: SwatchAgent): boolean {
  // Older internal-message participants carry the figure but not the tier.
  return agent.tier === "lead" || (
    !agent.tier && agent.figure?.archetype === "spirit" && agent.figure.base === "gigi"
  );
}

/** Lightweight vector identity shared by roster, profile and message surfaces. */
export function AgentSwatch({
  agent,
  size = 36,
  className,
  expressive = false,
}: {
  agent: SwatchAgent;
  size?: number;
  className?: string;
  /** Eyes a stage can animate between expressions (look, squint, wink, smile). */
  expressive?: boolean;
}) {
  const isJarvis = isLead(agent);
  // Real roster identities survive renames. Name-only historical participants
  // still get a deterministic symbol without fetching a roster or a 3D model.
  const appearance = resolveCompanion(agent.agentId || agent.name, agent.figure?.companion);
  const thinking = agent.state === "working";

  return (
    <span
      aria-hidden
      className={cn("relative inline-flex shrink-0 items-center justify-center select-none", isJarvis && "society-agent-symbol", className)}
      data-thinking={isJarvis && thinking ? "true" : undefined}
      style={{ width: size, height: size }}
    >
      {isJarvis ? (
        <>
        <GigiAvatar size={size} />
        {thinking && <svg aria-hidden viewBox="0 0 40 44" className="pointer-events-none absolute inset-0 h-full w-full"><SymbolThinkingDots color="#ffcf45" /></svg>}
        </>
      ) : (
        <AgentSymbol {...appearance} size={size} thinking={thinking} expressive={expressive} />
      )}
    </span>
  );
}
