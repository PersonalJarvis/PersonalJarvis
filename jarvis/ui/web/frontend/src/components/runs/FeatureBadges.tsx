import type { LucideIcon } from "lucide-react";
import { Bot, Monitor, Sparkles, Terminal } from "lucide-react";

import { agentBrand } from "@/lib/agentBrand";
import { useEventStore } from "@/store/events";

/**
 * "Which agents / tools / CLIs ran" — Computer-Use, the agent system and Skill
 * are called out by name and by glyph; everything else (CLI/tool names) gets a
 * monospace chip.
 *
 * These used to be painted sky / violet / fuchsia, which spent hue on three
 * things that are neither a status nor an identity — and made a run that
 * happened to touch a skill louder than one that failed. The glyph is what
 * distinguishes them now; the surface is the same neutral lift every chip in
 * the product rests on. The sub_agent label is still resolved per render: it
 * carries the wake-word-derived assistant name ("Ruben" -> "Ruben-Agent").
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
  // 11px is the type floor, so the two sizes differ in padding only.
  const pad = size === "xs" ? "px-1.5 py-px" : "px-2 py-0.5";
  const icon = size === "xs" ? "h-3 w-3" : "h-3.5 w-3.5";
  return (
    <div className="flex flex-wrap items-center gap-1" data-testid="feature-badges">
      {shown.map((t) => {
        const m = AGENT_META[t];
        if (m) {
          const { Icon } = m;
          return (
            <span
              key={t}
              data-feature={t}
              className={`inline-flex items-center gap-1 rounded-full bg-secondary text-micro font-medium text-foreground ${pad}`}
            >
              <Icon className={icon} strokeWidth={2.25} />
              {m.label ?? agentBrand(assistantName)}
            </span>
          );
        }
        return (
          <span
            key={t}
            data-feature={t}
            className={`inline-flex items-center gap-1 rounded-full bg-secondary font-mono text-micro text-muted-foreground ${pad}`}
          >
            <Terminal className={icon} strokeWidth={2} />
            {t}
          </span>
        );
      })}
      {rest > 0 && <span className="text-micro text-muted-foreground">+{rest}</span>}
    </div>
  );
}
