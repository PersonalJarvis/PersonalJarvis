import { useState } from "react";
import { SquareTerminal } from "lucide-react";

import { cn } from "@/lib/utils";

const AGENT_LOGOS: Record<string, string> = {
  claude: "/provider-logos/claude.svg",
  codex: "/provider-logos/openai.svg",
  kimi: "/agent-logos/kimi.svg",
  opencode: "/agent-logos/opencode.svg",
};

const AGENT_MONOGRAMS: Record<string, string> = {
  glm: "GLM",
};

interface AgentMarkProps {
  agent: string;
  label: string;
  className?: string;
  size?: "sm" | "md" | "lg";
}

/**
 * A registry-safe visual identity for a terminal agent.
 *
 * Known products use local, offline brand assets. Unknown registry entries get
 * a neutral monogram, so adding another CLI never produces a broken image. The
 * plain terminal is a capability rather than a brand and therefore uses the
 * system-terminal glyph.
 */
export function AgentMark({
  agent,
  label,
  className,
  size = "md",
}: AgentMarkProps) {
  const [failed, setFailed] = useState(false);
  const logo = AGENT_LOGOS[agent];
  const monogram =
    AGENT_MONOGRAMS[agent] ?? (label.trim().slice(0, 2).toUpperCase() || "?");
  const sizeClass =
    size === "sm"
      ? "h-7 w-7 rounded-[5px]"
      : size === "lg"
        ? "h-11 w-11 rounded-control"
        : "h-9 w-9 rounded-control";

  return (
    <span
      data-testid={`agent-mark-${agent}`}
      aria-hidden="true"
      className={cn(
        "inline-flex shrink-0 items-center justify-center overflow-hidden border border-border/80 bg-background/80 text-[9px] font-bold tracking-tight text-muted-foreground",
        sizeClass,
        className,
      )}
    >
      {agent === "shell" ? (
        <SquareTerminal className={size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4"} />
      ) : logo && !failed ? (
        <img
          src={logo}
          alt=""
          className={cn(
            "block object-contain",
            size === "sm"
              ? "h-3.5 w-3.5"
              : size === "lg"
                ? "h-6 w-6"
                : "h-5 w-5",
          )}
          onError={() => setFailed(true)}
        />
      ) : (
        <span className="font-mono">{monogram}</span>
      )}
    </span>
  );
}
