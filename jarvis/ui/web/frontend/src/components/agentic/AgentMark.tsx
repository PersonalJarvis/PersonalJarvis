import { useState } from "react";
import { SquareTerminal } from "lucide-react";

import { cn } from "@/lib/utils";

const AGENT_LOGOS: Record<string, string> = {
  claude: "/provider-logos/claude.svg",
  codex: "/provider-logos/openai.svg",
  glm: "/agent-logos/zai.svg",
  kimi: "/agent-logos/kimi.svg",
  opencode: "/agent-logos/opencode.svg",
};

interface AgentMarkProps {
  agent: string;
  label: string;
  className?: string;
  size?: "sm" | "md" | "lg";
  /**
   * An image to draw instead of the built-in table's — the mark a user uploaded
   * for a CLI of their own.
   *
   * Passed in rather than looked up, because this component cannot know it: the
   * table below is a fixed list of brands that ship with the app, and an entry
   * added at runtime has no place in it. An entry with no logo (and every
   * shipped one) leaves this undefined and the table decides as before.
   */
  logoUrl?: string;
  /**
   * How much of a mark this is.
   *
   * `boxed` is the identity badge: a framed tile, for the places that are ABOUT
   * which agent this is — a pane header, an agent picker. `plain` is the same
   * glyph with the tile taken away, for a list of conversations, where the mark
   * is a hint beside a title rather than the subject of the row. A framed tile
   * repeated down forty rows turns a reading list into a grid of boxes.
   */
  variant?: "boxed" | "plain";
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
  variant = "boxed",
  logoUrl,
}: AgentMarkProps) {
  /*
   * WHICH logo failed, not merely "one did".
   *
   * A boolean would stick: a user who replaces a broken upload with a working
   * one keeps seeing the monogram, because the component is the same instance
   * and its flag never clears. Storing the URL that failed makes the next one a
   * fresh attempt without an effect to keep in step.
   */
  const [failedLogo, setFailedLogo] = useState<string | null>(null);
  const logo = logoUrl || AGENT_LOGOS[agent];
  const failed = failedLogo != null && failedLogo === logo;
  const monogram = label.trim().slice(0, 2).toUpperCase() || "?";
  const plain = variant === "plain";
  const sizeClass = plain
    ? size === "sm"
      ? "h-4 w-4"
      : "h-5 w-5"
    : size === "sm"
      ? "h-7 w-7 rounded-[5px]"
      : size === "lg"
        ? "h-11 w-11 rounded-control"
        : "h-9 w-9 rounded-control";

  return (
    <span
      data-testid={`agent-mark-${agent}`}
      aria-hidden="true"
      className={cn(
        "inline-flex shrink-0 items-center justify-center overflow-hidden text-[9px] font-bold tracking-tight text-muted-foreground",
        // The tile, and the brightness that goes with being one. Without it the
        // glyph sits at the weight of the text it accompanies, which is the
        // point of `plain`.
        plain ? "opacity-70" : "border border-border/80 bg-background/80",
        sizeClass,
        className,
      )}
    >
      {agent === "shell" ? (
        <SquareTerminal
          className={plain || size === "sm" ? "h-3.5 w-3.5" : "h-4 w-4"}
        />
      ) : logo && !failed ? (
        <img
          src={logo}
          alt=""
          className={cn(
            "block object-contain",
            // Plain fills its box: there is no tile to sit inside, so the glyph
            // IS the mark and shrinking it further would leave a speck.
            plain
              ? "h-full w-full"
              : size === "sm"
                ? "h-3.5 w-3.5"
                : size === "lg"
                  ? "h-6 w-6"
                  : "h-5 w-5",
          )}
          onError={() => setFailedLogo(logo)}
        />
      ) : (
        <span className="font-mono">{monogram}</span>
      )}
    </span>
  );
}
