import type { LucideIcon } from "lucide-react";
import { Bot, Monitor, Sparkles, Terminal } from "lucide-react";

import { Badge } from "@/components/ui/badge";
import { agentBrand } from "@/lib/agentBrand";
import { cn } from "@/lib/utils";
import { useEventStore } from "@/store/events";

/**
 * "Which agents / tools / CLIs ran" — Computer-Use, the agent system and Skill
 * are called out by name and by glyph; everything else (CLI/tool names) keeps
 * its monospace spelling because it IS an identifier.
 *
 * These are neither a status nor an identity, so they carry no hue: the glyph
 * is what distinguishes them and the surface is the one neutral badge every
 * label in the product rests on. The sub_agent label is still resolved per
 * render — it carries the wake-word-derived assistant name ("Ruben" ->
 * "Ruben-Agent").
 */
const AGENT_META: Record<string, { label: string | null; Icon: LucideIcon }> = {
  computer_use: { label: "Computer-Use", Icon: Monitor },
  // Dynamic: agentBrand(assistantName).
  sub_agent: { label: null, Icon: Bot },
  skill: { label: "Skill", Icon: Sparkles },
};

export function FeatureBadges({
  tags,
  max,
  size = "sm",
}: {
  tags: string[];
  max?: number;
  size?: "sm" | "xs";
}) {
  const assistantName = useEventStore((s) => s.assistantName);
  if (!tags.length) return null;
  const shown = max ? tags.slice(0, max) : tags;
  const rest = tags.length - shown.length;
  // 12 px is the type floor, so the two sizes differ in height and padding.
  const compact = size === "xs" ? "h-5 px-1.5" : undefined;
  return (
    <div className="flex flex-wrap items-center gap-1" data-testid="feature-badges">
      {shown.map((tag) => {
        const meta = AGENT_META[tag];
        const Icon = meta ? meta.Icon : Terminal;
        return (
          <Badge
            key={tag}
            variant="secondary"
            data-feature={tag}
            className={cn(meta ? "text-foreground" : "font-mono", compact)}
          >
            <Icon aria-hidden />
            {meta ? (meta.label ?? agentBrand(assistantName)) : tag}
          </Badge>
        );
      })}
      {rest > 0 && (
        <span className="text-xs tabular-nums text-muted-foreground">+{rest}</span>
      )}
    </div>
  );
}
