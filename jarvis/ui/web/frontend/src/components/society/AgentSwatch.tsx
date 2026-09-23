import gigiCompanionMark from "@/assets/gigi-companion-avatar.png";
import { cn } from "@/lib/utils";

import { AgentSymbol } from "./AgentSymbol";
import { resolveCompanion } from "./companion/appearance";
import type { SocietyAgent } from "./data";

type SwatchAgent = Pick<SocietyAgent, "figure" | "palette" | "name"> &
  Partial<Pick<SocietyAgent, "agentId" | "tier" | "state">>;

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
        <img
          data-agent-mascot="gigi"
          src={gigiCompanionMark}
          alt=""
          draggable={false}
          width={size}
          height={size}
          className="agent-symbol-character h-full w-full object-contain"
        />
      ) : (
        <AgentSymbol {...appearance} size={size} thinking={thinking} />
      )}
    </span>
  );
}
