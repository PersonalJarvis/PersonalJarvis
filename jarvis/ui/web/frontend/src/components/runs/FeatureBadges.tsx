import type { LucideIcon } from "lucide-react";
import { Bot, Monitor, Sparkles, Terminal } from "lucide-react";

// Specific agent/feature markers get a named, icon-led badge; everything else
// (CLI/tool names) gets a neutral monospace chip. This is the "which agents /
// tools / CLIs ran" overview — Computer-Use, Sub-Agent and Skill are called out
// by name.
const AGENT_META: Record<string, { label: string; Icon: LucideIcon; cls: string }> = {
  computer_use: {
    label: "Computer-Use",
    Icon: Monitor,
    cls: "bg-sky-400/10 text-sky-300 ring-sky-400/25",
  },
  sub_agent: {
    label: "Sub-Agent",
    Icon: Bot,
    cls: "bg-violet-400/10 text-violet-300 ring-violet-400/25",
  },
  skill: {
    label: "Skill",
    Icon: Sparkles,
    cls: "bg-fuchsia-400/10 text-fuchsia-300 ring-fuchsia-400/25",
  },
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
  if (!tags.length) return null;
  const shown = max ? tags.slice(0, max) : tags;
  const rest = tags.length - shown.length;
  const pad = size === "xs" ? "px-1.5 py-px text-[10px]" : "px-2 py-0.5 text-[11px]";
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
              className={`inline-flex items-center gap-1 rounded-full font-medium ring-1 ring-inset ${pad} ${m.cls}`}
            >
              <Icon className={icon} strokeWidth={2.25} />
              {m.label}
            </span>
          );
        }
        return (
          <span
            key={t}
            data-feature={t}
            className={`inline-flex items-center gap-1 rounded-full bg-muted/40 font-mono text-muted-foreground ring-1 ring-inset ring-border/70 ${pad}`}
          >
            <Terminal className={icon} strokeWidth={2} />
            {t}
          </span>
        );
      })}
      {rest > 0 && (
        <span className="text-[10px] text-muted-foreground">+{rest}</span>
      )}
    </div>
  );
}
